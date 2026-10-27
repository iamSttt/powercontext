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

"""Capture the selected Pi session branch as immutable event-level Sources."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_VERSION = 3
PI_CHECKPOINT_SCHEMA = "powercontext.pi-session-history.checkpoint.v1"

NonEmptyText = Annotated[str, Field(min_length=1)]

_MESSAGE_TYPE = "message"
_TOOL_CALL_TYPE = "toolCall"
_TEXT_BLOCK_TYPE = "text"
_SUMMARY_TYPES = frozenset({"compaction", "branch_summary"})


class PiSessionInput(BaseModel):
    """One complete Pi JSONL session archive and its optional selected leaf."""

    content: bytes
    leaf_id: str | None = None

    @field_validator("leaf_id")
    @classmethod
    def validate_leaf_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("leaf_id must be a non-empty string when supplied")  # noqa: TRY003
        return value


class PiEventInput(BaseModel):
    """One visible event on the selected branch with its complete raw record."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """A captured Pi event retaining its original record and rendered body."""

    source_id: NonEmptyText
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if self.source_id != self.name:
            raise ValueError("Pi event Source identity must equal its name")  # noqa: TRY003
        return self


class PiEventSourceAdapter:
    """Resolve and read immutable Pi event Sources."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose one complete Pi event as retrievable text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the stable identity of one Pi session event."""

    if not session_id.strip():
        raise ValueError("session_id must not be blank")  # noqa: TRY003
    if not event_id.strip():
        raise ValueError("event_id must not be blank")  # noqa: TRY003
    identity = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"Pi event identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse one Pi archive into the recorded events of the selected branch."""

    session_id, records = _read_archive(value.content)
    nodes, order = _read_graph(records)
    leaf_id = _select_leaf(value.leaf_id, order)
    if leaf_id is None:
        return ()
    events: list[PiEventInput] = []
    for event_id in _ancestor_chain(leaf_id, nodes):
        node = nodes[event_id]
        rendered = _render_event(node.record)
        if rendered is None:
            continue
        role, body = rendered
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=event_id,
                parent_id=node.parent_id,
                event_type=str(node.record["type"]),
                role=role,
                content=f"{role}: {body}",
                record=node.record,
            )
        )
    return tuple(events)


class _GraphNode:
    """One validated archive record with its declared parent link."""

    __slots__ = ("parent_id", "record")

    def __init__(self, parent_id: str | None, record: dict[str, JsonValue]) -> None:
        self.parent_id = parent_id
        self.record = record


def _read_archive(content: bytes) -> tuple[str, list[dict[str, JsonValue]]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session archive must be UTF-8 encoded JSONL") from error
    records: list[dict[str, JsonValue]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Pi session line {number} is not valid JSON: {error}") from error
        if not isinstance(decoded, dict):
            raise ValueError(f"Pi session line {number} must be a JSON object")  # noqa: TRY003
        records.append(decoded)
    if not records:
        raise ValueError("Pi session archive must start with a session header record")  # noqa: TRY003
    header = records[0]
    if header.get("type") != "session":
        raise ValueError("Pi session archive must start with a session header record")  # noqa: TRY003
    if header.get("version") != PI_SESSION_VERSION:
        raise ValueError(f"Pi session header must declare version {PI_SESSION_VERSION}")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Pi session header must declare a non-empty id")  # noqa: TRY003
    return session_id, records[1:]


def _read_graph(records: list[dict[str, JsonValue]]) -> tuple[dict[str, _GraphNode], list[str]]:
    nodes: dict[str, _GraphNode] = {}
    order: list[str] = []
    for number, record in enumerate(records, start=2):
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError(f"Pi session line {number} must declare a non-empty id")  # noqa: TRY003
        if event_id in nodes:
            raise ValueError(f"Pi session record id {event_id!r} is declared more than once")  # noqa: TRY003
        parent_id = record.get("parentId")
        if parent_id is not None:
            if not isinstance(parent_id, str) or not parent_id.strip():
                raise ValueError(f"Pi session record {event_id!r} has an invalid parentId")  # noqa: TRY003
            if parent_id not in nodes:
                raise ValueError(  # noqa: TRY003
                    f"Pi session record {event_id!r} references unknown parent {parent_id!r}"
                )
        nodes[event_id] = _GraphNode(parent_id, record)
        order.append(event_id)
    return nodes, order


def _select_leaf(leaf_id: str | None, order: list[str]) -> str | None:
    if leaf_id is None:
        return order[-1] if order else None
    if leaf_id not in order:
        raise ValueError(f"Pi session leaf {leaf_id!r} is not a record of this archive")  # noqa: TRY003
    return leaf_id


def _ancestor_chain(leaf_id: str, nodes: dict[str, _GraphNode]) -> tuple[str, ...]:
    chain: list[str] = []
    current: str | None = leaf_id
    while current is not None:
        chain.append(current)
        current = nodes[current].parent_id
    chain.reverse()
    return tuple(chain)


def _render_event(record: dict[str, JsonValue]) -> tuple[str, str] | None:
    """Return the role and visible body of one record, or ``None`` without evidence."""

    event_type = record.get("type")
    if event_type == _MESSAGE_TYPE:
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        if not isinstance(role, str) or not role.strip():
            return None
        body = _message_body(message, role)
        if not body.strip():
            return None
        return role, body
    if isinstance(event_type, str) and event_type in _SUMMARY_TYPES:
        summary = record.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return None
        return event_type, summary
    if event_type == "custom_message":
        body = _text_block_body(record.get("content"))
        if not body.strip():
            return None
        return "custom", body
    return None


def _message_body(message: dict[str, JsonValue], role: str) -> str:
    if role == "toolResult":
        return (
            f"[tool_result {_as_text(message.get('toolName'))} {_as_text(message.get('toolCallId'))}] "
            f"{_text_block_body(message.get('content'))}"
        )
    if role == "bashExecution":
        return f"$ {_as_text(message.get('command'))}\n{_as_text(message.get('output'))}"
    return _content_body(message.get("content"))


def _content_body(content: JsonValue) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == _TEXT_BLOCK_TYPE:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif block_type == _TOOL_CALL_TYPE:
            arguments = block.get("arguments")
            parts.append(
                f"[tool_call {_as_text(block.get('name'))} {_as_text(block.get('id'))}] "
                f"{_compact_json(arguments)}"
            )
    return "\n".join(parts)


def _text_block_body(content: JsonValue) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == _TEXT_BLOCK_TYPE and isinstance(block.get("text"), str)
    )


def _as_text(value: JsonValue) -> str:
    return value if isinstance(value, str) else ""


def _compact_json(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_digest(record: dict[str, JsonValue]) -> str:
    encoded = _compact_json(record).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PiSessionConnector:
    """Import the newly selected Pi branch events that are not yet captured."""

    name = PI_SESSION_CONNECTOR_NAME
    version = "1"
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        events = parse_pi_session(PiSessionInput(content=self._path.read_bytes(), leaf_id=self._leaf_id))
        imported = _read_checkpoint(session.checkpoint)
        pending: list[tuple[str, PiEventInput]] = []
        for event in events:
            identity = pi_event_source_id(event.session_id, event.event_id)
            digest = _record_digest(event.record)
            captured = imported.get(identity)
            if captured is not None:
                if captured != digest:
                    raise ValueError(  # noqa: TRY003
                        f"Pi session event {event.event_id!r} changed after it was captured"
                    )
                continue
            imported[identity] = digest
            pending.append((identity, event))
        if not pending:
            return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=session.checkpoint)
        for identity, event in pending:
            await session.submit(identity, PI_EVENT_SOURCE_NAME, event)
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"schema": PI_CHECKPOINT_SCHEMA, "events": dict(sorted(imported.items()))},
        )


def _read_checkpoint(checkpoint: JsonValue | None) -> dict[str, str]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, dict):
        raise ValueError("Pi session checkpoint must be a JSON object")  # noqa: TRY003
    events = checkpoint.get("events")
    if events is None:
        return {}
    if not isinstance(events, dict):
        raise ValueError("Pi session checkpoint events must be a JSON object")  # noqa: TRY003
    imported: dict[str, str] = {}
    for identity, digest in events.items():
        if not isinstance(identity, str) or not isinstance(digest, str):
            raise ValueError("Pi session checkpoint entries must map identities to digests")  # noqa: TRY003
        imported[identity] = digest
    return imported


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    projections=(PiEventTextEvidenceProjection(),),
)


__all__ = [
    "PI_CHECKPOINT_SCHEMA",
    "PI_EVENT_SOURCE_ADAPTER",
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_SESSION_CONNECTOR_NAME",
    "PiEventInput",
    "PiEventSource",
    "PiEventSourceAdapter",
    "PiEventTextEvidenceProjection",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
