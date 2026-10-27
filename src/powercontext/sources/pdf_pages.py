# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Parse whole financial-report PDFs into page-level evidence Sources."""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Self
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

if TYPE_CHECKING:
    from powercontext.artifacts import MemoryCitation

PDF_PAGE_SOURCE_NAME = "pdf-page"

NonEmptyText = Annotated[str, Field(min_length=1)]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WORD = re.compile(r"[a-z0-9]+")
_NUMERIC_WORD = re.compile(r"^\d+$")
_FISCAL_YEAR = re.compile(r"^fy(\d{2}|\d{4})$")
_SUBJECT_HINT = re.compile(r"\b(?:[A-Z][a-z]+[A-Z][A-Za-z]*|[A-Z]{3,})\b")
_MAX_SUBJECT_HINTS = 4
_SUBJECT_BONUS = 1.2
# A question term that is also a substring of the candidate page's own document id
# (the first content line) pins the page to the company the question names — this
# counteracts cross-document flooding when every report carries identical statement
# boilerplate.
_DOC_ANCHOR_BONUS = 3.0
# Exhibit-only appendices (pension plans, indentures, certifications) repeat question words
# far more often than the report body does, so prefer pages carrying the report's own
# running header when the file has one.
_BODY_PAGE_BONUS = 1.3
_BODY_PAGE_MARKER = "table of contents"

# Lexical query planning. The Memory admission floor requires a large share of a long
# question's terms to match, so retrieval runs short, high-precision lexical probes and
# then re-ranks their union against the question itself.
_STOPWORDS = frozenset({
    "a",
    "about",
    "after",
    "all",
    "also",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "between",
    "both",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "each",
    "explicitly",
    "for",
    "from",
    "give",
    "had",
    "has",
    "have",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "may",
    "more",
    "most",
    "must",
    "no",
    "not",
    "of",
    "on",
    "or",
    "other",
    "our",
    "out",
    "over",
    "please",
    "provide",
    "provided",
    "relying",
    "respond",
    "response",
    "same",
    "shall",
    "should",
    "shown",
    "so",
    "some",
    "state",
    "such",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "to",
    "unadjusted",
    "under",
    "up",
    "us",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
})

# Analyst phrasing versus report phrasing: each trigger widens the lexical probes so a
# question about "legal battles" also reaches the "Legal Proceedings" section, and a
# question about EBITDA also reaches the consolidated income statement.
_FINANCE_PROBES: tuple[tuple[frozenset[str], tuple[str, ...]], ...] = (
    (
        frozenset({
            "battle",
            "battles",
            "contingencies",
            "contingency",
            "lawsuit",
            "lawsuit",
            "legal",
            "litigation",
            "proceeding",
            "proceedings",
            "settlement",
            "settlements",
        }),
        ("legal proceedings", "legal matters", "contingencies"),
    ),
    (
        frozenset({
            "earnings",
            "ebit",
            "ebitda",
            "income",
            "margin",
            "operating",
            "profit",
            "profitability",
        }),
        (
            "consolidated statements of income",
            "consolidated statement of income",
            "consolidated statements of operations",
            "consolidated statement of operations",
            "consolidated statements of earnings",
            "consolidated statement of earnings",
            "statements of operations",
            "statement of operations",
            "statements of income",
            "statement of income",
            "statements of earnings",
            "statement of earnings",
            "operating profit",
        ),
    ),
    (
        frozenset({"capex", "cash", "flow", "flows", "liquidity", "working"}),
        (
            "consolidated statements of cash flows",
            "consolidated statement of cash flows",
            "consolidated statements of cash flow",
            "consolidated statement of cash flow",
            "statements of cash flows",
            "statement of cash flows",
            "statements of cash flow",
            "statement of cash flow",
            "capital expenditures",
        ),
    ),
    (
        frozenset({"amortization", "depreciation", "ebitda"}),
        ("depreciation and amortization",),
    ),
    (
        frozenset({"charges", "impairment", "productivity", "restructuring", "severance"}),
        ("restructuring charges", "productivity plan"),
    ),
    (
        frozenset({
            "geographic",
            "geographies",
            "geography",
            "operate",
            "operates",
            "region",
            "regions",
            "segment",
            "segments",
        }),
        ("segment information", "geographic", "north america", "latin america", "business segment"),
    ),
    (
        frozenset({"asset", "assets", "borrowings", "debt", "leverage", "liabilities", "liability"}),
        (
            "consolidated balance sheets",
            "consolidated balance sheet",
            "consolidated statements of financial position",
            "consolidated statement of financial position",
            "statements of financial position",
            "statement of financial position",
            "balance sheets",
            "balance sheet",
            "long-term debt",
        ),
    ),
    (
        frozenset({"capital", "intensive"}),
        (
            "capital expenditures",
            "property plant and equipment",
            "purchases of property",
            "consolidated statement of income",
            "consolidated balance sheet",
            "consolidated statements of cash flows",
        ),
    ),
    (
        frozenset({"liquidity", "quick", "ratio", "solvency"}),
        (
            "consolidated balance sheets",
            "consolidated balance sheet",
            "balance sheets",
            "balance sheet",
            "current assets",
            "current liabilities",
        ),
    ),
    (
        frozenset({"customer", "customers"}),
        ("major customers", "customer concentration", "u.s. government", "government contracts"),
    ),
    (
        frozenset({"cogs"}),
        ("cost of sales", "cost of goods sold", "cost of revenue"),
    ),
    (
        frozenset({"categories", "category"}),
        ("business segment data", "summary of business segment"),
    ),
    (
        frozenset({"dividend", "dividends", "buyback", "repurchase", "shareholder", "shareholders"}),
        ("share repurchase", "dividends"),
    ),
    (
        frozenset({"revenue", "sales"}),
        ("net revenue",),
    ),
    (
        frozenset({"effective", "tax", "taxes"}),
        ("effective tax rate", "provision for income taxes", "income tax expense", "before income taxes"),
    ),
    (
        frozenset({"pension", "retirement"}),
        ("pension", "retirement"),
    ),
)
_RAW_PROBES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("d&a", ("depreciation and amortization",)),
    ("p&l", ("consolidated statement of income", "consolidated statements of operations")),
)

_VARIANT_LIMIT = 28
_WINDOW_SIZES = (3, 2)
_MAX_WINDOW_VARIANTS = 6
_MAX_TERM_VARIANTS = 3
_MAX_PROBES = 10
_RRF_OFFSET = 10
_WHOLE_QUESTION_WEIGHT = 1.2
_CONTENT_QUESTION_WEIGHT = 1.5
_PROBE_WEIGHT = 2.5
_WINDOW_WEIGHT = 0.9
_TERM_WEIGHT = 0.5
_RRF_SHARE = 0.65
_COVERAGE_SHARE = 0.35


class PDFDocumentInput(BaseModel):
    """One complete original PDF document supplied by the caller."""

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must not be blank")  # noqa: TRY003
        return value


class PDFPageInput(BaseModel):
    """One physical page of a PDF document with its extractable text."""

    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must not be blank")  # noqa: TRY003
        return value

    @field_validator("document_sha256")
    @classmethod
    def validate_document_sha256(cls, value: str) -> str:
        if not _SHA256.match(value):
            raise ValueError("document_sha256 must be a lowercase hexadecimal SHA-256 digest")  # noqa: TRY003
        return value


class PDFPageSource(Source):
    """A captured PDF page bound to its document revision and physical page number."""

    source_id: NonEmptyText
    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if self.source_id != self.name:
            raise ValueError("PDF page Source identity must equal its name")  # noqa: TRY003
        return self


class PDFPageSourceAdapter:
    """Resolve and read page-level PDF Sources."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one PDF page as retrievable, exactly citable text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build the stable identity of one physical page of one document revision."""

    if not document_id.strip():
        raise ValueError("document_id must not be blank")  # noqa: TRY003
    if page_number < 1:
        raise ValueError("page_number must be a positive physical page number")  # noqa: TRY003
    identity = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        # A bounded digest keeps long document ids stable and collision-free.
        identity = f"pdf:{_digest(document_id)}:{document_sha256}:{page_number}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"PDF page identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every physical page of one complete original PDF document."""

    # The declared contract rejects a blank document id at parse time, before any
    # page identity is built, so callers see one atomic ValueError instead of a
    # tuple of pages that only fail later during registry resolution.
    document_id = getattr(value, "document_id", None)
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must not be blank")  # noqa: TRY003
    document_sha256 = hashlib.sha256(value.content).hexdigest()
    texts = _extract_page_texts(value.content)
    return tuple(
        PDFPageInput(
            document_id=value.document_id,
            document_sha256=document_sha256,
            page_number=page_number,
            text=text,
        )
        for page_number, text in enumerate(texts, start=1)
    )


async def search_pdf_pages(
    memory: object,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Return at most ``limit`` distinct exact page citations for one question."""

    from powercontext.builtin.artifacts.memory.models import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    if limit < 1:
        return ()
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-blank string")  # noqa: TRY003
    terms = _question_terms(question)
    window = max(limit * 3, 32)
    candidates: dict[str, _Candidate] = {}
    for variant, specificity in _query_variants(question, terms):
        page = await memory.search(  # type: ignore[attr-defined]
            SearchMemoryRequest(query=variant, mode="fts", limit=window)
        )
        for rank, hit in enumerate(page.hits):
            contribution = specificity / (_RRF_OFFSET + rank)
            candidate = candidates.get(hit.entry_id)
            if candidate is None:
                candidates[hit.entry_id] = _Candidate(
                    citation=MemoryCitation(
                        memory_ref=hit.memory_ref,
                        entry_id=hit.entry_id,
                        entry_version_id=hit.entry_version_id,
                    ),
                    text=hit.text,
                    rrf=contribution,
                )
            else:
                candidate.rrf += contribution
    if not candidates:
        return ()
    ordered = _rank_candidates(terms, _subject_hints(question), candidates, probes=_finance_probes(question, terms))
    citations: list[MemoryCitation] = []
    seen_pages: set[str] = set()
    for entry_id in ordered:
        candidate = candidates[entry_id]
        record = await memory.get(GetMemoryEntryRequest(citation=candidate.citation))  # type: ignore[attr-defined]
        pages = {
            reference.source_id
            for reference in record.entry.sources
            if reference.source_type == PDF_PAGE_SOURCE_NAME
        }
        if len(pages) != 1:
            continue
        page_key = pages.pop()
        if page_key in seen_pages:
            continue
        seen_pages.add(page_key)
        citations.append(candidate.citation)
        if len(citations) >= limit:
            break
    if len(citations) < limit:
        citations = await _top_up(memory, terms, candidates, seen_pages, citations, limit)
    return tuple(citations)


async def _top_up(
    memory: object,
    terms: tuple[str, ...],
    candidates: dict[str, _Candidate],
    seen_pages: set[str],
    citations: list[MemoryCitation],
    limit: int,
) -> list[MemoryCitation]:
    """Widen recall with broad single-term probes when ranking under-fills ``limit``."""

    from powercontext.builtin.artifacts.memory.models import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    for term in terms:
        if len(citations) >= limit:
            break
        if _term_weight(term) < 1.2:
            continue
        page = await memory.search(  # type: ignore[attr-defined]
            SearchMemoryRequest(query=term, mode="fts", limit=max(limit * 4, 48))
        )
        for hit in page.hits:
            if len(citations) >= limit:
                break
            if hit.entry_id in candidates:
                candidate = candidates[hit.entry_id]
            else:
                candidate = _Candidate(
                    citation=MemoryCitation(
                        memory_ref=hit.memory_ref,
                        entry_id=hit.entry_id,
                        entry_version_id=hit.entry_version_id,
                    ),
                    text=hit.text,
                )
                candidates[hit.entry_id] = candidate
            record = await memory.get(GetMemoryEntryRequest(citation=candidate.citation))  # type: ignore[attr-defined]
            pages = {
                reference.source_id
                for reference in record.entry.sources
                if reference.source_type == PDF_PAGE_SOURCE_NAME
            }
            if len(pages) != 1:
                continue
            page_key = pages.pop()
            if page_key in seen_pages:
                continue
            seen_pages.add(page_key)
            citations.append(candidate.citation)
    return citations


@dataclass(slots=True)
class _Candidate:
    """One admission-filtered Memory entry competing for the final citation list."""

    citation: MemoryCitation
    text: str
    rrf: float = 0.0
    tokens: Counter[str] = field(default_factory=Counter)


def _extract_page_texts(content: bytes) -> tuple[str, ...]:
    try:
        from pypdf import PdfReader
    except ImportError as error:  # pragma: no cover - the shared environment pins pypdf
        raise ValueError("PDF parsing requires the pinned pypdf package") from error
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as error:
        raise ValueError(f"PDF document could not be parsed: {error}") from error
    try:
        if reader.is_encrypted:
            _decrypt_reader(reader)
        page_count = len(reader.pages)
        # A document that declares pages but yields none did not parse completely:
        # return no partial result instead of an empty page tuple.
        if page_count < 1:
            raise ValueError("PDF document contains no physical pages")  # noqa: TRY003
        texts: list[str] = []
        for index in range(page_count):
            text = reader.pages[index].extract_text()
            texts.append(text if isinstance(text, str) else "")
        if len(texts) != page_count:
            raise ValueError("PDF document did not parse completely")  # noqa: TRY003
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"PDF document could not be parsed completely: {error}") from error
    return tuple(texts)


def _decrypt_reader(reader: object) -> None:
    result = reader.decrypt("")  # type: ignore[attr-defined]
    if not int(result):
        raise ValueError("PDF document requires a user password that was not supplied")  # noqa: TRY003


def _question_terms(question: str) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in _WORD.findall(question.casefold()):
        token = _normalize_token(token)
        if not token or token in _STOPWORDS or token in seen:
            continue
        if len(token) == 1 and not token.isdigit():
            continue
        seen.add(token)
        terms.append(token)
    return tuple(terms)


def _normalize_token(token: str) -> str:
    fiscal = _FISCAL_YEAR.match(token)
    if fiscal is None:
        return token
    digits = fiscal.group(1)
    return digits if len(digits) == 4 else f"20{digits}"


def _query_variants(question: str, terms: tuple[str, ...]) -> tuple[tuple[str, float], ...]:
    variants: list[tuple[str, float]] = []
    seen: set[str] = set()

    def add(query: str, specificity: float) -> None:
        key = " ".join(query.split()).casefold()
        if not key or key in seen or len(variants) >= _VARIANT_LIMIT:
            return
        seen.add(key)
        variants.append((query, specificity))

    add(question.strip()[:2000], _WHOLE_QUESTION_WEIGHT)
    if not terms:
        return tuple(variants)
    add(" ".join(terms), _CONTENT_QUESTION_WEIGHT)
    for probe in _finance_probes(question, terms):
        add(probe, _PROBE_WEIGHT)
    for query in _window_queries(terms):
        add(query, _WINDOW_WEIGHT)
    for term in _distinctive_terms(terms):
        add(term, _TERM_WEIGHT)
    return tuple(variants)


def _finance_probes(question: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    term_set = set(terms)
    probes: list[str] = []
    seen: set[str] = set()
    folded = question.casefold()
    for marker, queries in _RAW_PROBES:
        if marker in folded:
            for query in queries:
                if query not in seen:
                    seen.add(query)
                    probes.append(query)
    for triggers, queries in _FINANCE_PROBES:
        if not term_set.intersection(triggers):
            continue
        for query in queries:
            if query not in seen:
                seen.add(query)
                probes.append(query)
        if len(probes) >= _MAX_PROBES:
            break
    return tuple(probes[:_MAX_PROBES])


def _window_queries(terms: tuple[str, ...]) -> tuple[str, ...]:
    scored: list[tuple[float, int, str]] = []
    seen: set[str] = set()
    for size in _WINDOW_SIZES:
        if len(terms) < size:
            continue
        for start in range(len(terms) - size + 1):
            window = terms[start : start + size]
            query = " ".join(window)
            if query in seen:
                continue
            seen.add(query)
            scored.append((-sum(_term_weight(term) for term in window), start, query))
    scored.sort()
    return tuple(query for _score, _start, query in scored[:_MAX_WINDOW_VARIANTS])


def _distinctive_terms(terms: tuple[str, ...]) -> tuple[str, ...]:
    scored = sorted(
        (-_term_weight(term), position, term) for position, term in enumerate(terms)
    )
    selected: list[str] = []
    for _score, _position, term in scored:
        if _term_weight(term) < 1.4:
            continue
        selected.append(term)
        if len(selected) >= _MAX_TERM_VARIANTS:
            break
    return tuple(selected)


def _term_weight(term: str) -> float:
    if _NUMERIC_WORD.match(term):
        return 3.0 if len(term) >= 4 else 1.2
    if len(term) >= 10:
        return 1.8
    if len(term) >= 7:
        return 1.4
    return 1.0


def _subject_hints(question: str) -> tuple[str, ...]:
    """Return the proper-noun-ish subject terms named by a question, if any."""

    hints: list[str] = []
    seen: set[str] = set()
    for match in _SUBJECT_HINT.finditer(question):
        term = match.group(0).casefold()
        if term in _STOPWORDS or term in seen or len(term) < 3:
            continue
        seen.add(term)
        hints.append(term)
        if len(hints) >= _MAX_SUBJECT_HINTS:
            break
    return tuple(hints)


def _stem(token: str) -> str:
    """Light plural folding used only for phrase-adjacency bonuses."""

    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _forms(term: str) -> frozenset[str]:
    """Morphological variants of one question term for page-token matching.

    Financial pages pluralize or conjugate question words (``revenue`` versus
    ``Revenues``, ``category`` versus ``categories``), so exact-token matching alone
    undercounts pages that carry the answer.
    """

    forms = {term}
    if len(term) >= 3:
        forms.add(term + "s")
        forms.add(term + "es")
        if term.endswith("s"):
            forms.add(term[:-1])
        if term.endswith("es"):
            forms.add(term[:-2])
        if term.endswith("ies"):
            forms.add(term[:-3] + "y")
    return frozenset(forms)


def _rank_candidates(
    terms: tuple[str, ...],
    hints: tuple[str, ...],
    candidates: dict[str, _Candidate],
    probes: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if not terms:
        return tuple(
            entry_id
            for entry_id, _candidate in sorted(candidates.items(), key=lambda item: (-item[1].rrf, item[0]))
        )
    term_set = set(terms)
    form_to_term: dict[str, str] = {}
    for term in terms:
        for variant in _forms(term):
            form_to_term.setdefault(variant, term)
    pool_size = len(candidates)
    lengths: dict[str, int] = {}
    all_tokens: dict[str, list[str]] = {}
    for entry_id, candidate in candidates.items():
        tokens = _WORD.findall(candidate.text.casefold())
        all_tokens[entry_id] = tokens
        matched: Counter[str] = Counter()
        for token in tokens:
            canonical = form_to_term.get(token)
            if canonical is not None:
                matched[canonical] += 1
        candidate.tokens = matched
        lengths[entry_id] = len(tokens)
    # Company anchor: a question term that is a substring of a candidate's document id
    # (the first content line) names the company the question asks about. Restrict the
    # ranking to those documents so identical statement boilerplate in other reports
    # cannot flood the page list.
    anchor_terms = tuple(term for term in terms if len(term) >= 2 and not term.isdigit())
    if anchor_terms:
        anchored_docs = {
            candidate.text.split("\n", 1)[0].casefold()
            for candidate in candidates.values()
            if any(term in candidate.text.split("\n", 1)[0].casefold() for term in anchor_terms)
        }
        if anchored_docs:
            candidates = {
                entry_id: candidate
                for entry_id, candidate in candidates.items()
                if candidate.text.split("\n", 1)[0].casefold() in anchored_docs
            }
            pool_size = len(candidates)
            if not candidates:
                return ()
    # Pool-relative inverse document frequency: terms carried by few candidate pages
    # ("restructuring", "concentration") discriminate; ubiquitous terms ("2022") do not.
    doc_freq: Counter[str] = Counter()
    for candidate in candidates.values():
        for term in candidate.tokens:
            doc_freq[term] += 1
    idf = {
        term: math.log(1.0 + (pool_size - doc_freq.get(term, 0) + 0.5) / (doc_freq.get(term, 0) + 0.5))
        for term in terms
    }
    total_idf = sum(idf.values()) or 1.0
    weights = {term: _term_weight(term) for term in terms}
    total_weight = sum(weights.values()) or 1.0
    avg_len = (sum(lengths[entry_id] for entry_id in candidates) / pool_size) if pool_size else 1.0
    best_rrf = max(candidate.rrf for candidate in candidates.values()) or 1.0
    pairs = tuple(zip(terms, terms[1:]))
    probe_tokens = tuple(
        tuple(_stem(token) for token in _WORD.findall(probe.casefold()))
        for probe in probes
    )
    ranked: list[tuple[float, str]] = []
    for entry_id, candidate in candidates.items():
        length = lengths[entry_id] or 1
        bm25 = 0.0
        for term, tf in candidate.tokens.items():
            saturation = (tf * 2.2) / (tf + 1.2 * (1.0 - 0.75 + 0.75 * length / avg_len))
            bm25 += idf[term] * saturation
        coverage = sum(weights[term] for term in candidate.tokens) / total_weight
        score = 0.55 * (bm25 / total_idf) + 0.45 * coverage
        stemmed = tuple(_stem(token) for token in all_tokens[entry_id])
        padded = f" {' '.join(stemmed)} "
        phrase = 0.0
        if pairs:
            phrase = sum(
                0.5 * (idf[left] + idf[right])
                for left, right in pairs
                if f" {_stem(left)} {_stem(right)} " in padded
            )
            score += 0.6 * (phrase / total_idf)
        probe_hits = sum(
            1 for probe in probe_tokens if probe and f" {' '.join(probe)} " in padded
        )
        score += 0.15 * min(probe_hits, 2)
        if hints and _mentions(candidate.text, hints):
            score *= _SUBJECT_BONUS
        if _BODY_PAGE_MARKER in candidate.text[:120].casefold():
            score *= _BODY_PAGE_BONUS
        score += 0.05 * (candidate.rrf / best_rrf)
        ranked.append((-score, entry_id))
    ranked.sort()
    return tuple(entry_id for _score, entry_id in ranked)


def _mentions(text: str, hints: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(hint in folded for hint in hints)


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    projections=(PDFPageTextEvidenceProjection(),),
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "PDF_PAGE_SOURCE_ADAPTER",
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDFPageSourceAdapter",
    "PDFPageTextEvidenceProjection",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
