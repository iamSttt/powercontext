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

"""Count-driven admission of pending Source journal identities.

The count condition complements the existing time condition. It reads only
persisted journal identity and the Family's committed Source cursor, then
requests one ordinary processing invocation for the accepting Family. Explicit
flush stays independent: it never consults this trigger.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from powercontext._logging import log_safely
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime
    from powercontext.builtin.runtime.relational import RelationalContexts

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.2
REQUEST_COOLDOWN_SECONDS = 2.0
RETRY_BASE_SECONDS = 0.5
RETRY_CAP_SECONDS = 5.0
_FAMILIES = frozenset({"memory", "topic-memory"})


@dataclass(frozen=True, slots=True)
class SourceCountBinding:
    """One Family's count threshold over its committed Source cursor."""

    family: str
    binding_name: str
    threshold: int

    def __post_init__(self) -> None:
        if self.family not in _FAMILIES:
            raise ValueError("count trigger Family must be memory or topic-memory")  # noqa: TRY003
        if not isinstance(self.binding_name, str) or not self.binding_name.strip():
            raise ValueError("count trigger requires a processing binding name")  # noqa: TRY003
        if type(self.threshold) is not int or self.threshold < 1:
            raise ValueError("count threshold must be a positive integer")  # noqa: TRY003


@dataclass(slots=True)
class _Retry:
    failures: int
    ready_at: float


class SourceCountTrigger:
    """Wake one ordinary invocation per Family once its pending count is met.

    Both Families count persisted journal positions after the cursor they last
    committed, so duplicate captures and receipt trust upgrades never add
    pending work while lineage-only Sources still count. A Memory invocation
    consumes exactly one window and re-evaluates the remainder; an accepted
    Topic Memory request keeps its own wave semantics.
    """

    def __init__(
        self,
        runtime: BuiltinRuntime,
        contexts: RelationalContexts,
        bindings: Sequence[SourceCountBinding],
        *,
        poll_seconds: float = POLL_SECONDS,
        request_cooldown_seconds: float = REQUEST_COOLDOWN_SECONDS,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("count trigger poll interval must be positive")  # noqa: TRY003
        if request_cooldown_seconds < 0:
            raise ValueError("count trigger request cooldown must be non-negative")  # noqa: TRY003
        self._runtime = runtime
        self._contexts = contexts
        self._bindings = tuple(bindings)
        self._poll_seconds = poll_seconds
        self._request_cooldown_seconds = request_cooldown_seconds
        self._cursors = SourceCursorRepository()
        self._retries: dict[tuple[str, str], _Retry] = {}
        self._cooling: dict[tuple[str, str], float] = {}
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> SourceCountTrigger:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="source-count-trigger")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._poll_seconds)
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # a failed pass must not stop later count checks
                log_safely(
                    logger,
                    logging.WARNING,
                    "Source count trigger pass failed",
                    exc_info=error,
                    extra={"event": "source_count.trigger.failure", "outcome": "failed"},
                )

    async def check_once(self) -> None:
        """Request at most one invocation per Scope and Family above its threshold."""

        if self._runtime._closing or self._runtime._closed:  # noqa: SLF001 - same-package lifecycle probe
            return
        now = asyncio.get_running_loop().time()
        for scope_id in await self._contexts.scope_ids():
            for binding in self._bindings:
                key = (scope_id, binding.binding_name)
                if self._deferred(key, now):
                    continue
                if await self._pending(scope_id, binding) < binding.threshold:
                    continue
                await self._request(scope_id, binding, key, now)

    def _deferred(self, key: tuple[str, str], now: float) -> bool:
        retry = self._retries.get(key)
        if retry is not None and retry.ready_at > now:
            return True
        cooling = self._cooling.get(key)
        return cooling is not None and cooling > now

    async def _pending(self, scope_id: str, binding: SourceCountBinding) -> int:
        heads = SOURCE_JOURNAL_HEADS_TABLE
        async with self._contexts.database.transaction() as connection:
            cursor = await self._cursors.load(connection, scope_id, binding.binding_name)
            head = await connection.scalar(select(heads.c.position).where(heads.c.scope_id == scope_id))
        consumed = 0 if cursor is None else cursor.cursor.sequence
        return max(0, int(head or 0) - consumed)

    async def _request(self, scope_id: str, binding: SourceCountBinding, key: tuple[str, str], now: float) -> None:
        try:
            if binding.family == "memory":
                await self._runtime.memory.for_scope(scope_id).flush()
            else:
                await self._runtime.topic_memory.for_scope(scope_id).flush()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            previous = self._retries.get(key)
            failures = 1 if previous is None else previous.failures + 1
            delay = min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * 2 ** min(failures - 1, 20))
            self._retries[key] = _Retry(failures, now + delay)
            log_safely(
                logger,
                logging.WARNING,
                "Source count invocation failed",
                exc_info=error,
                extra={
                    "event": "source_count.invocation.failure",
                    "outcome": "failed",
                    "family": binding.family,
                    "scope_id": scope_id,
                    "failures": failures,
                },
            )
        else:
            self._retries.pop(key, None)
            if binding.family == "topic-memory":
                # One accepted wave already owns the frozen input; new input or
                # a finished wave ends the cooldown instead of piling requests.
                self._cooling[key] = now + self._request_cooldown_seconds


__all__ = ["SourceCountBinding", "SourceCountTrigger"]
