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

"""Coordinate Source withdrawal and derived-evidence recovery in one Scope.

A Scope owns one dependency graph whose roots are captured Sources and whose derived
nodes are Memory entries and Topic Revisions. Registering a node binds one immutable
evidence reference to it together with the parents and the ``all``/``any`` support
policy that decides whether the node is currently usable.

Withdrawing a Source merges that observation into the Scope's graph and reconciles the
native artifacts: unsupported Memory entries are deactivated in place, unsupported
Topics disappear from generation through this module's filtering, and ``audit`` reads
stay possible for every node that was ever registered. The graph and the observed
Source availability are persisted in the deployment database, so a Runtime restart
recovers the same usable set and a replayed observation stays a no-op.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, field_validator
from sqlalchemy import text

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

LifecycleFamily = Literal["source", "memory", "topic"]
LifecyclePolicy = Literal["all", "any"]
LifecyclePurpose = Literal["generation", "audit"]

_FAMILIES: frozenset[str] = frozenset({"source", "memory", "topic"})
_POLICIES: frozenset[str] = frozenset({"all", "any"})
_PURPOSES: frozenset[str] = frozenset({"generation", "audit"})
_MAX_IDENTIFIER_LENGTH = 255

_INSERT_NODE = (
    "INSERT INTO pc_source_lifecycle_nodes "
    "(scope_id, node_id, ordinal, family, parents, policy, binding, revision) "
    "VALUES (:scope_id, :node_id, :ordinal, :family, :parents, :policy, :binding, :revision)"
)
_SELECT_NODES = (
    "SELECT node_id, ordinal, family, parents, policy, binding, revision "
    "FROM pc_source_lifecycle_nodes WHERE scope_id = :scope_id ORDER BY ordinal"
)
_SELECT_AVAILABILITY = (
    "SELECT node_id, sequence, available FROM pc_source_lifecycle_sources WHERE scope_id = :scope_id"
)
_DELETE_AVAILABILITY = (
    "DELETE FROM pc_source_lifecycle_sources WHERE scope_id = :scope_id AND node_id = :node_id"
)
_INSERT_AVAILABILITY = (
    "INSERT INTO pc_source_lifecycle_sources (scope_id, node_id, sequence, available) "
    "VALUES (:scope_id, :node_id, :sequence, :available)"
)
_CREATE_NODES = (
    "CREATE TABLE IF NOT EXISTS pc_source_lifecycle_nodes ("
    "scope_id VARCHAR(255) NOT NULL,"
    "node_id VARCHAR(255) NOT NULL,"
    "ordinal INTEGER NOT NULL,"
    "family VARCHAR(16) NOT NULL,"
    "parents TEXT NOT NULL,"
    "policy VARCHAR(8) NOT NULL,"
    "binding TEXT NOT NULL,"
    "revision INTEGER,"
    "PRIMARY KEY (scope_id, node_id))"
)
_CREATE_AVAILABILITY = (
    "CREATE TABLE IF NOT EXISTS pc_source_lifecycle_sources ("
    "scope_id VARCHAR(255) NOT NULL,"
    "node_id VARCHAR(255) NOT NULL,"
    "sequence INTEGER NOT NULL,"
    "available INTEGER NOT NULL,"
    "PRIMARY KEY (scope_id, node_id))"
)


class SourceLifecycleError(ValueError):
    """Reject one invalid lifecycle registration, availability event, or resolution."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"source lifecycle request is invalid: {reason}")
        self.reason = reason


class SourceLifecycleEvent(BaseModel):
    """One monotonic availability observation for a registered Source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: StrictStr
    revision: StrictInt
    sequence: StrictInt
    available: StrictBool
    reason: StrictStr

    @field_validator("node_id", "reason")
    @classmethod
    def require_text(cls, value: str) -> str:
        """Reject blank identifiers and reasons."""

        if not value.strip():
            raise ValueError("source lifecycle text must not be blank")  # noqa: TRY003
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def require_counter(cls, value: int) -> int:
        """Reject non-positive revision and sequence counters."""

        if value < 1:
            raise ValueError("source lifecycle counters must be positive")  # noqa: TRY003
        return value


@dataclass(frozen=True)
class _LifecycleNode:
    """One registered evidence node inside a Scope's dependency graph."""

    node_id: str
    ordinal: int
    family: LifecycleFamily
    parents: tuple[str, ...]
    policy: LifecyclePolicy
    revision: int | None
    reference: dict[str, Any]


def open_source_lifecycle(runtime: BuiltinRuntime) -> SourceLifecycleApplication:
    """Build the Source Lifecycle application that persists into this Runtime's database."""

    provider = getattr(runtime, "_provider", None)
    database = getattr(provider, "database", None)
    return SourceLifecycleApplication(runtime, database if isinstance(database, AsyncDatabase) else None)


class SourceLifecycleApplication:
    """Build the Scope-bound Source Lifecycle service for one Runtime."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase | None = None) -> None:
        self._runtime = runtime
        self._database = database
        self._schema_lock = asyncio.Lock()
        self._schema_ready = False
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def runtime(self) -> BuiltinRuntime:
        """Return the Runtime whose derived evidence this service reconciles."""

        return self._runtime

    @property
    def available(self) -> bool:
        """Return whether this Runtime exposes the database that holds lifecycle state."""

        return self._database is not None

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        """Return the lifecycle service bound to one Scope."""

        return ScopedSourceLifecycle(self, scope_id)

    async def database(self) -> AsyncDatabase:
        """Return the state database, creating the lifecycle tables once per Runtime."""

        database = self._database
        if database is None:
            raise SourceLifecycleError("lifecycle-database")
        if not self._schema_ready:
            async with self._schema_lock:
                if not self._schema_ready:
                    async with database.transaction() as connection:
                        await connection.exec_driver_sql(_CREATE_NODES)
                        await connection.exec_driver_sql(_CREATE_AVAILABILITY)
                    self._schema_ready = True
        return database

    def lock(self, scope_id: str) -> asyncio.Lock:
        """Return the write lock that serializes registrations and observations per Scope."""

        lock = self._locks.get(scope_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[scope_id] = lock
        return lock


class ScopedSourceLifecycle:
    """Register evidence nodes and apply Source availability events for one Scope."""

    def __init__(self, application: SourceLifecycleApplication, scope_id: str) -> None:
        if not isinstance(scope_id, str) or not scope_id.strip():
            raise SourceLifecycleError("lifecycle-scope")
        self._application = application
        self.scope_id = scope_id

    async def register_source(self, node_id: str, source_ref: SourceRef, /, *, revision: int = 1) -> None:
        """Register one captured Source as a lifecycle root."""

        if revision < 1:
            raise SourceLifecycleError("lifecycle-revision")
        await self._register(node_id, "source", source_ref, parents=(), policy="all", revision=revision)

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: Sequence[str] = (),
        policy: LifecyclePolicy = "all",
    ) -> None:
        """Register one Memory entry version and the evidence that supports it."""

        await self._register(node_id, "memory", citation, parents=tuple(parents), policy=policy, revision=None)

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        /,
        *,
        parents: Sequence[str] = (),
        policy: LifecyclePolicy = "all",
    ) -> None:
        """Register one Topic Revision and the evidence that supports it."""

        await self._register(node_id, "topic", artifact_ref, parents=tuple(parents), policy=policy, revision=None)

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        """Apply one Source availability observation and return the sorted active node ids."""

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("lifecycle-event")
        database = await self._application.database()
        async with self._application.lock(self.scope_id):
            nodes, availability = await self._load(database)
            node = next((item for item in nodes if item.node_id == event.node_id), None)
            if node is None or node.family != "source":
                raise SourceLifecycleError("lifecycle-node")
            if node.revision != event.revision:
                raise SourceLifecycleError("lifecycle-revision")
            recorded = availability.get(node.node_id)
            if recorded is not None:
                if event.sequence < recorded[0] or (event.sequence == recorded[0] and event.available != recorded[1]):
                    raise SourceLifecycleError("lifecycle-sequence")
            if recorded is None or event.sequence > recorded[0]:
                await self._store_availability(database, node.node_id, event.sequence, event.available)
                availability[node.node_id] = (event.sequence, event.available)
            active = _resolve_active(nodes, _observed(availability))
            await self._sync_memory_states(nodes, active, reason=event.reason)
        return sorted(active)

    async def resolve(
        self,
        node_id: str,
        /,
        *,
        purpose: LifecyclePurpose = "generation",
    ) -> SourceRef | MemoryCitation | ArtifactRef:
        """Return the exact evidence reference bound to one node for one purpose."""

        if purpose not in _PURPOSES:
            raise SourceLifecycleError("lifecycle-purpose")
        identifier = _require_identifier(node_id)
        database = await self._application.database()
        nodes, availability = await self._load(database)
        node = next((item for item in nodes if item.node_id == identifier), None)
        if node is None:
            raise SourceLifecycleError("lifecycle-node")
        if purpose == "generation" and node.node_id not in _resolve_active(nodes, _observed(availability)):
            raise SourceLifecycleError("lifecycle-inactive")
        return _decode_reference(node.family, node.reference)

    async def inactive_topic_artifact_ids(self) -> frozenset[str]:
        """Return Topic artifact ids that Lifecycle State keeps out of generation."""

        if not self._application.available:
            return frozenset()
        database = await self._application.database()
        nodes, availability = await self._load(database)
        topics = tuple(node for node in nodes if node.family == "topic")
        if not topics:
            return frozenset()
        active = _resolve_active(nodes, _observed(availability))
        inactive: set[str] = set()
        for node in topics:
            if node.node_id in active:
                continue
            reference = _decode_reference("topic", node.reference)
            if isinstance(reference, ArtifactRef):
                inactive.add(reference.artifact_id)
        return frozenset(inactive)

    async def _register(
        self,
        node_id: str,
        family: LifecycleFamily,
        reference: SourceRef | MemoryCitation | ArtifactRef,
        *,
        parents: tuple[str, ...],
        policy: LifecyclePolicy,
        revision: int | None,
    ) -> None:
        identifier = _require_identifier(node_id)
        if policy not in _POLICIES:
            raise SourceLifecycleError("lifecycle-policy")
        normalized = _normalize_parents(identifier, parents, derived=family != "source")
        _require_reference(family, reference)
        if revision is not None and revision < 1:
            raise SourceLifecycleError("lifecycle-revision")
        payload = _encode_reference(reference)
        database = await self._application.database()
        async with self._application.lock(self.scope_id):
            nodes, availability = await self._load(database)
            existing = next((item for item in nodes if item.node_id == identifier), None)
            if existing is not None:
                if (
                    existing.family == family
                    and existing.parents == normalized
                    and existing.policy == policy
                    and existing.revision == revision
                    and existing.reference == payload
                ):
                    return
                raise SourceLifecycleError("lifecycle-registration")
            known = {item.node_id for item in nodes}
            if any(parent not in known for parent in normalized):
                raise SourceLifecycleError("lifecycle-parent")
            node = _LifecycleNode(
                node_id=identifier,
                ordinal=max((item.ordinal for item in nodes), default=0) + 1,
                family=family,
                parents=normalized,
                policy=policy,
                revision=revision,
                reference=payload,
            )
            async with database.transaction() as connection:
                await connection.execute(
                    text(_INSERT_NODE),
                    {
                        "scope_id": self.scope_id,
                        "node_id": node.node_id,
                        "ordinal": node.ordinal,
                        "family": node.family,
                        "parents": _dump_json(list(node.parents)),
                        "policy": node.policy,
                        "binding": _dump_json(node.reference),
                        "revision": node.revision,
                    },
                )
            if family == "memory":
                registered = (*nodes, node)
                active = _resolve_active(registered, _observed(availability))
                await self._sync_memory_states((node,), active, reason="lifecycle-registration")

    async def _load(
        self,
        database: AsyncDatabase,
    ) -> tuple[tuple[_LifecycleNode, ...], dict[str, tuple[int, bool]]]:
        async with database.transaction() as connection:
            node_rows = (
                (await connection.execute(text(_SELECT_NODES), {"scope_id": self.scope_id})).mappings().all()
            )
            source_rows = (
                (await connection.execute(text(_SELECT_AVAILABILITY), {"scope_id": self.scope_id}))
                .mappings()
                .all()
            )
        nodes = tuple(_node_from_row(row) for row in node_rows)
        availability = {
            str(row["node_id"]): (int(row["sequence"]), bool(row["available"])) for row in source_rows
        }
        return nodes, availability

    async def _store_availability(
        self,
        database: AsyncDatabase,
        node_id: str,
        sequence: int,
        available: bool,
    ) -> None:
        parameters = {
            "scope_id": self.scope_id,
            "node_id": node_id,
            "sequence": sequence,
            "available": 1 if available else 0,
        }
        async with database.transaction() as connection:
            await connection.execute(text(_DELETE_AVAILABILITY), parameters)
            await connection.execute(text(_INSERT_AVAILABILITY), parameters)

    async def _sync_memory_states(
        self,
        nodes: Sequence[_LifecycleNode],
        active: frozenset[str],
        *,
        reason: str,
    ) -> None:
        memory_nodes = tuple(node for node in nodes if node.family == "memory")
        if not memory_nodes:
            return
        memory = self._application.runtime.memory.for_scope(self.scope_id)
        page = await memory.list(include_inactive=True)
        states = {record.entry.entry_id: record.state for record in page.entries}
        activate: list[str] = []
        deactivate: list[str] = []
        for node in memory_nodes:
            entry_id = _entry_id(node)
            state = states.get(entry_id)
            if state is None:
                raise SourceLifecycleError("lifecycle-entry")
            supported = node.node_id in active
            if (state == "active") == supported:
                continue
            if supported:
                activate.append(entry_id)
            else:
                deactivate.append(entry_id)
        if deactivate:
            await memory.set_active(deactivate, active=False, reason=reason)
        if activate:
            await memory.set_active(activate, active=True, reason=reason)


def _require_identifier(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceLifecycleError("lifecycle-node-id")
    if len(value) > _MAX_IDENTIFIER_LENGTH:
        raise SourceLifecycleError("lifecycle-node-id")
    return value


def _normalize_parents(identifier: str, parents: Sequence[str], *, derived: bool) -> tuple[str, ...]:
    if not isinstance(parents, (tuple, list)):
        raise SourceLifecycleError("lifecycle-parents")
    normalized = tuple(parents)
    if derived and not normalized:
        raise SourceLifecycleError("lifecycle-parents")
    if not derived and normalized:
        raise SourceLifecycleError("lifecycle-parents")
    for parent in normalized:
        if not isinstance(parent, str) or not parent.strip():
            raise SourceLifecycleError("lifecycle-parents")
    if len(set(normalized)) != len(normalized) or identifier in normalized:
        raise SourceLifecycleError("lifecycle-parents")
    return normalized


def _require_reference(
    family: LifecycleFamily,
    reference: SourceRef | MemoryCitation | ArtifactRef,
) -> None:
    expected: type[SourceRef] | type[MemoryCitation] | type[ArtifactRef]
    if family == "source":
        expected = SourceRef
    elif family == "memory":
        expected = MemoryCitation
    else:
        expected = ArtifactRef
    if not isinstance(reference, expected):
        raise SourceLifecycleError("lifecycle-reference")


def _encode_reference(reference: SourceRef | MemoryCitation | ArtifactRef) -> dict[str, Any]:
    payload = reference.model_dump(mode="json")
    if not isinstance(payload, dict):
        raise SourceLifecycleError("lifecycle-reference")
    return cast("dict[str, Any]", payload)


def _decode_reference(
    family: LifecycleFamily,
    payload: Mapping[str, Any],
) -> SourceRef | MemoryCitation | ArtifactRef:
    if family == "source":
        return SourceRef.model_validate(dict(payload))
    if family == "memory":
        return MemoryCitation.model_validate(dict(payload))
    return ArtifactRef.model_validate(dict(payload))


def _entry_id(node: _LifecycleNode) -> str:
    entry_id = node.reference.get("entry_id")
    if not isinstance(entry_id, str) or not entry_id:
        raise SourceLifecycleError("lifecycle-entry")
    return entry_id


def _dump_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: object) -> object:
    if not isinstance(value, str):
        raise SourceLifecycleError("lifecycle-storage")
    try:
        return json.loads(value)
    except ValueError as error:
        raise SourceLifecycleError("lifecycle-storage") from error


def _node_from_row(row: Mapping[str, Any]) -> _LifecycleNode:
    family = row["family"]
    policy = row["policy"]
    if not isinstance(family, str) or family not in _FAMILIES:
        raise SourceLifecycleError("lifecycle-storage")
    if not isinstance(policy, str) or policy not in _POLICIES:
        raise SourceLifecycleError("lifecycle-storage")
    parents = _load_json(row["parents"])
    reference = _load_json(row["binding"])
    if not isinstance(parents, list) or not isinstance(reference, dict):
        raise SourceLifecycleError("lifecycle-storage")
    revision = row["revision"]
    return _LifecycleNode(
        node_id=str(row["node_id"]),
        ordinal=int(row["ordinal"]),
        family=cast("LifecycleFamily", family),
        parents=tuple(str(parent) for parent in parents),
        policy=cast("LifecyclePolicy", policy),
        revision=None if revision is None else int(revision),
        reference=cast("dict[str, Any]", reference),
    )


def _observed(availability: Mapping[str, tuple[int, bool]]) -> dict[str, bool]:
    """Return observed Source availability, defaulting an unobserved Source to available."""

    return {node_id: available for node_id, (_, available) in availability.items()}


def _resolve_active(
    nodes: Sequence[_LifecycleNode],
    available: Mapping[str, bool],
) -> frozenset[str]:
    active: set[str] = set()
    for node in nodes:
        if node.family == "source":
            if available.get(node.node_id, True):
                active.add(node.node_id)
            continue
        support = tuple(parent in active for parent in node.parents)
        satisfied = all(support) if node.policy == "all" else any(support)
        if satisfied:
            active.add(node.node_id)
    return frozenset(active)
