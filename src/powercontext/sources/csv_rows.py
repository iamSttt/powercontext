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

"""Capture every CSV record as a row-level Source with primary-key identity."""

from __future__ import annotations

import csv
import io
import json
from typing import Annotated, Self
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

CSV_ROW_SOURCE_NAME = "csv-row"

NonEmptyText = Annotated[str, Field(min_length=1)]


def _require_document_id(value: str) -> str:
    if not value.strip():
        raise ValueError("document_id must not be blank")  # noqa: TRY003
    if value != value.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003
    return value


def _require_primary_key(value: str) -> str:
    if not value.strip():
        raise ValueError("primary_key must not be blank")  # noqa: TRY003
    return value


def _require_primary_key_value(value: str) -> str:
    if not value.strip():
        raise ValueError("primary_key_value must not be blank")  # noqa: TRY003
    return value


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document decoded by the caller."""

    document_id: str
    primary_key: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _require_document_id(value)

    @field_validator("primary_key")
    @classmethod
    def validate_primary_key(cls, value: str) -> str:
        return _require_primary_key(value)


class CSVRowInput(BaseModel):
    """One CSV record described by its header and its raw field values."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _require_document_id(value)

    @field_validator("primary_key")
    @classmethod
    def validate_primary_key(cls, value: str) -> str:
        return _require_primary_key(value)

    @field_validator("primary_key_value")
    @classmethod
    def validate_primary_key_value(cls, value: str) -> str:
        return _require_primary_key_value(value)

    @model_validator(mode="after")
    def validate_row_width(self) -> Self:
        if len(self.columns) != len(self.values):
            raise ValueError("row must carry exactly one value per header column")  # noqa: TRY003
        return self


class CSVRowSource(Source):
    """A captured CSV record retaining its original columns and values."""

    source_id: NonEmptyText
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if self.source_id != self.name:
            raise ValueError("CSV row Source identity must equal its name")  # noqa: TRY003
        if len(self.columns) != len(self.values):
            raise ValueError("row must carry exactly one value per header column")  # noqa: TRY003
        return self


class CSVRowSourceAdapter:
    """Resolve and read row-level CSV Sources without altering raw values."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Expose one complete CSV record as retrievable text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = _row_mapping(source.columns, source.values)
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the delimiter-safe identity of one CSV record."""

    _require_document_id(document_id)
    _require_primary_key_value(primary_key_value)
    identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"CSV row identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse and validate one whole CSV document into row-level inputs."""

    reader = csv.reader(io.StringIO(_decode_document(value.content), newline=""), strict=True)
    try:
        header = next(reader, None)
        if header is None:
            raise ValueError("CSV document must start with a header record")  # noqa: TRY003
        columns = _validate_header(header)
        primary_index = _primary_key_index(columns, value.primary_key)
        rows: list[CSVRowInput] = []
        seen: set[str] = set()
        for record in reader:
            if not record:
                continue
            if len(record) != len(columns):
                raise ValueError(  # noqa: TRY003
                    f"CSV record {len(rows) + 2} must carry {len(columns)} fields, observed {len(record)}"
                )
            primary_key_value = _require_primary_key_value(record[primary_index])
            if primary_key_value in seen:
                raise ValueError(  # noqa: TRY003
                    f"CSV primary key value {primary_key_value!r} is not unique within the document"
                )
            seen.add(primary_key_value)
            csv_row_source_id(value.document_id, primary_key_value)
            rows.append(
                CSVRowInput(
                    document_id=value.document_id,
                    primary_key=value.primary_key,
                    primary_key_value=primary_key_value,
                    columns=columns,
                    values=tuple(record),
                )
            )
    except csv.Error as error:
        raise ValueError(f"CSV document is malformed: {error}") from error
    return tuple(rows)


def _decode_document(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV document must be strict UTF-8 with an optional leading BOM") from error


def _validate_header(header: list[str]) -> tuple[str, ...]:
    if not header:
        raise ValueError("CSV header must declare at least one column")  # noqa: TRY003
    declared: set[str] = set()
    for column in header:
        if not column.strip():
            raise ValueError("CSV header columns must be non-empty and not blank")  # noqa: TRY003
        if column in declared:
            raise ValueError(f"CSV header column {column!r} is declared more than once")  # noqa: TRY003
        declared.add(column)
    return tuple(header)


def _primary_key_index(columns: tuple[str, ...], primary_key: str) -> int:
    try:
        return columns.index(primary_key)
    except ValueError:
        raise ValueError(f"primary_key {primary_key!r} does not match any CSV header column") from None


def _row_mapping(columns: tuple[str, ...], values: tuple[str, ...]) -> dict[str, str]:
    return {column: value for column, value in zip(columns, values, strict=True)}


CSV_ROW_SOURCE_ADAPTER = CSVRowSourceAdapter()
CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    projections=(CSVRowTextEvidenceProjection(),),
)


__all__ = [
    "CSV_ROW_SOURCE_ADAPTER",
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSVRowSourceAdapter",
    "CSVRowTextEvidenceProjection",
    "csv_row_source_id",
    "parse_csv_document",
]
