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

"""Select literal, query-relevant spans from recalled Memory entries.

The selector works only on text that retrieval already returned: every delivered
fragment is an exact ``text[start:end]`` slice of one entry, never a rewrite or a
summary. Spans are ranked by lexical relevance to the current query so that evidence
late in a long entry can win space that a prefix-only assembly would spend on
whatever happened to be recalled first, and the complete rendered envelope is
measured against the caller's UTF-8 byte budget before anything is delivered.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

_BEGIN_MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_END_MARKER = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
_TRUST = "untrusted_history"
_ENTRY_OPEN = '{"trust":"untrusted_history","items":'
_SENTENCE_END = frozenset(".!?\n;。！？；")
_SOFT_BREAK = frozenset(",，;；:：、 \t\n")
_MAX_SPAN_CHARS = 480
_TARGET_SPAN_CHARS = 240
_DEFAULT_LIMIT = 8
_TOKEN_PATTERN = __import__("re").compile(r"[a-z0-9]+|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


@dataclass(frozen=True)
class EvidenceSource:
    """One recalled Memory entry exactly as retrieval reported it."""

    scope_id: str
    is_current_scope: bool
    memory_ref: ArtifactRef
    entry_id: str
    entry_version_id: str
    text: str


@dataclass(frozen=True)
class EvidenceFragment:
    """One literal span of one source entry, with its exact character range."""

    source_index: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class EvidenceSelection:
    """The delivered evidence context and the exact spans it was rendered from."""

    content: str | None
    fragments: tuple[EvidenceFragment, ...]
    byte_count: int


@dataclass(frozen=True)
class _Span:
    source_index: int
    start: int
    end: int
    score: float


def build_evidence_selection(
    *,
    query: str,
    sources: Sequence[EvidenceSource],
    max_bytes: int,
    limit: int = _DEFAULT_LIMIT,
) -> EvidenceSelection:
    """Choose literal spans that fit ``max_bytes`` and render one untrusted envelope."""

    if not sources:
        return EvidenceSelection(content=None, fragments=(), byte_count=0)
    spans = _candidate_spans(sources)
    if not spans:
        return EvidenceSelection(content=None, fragments=(), byte_count=0)
    selected: list[_Span] = []
    for span in _rank_spans(query, spans, sources):
        trial = _merge((*selected, span), sources)
        if len(trial) > limit:
            continue
        if len(_content(trial, sources).encode("utf-8")) > max_bytes:
            continue
        selected.append(span)
    fragments = _merge(tuple(selected), sources)
    if not fragments:
        return EvidenceSelection(content=None, fragments=(), byte_count=0)
    content = _content(fragments, sources)
    return EvidenceSelection(content=content, fragments=fragments, byte_count=len(content.encode("utf-8")))


def _candidate_spans(sources: Sequence[EvidenceSource]) -> tuple[_Span, ...]:
    spans: list[_Span] = []
    for index, source in enumerate(sources):
        for start, end in _split(source.text):
            if source.text[start:end].strip():
                spans.append(_Span(source_index=index, start=start, end=end, score=0.0))
    return tuple(spans)


def _split(text: str) -> tuple[tuple[int, int], ...]:
    """Split one entry into contiguous sentence spans, subdividing overlong sentences."""

    pieces: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char in _SENTENCE_END:
            end = index + 1
            while end < len(text) and text[end] in " \t\r\n":
                end += 1
            pieces.extend(_subdivide(text, start, end))
            start = end
    if start < len(text):
        pieces.extend(_subdivide(text, start, len(text)))
    return tuple(pieces)


def _subdivide(text: str, start: int, end: int) -> tuple[tuple[int, int], ...]:
    """Keep one span navigable by breaking at natural boundaries before the hard cap."""

    if end - start <= _MAX_SPAN_CHARS:
        return ((start, end),)
    pieces: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > _MAX_SPAN_CHARS:
        cut = cursor + _TARGET_SPAN_CHARS
        softened = cut
        while softened > cursor and text[softened] not in _SOFT_BREAK:
            softened -= 1
        if softened <= cursor:
            softened = cut
        pieces.append((cursor, softened))
        cursor = softened
    pieces.append((cursor, end))
    return tuple(pieces)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_normalize(token) for token in _TOKEN_PATTERN.findall(text.lower()))


def _normalize(token: str) -> str:
    """Fold a plain English plural so a query word matches its corpus inflection."""

    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _rank_spans(query: str, spans: Sequence[_Span], sources: Sequence[EvidenceSource]) -> tuple[_Span, ...]:
    """Score every span by query term rarity and adjacent-pair presence, then rank it."""

    lowered = [source.text.lower() for source in sources]
    span_texts = [lowered[span.source_index][span.start : span.end] for span in spans]
    span_tokens = [set(_tokens(text)) for text in span_texts]
    document_frequency = Counter(token for tokens in span_tokens for token in tokens)
    total = len(spans)
    idf = {
        token: math.log(1 + (total - count + 0.5) / (count + 0.5)) for token, count in document_frequency.items()
    }
    query_tokens = tuple(dict.fromkeys(_tokens(query)))
    literal_pairs = tuple(zip(query_tokens, query_tokens[1:], strict=False))
    scored: list[_Span] = []
    for span, tokens, text in zip(spans, span_tokens, span_texts, strict=True):
        score = 0.0
        for token in query_tokens:
            if token in tokens:
                score += idf.get(token, 0.0)
        for first, second in literal_pairs:
            if first in tokens and second in tokens:
                score += 0.5 * (idf.get(first, 0.0) + idf.get(second, 0.0)) * _adjacency(text, first, second)
        scored.append(_Span(source_index=span.source_index, start=span.start, end=span.end, score=score))
    scored.sort(key=lambda span: (-span.score, span.source_index, span.start))
    return tuple(scored)


def _adjacency(text: str, first: str, second: str) -> float:
    """Reward two query terms that appear next to each other in this exact span."""

    if f"{first} {second}" in text or f"{first}{second}" in text:
        return 1.0
    return 0.0


def _merge(spans: Sequence[_Span], sources: Sequence[EvidenceSource]) -> tuple[EvidenceFragment, ...]:
    """Coalesce touching spans of one entry so no budget is spent on extra citations."""

    grouped: dict[int, list[_Span]] = {}
    for span in spans:
        grouped.setdefault(span.source_index, []).append(span)
    fragments: list[EvidenceFragment] = []
    for source_index in sorted(grouped):
        ordered = sorted(grouped[source_index], key=lambda span: span.start)
        start, end = ordered[0].start, ordered[0].end
        for span in ordered[1:]:
            if span.start <= end:
                end = max(end, span.end)
                continue
            fragments.append(_fragment(sources, source_index, start, end))
            start, end = span.start, span.end
        fragments.append(_fragment(sources, source_index, start, end))
    return tuple(fragments)


def _fragment(sources: Sequence[EvidenceSource], source_index: int, start: int, end: int) -> EvidenceFragment:
    return EvidenceFragment(
        source_index=source_index,
        start=start,
        end=end,
        text=sources[source_index].text[start:end],
    )


def _citation(source: EvidenceSource) -> dict[str, object]:
    return {
        "scope_id": source.scope_id,
        "memory_ref": source.memory_ref.model_dump(mode="json"),
        "entry_id": source.entry_id,
        "entry_version_id": source.entry_version_id,
    }


def _item(fragment: EvidenceFragment, source: EvidenceSource) -> dict[str, object]:
    return {
        "citation": _citation(source),
        "content": fragment.text,
        "start": fragment.start,
        "end": fragment.end,
    }


def _content(fragments: Sequence[EvidenceFragment], sources: Sequence[EvidenceSource]) -> str:
    envelope = {
        "trust": _TRUST,
        "items": [_item(fragment, sources[fragment.source_index]) for fragment in fragments],
    }
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


__all__ = [
    "EvidenceFragment",
    "EvidenceSelection",
    "EvidenceSource",
    "build_evidence_selection",
]
