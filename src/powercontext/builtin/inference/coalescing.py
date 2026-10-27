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

"""Cross-call embedding request coalescing with a bounded successful-vector cache.

One wrapper instance is shared by every caller in an event loop. Callers that need the
same text join the same provider request instead of issuing one each, the wrapper keeps
at most ``max_batch_size`` distinct texts in one request, and successful vectors stay in
a bounded LRU cache.

Isolation rules the wrapper guarantees:

* each caller receives its own vectors in its own input order, duplicates preserved;
* cancelling one caller never cancels work another caller waits for;
* a failed request is never cached, and it only fails the callers that waited for it;
* a caller's usage aggregates each provider batch it actually waited for exactly once, so
  a pure cache hit reports zero requests.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel

__all__ = ["CoalescingEmbeddingModel"]


@dataclass(slots=True)
class _ProviderBatch:
    """Identity and provider usage of one request sent to the wrapped model."""

    batch_id: int
    usage: InferenceUsage | None = None


@dataclass(slots=True)
class _TextSlot:
    """The one shared, still-unresolved vector of one distinct text."""

    future: asyncio.Future[EmbeddingVector] = field(init=False)
    batch: _ProviderBatch | None = None


class CoalescingEmbeddingModel:
    """Share in-flight provider work across concurrent callers and cache successes.

    ``max_batch_size`` bounds the distinct texts of one provider request, ``coalesce_ms``
    is the merge window during which new texts join the same flush, and ``cache_size``
    bounds the successful-vector LRU (``0`` disables cross-call caching while still
    sharing requests that are already running).
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if max_batch_size < 1:
            raise ValueError("max_batch_size must be at least 1")
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must not be negative")
        if cache_size < 0:
            raise ValueError("cache_size must not be negative")
        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = coalesce_ms / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        # Texts that have not finished their provider request yet. A slot is created once
        # and removed only when its batch settles, so a late caller always joins running
        # work instead of paying for the same text twice.
        self._slots: dict[str, _TextSlot] = {}
        # Distinct texts waiting for the next flush, in first-arrival order.
        self._queued: list[str] = []
        self._flush_task: asyncio.Task[None] | None = None
        self._batch_tasks: set[asyncio.Task[None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._batch_count = 0

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one vector per input text, sharing and caching provider work."""

        self._bind_loop()
        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        distinct = tuple(dict.fromkeys(texts))
        resolved: dict[str, EmbeddingVector] = {}
        awaited: dict[str, _TextSlot] = {}
        for text in distinct:
            vector = self._cached(text)
            if vector is not None:
                resolved[text] = vector
                continue
            slot = self._slots.get(text)
            if slot is None:
                slot = self._enqueue(text)
            awaited[text] = slot
        if awaited:
            await self._share(tuple(awaited.values()))
            for text, slot in awaited.items():
                resolved[text] = slot.future.result()
        return EmbeddingResult(
            vectors=tuple(resolved[text] for text in texts),
            usage=self._usage(tuple(awaited.values())),
        )

    def _bind_loop(self) -> asyncio.AbstractEventLoop:
        """Rebind runtime state when the instance is reused from another event loop."""

        loop = asyncio.get_running_loop()
        if loop is not self._loop:
            self._loop = loop
            self._cache.clear()
            self._slots.clear()
            self._queued.clear()
            self._flush_task = None
            self._batch_tasks.clear()
        return loop

    async def _share(self, awaited: tuple[_TextSlot, ...]) -> None:
        """Wait for every distinct text without letting cancellation reach the provider."""

        futures = [slot.future for slot in awaited]
        # ``asyncio.wait`` observes completion only: cancelling this caller drops its own
        # wait while the shared provider request keeps running for the other waiters.
        await asyncio.wait(futures)
        for future in futures:
            future.result()

    def _enqueue(self, text: str) -> _TextSlot:
        loop = self._loop
        if loop is None:  # pragma: no cover - _bind_loop always runs first
            raise RuntimeError("coalescing embedder is not bound to an event loop")
        slot = _TextSlot()
        slot.future = loop.create_future()
        self._slots[text] = slot
        self._queued.append(text)
        self._schedule_flush()
        return slot

    def _schedule_flush(self) -> None:
        if self._flush_task is not None or not self._queued:
            return
        self._flush_task = asyncio.create_task(self._flush())

    async def _flush(self) -> None:
        """Merge the texts that arrived in the window into bounded provider batches."""

        try:
            # A zero window still yields once so callers made runnable in the same tick
            # join this flush; a positive window additionally absorbs later arrivals.
            await asyncio.sleep(self._coalesce_seconds if self._coalesce_seconds > 0 else 0)
            while self._queued:
                batch = tuple(self._queued[: self._max_batch_size])
                del self._queued[: self._max_batch_size]
                self._dispatch(batch)
        finally:
            self._flush_task = None
            if self._queued:
                self._schedule_flush()

    def _dispatch(self, texts: tuple[str, ...]) -> None:
        self._batch_count += 1
        batch = _ProviderBatch(batch_id=self._batch_count)
        for text in texts:
            slot = self._slots.get(text)
            if slot is not None:
                slot.batch = batch
        task = asyncio.create_task(self._run_batch(texts, batch))
        self._batch_tasks.add(task)
        task.add_done_callback(self._batch_tasks.discard)

    async def _run_batch(self, texts: tuple[str, ...], batch: _ProviderBatch) -> None:
        try:
            result = await self._model.embed(texts)
        except Exception as error:  # routed to every waiter of this batch
            self._settle_failure(texts, error)
            return
        vectors = result.vectors
        if len(vectors) != len(texts):
            self._settle_failure(
                texts,
                ValueError(f"embedding provider returned {len(vectors)} vectors for {len(texts)} texts"),
            )
            return
        batch.usage = result.usage
        if self._cache_size:
            for text, vector in zip(texts, vectors, strict=True):
                self._remember(text, vector)
        for text, vector in zip(texts, vectors, strict=True):
            self._settle(text, vector)

    def _settle_failure(self, texts: tuple[str, ...], error: Exception) -> None:
        for text in texts:
            slot = self._slots.pop(text, None)
            if slot is None or slot.future.done():
                continue
            slot.future.set_exception(error)
            # Consume the traceback bookkeeping so a caller that already gave up cannot log
            # "exception was never retrieved"; awaiting the future still raises the error.
            slot.future.exception()

    def _settle(self, text: str, vector: EmbeddingVector) -> None:
        slot = self._slots.pop(text, None)
        if slot is None or slot.future.done():
            return
        slot.future.set_result(vector)

    def _cached(self, text: str) -> EmbeddingVector | None:
        vector = self._cache.get(text)
        if vector is None:
            return None
        self._cache.move_to_end(text)
        return vector

    def _remember(self, text: str, vector: EmbeddingVector) -> None:
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _usage(self, awaited: tuple[_TextSlot, ...]) -> InferenceUsage:
        batches: dict[int, InferenceUsage] = {}
        for slot in awaited:
            batch = slot.batch
            if batch is not None and batch.usage is not None:
                batches.setdefault(batch.batch_id, batch.usage)
        requests = 0
        input_tokens: int | None = None
        output_tokens: int | None = None
        for usage in batches.values():
            requests += usage.requests
            input_tokens = _add_tokens(input_tokens, usage.input_tokens)
            output_tokens = _add_tokens(output_tokens, usage.output_tokens)
        return InferenceUsage(requests=requests, input_tokens=input_tokens, output_tokens=output_tokens)


def _add_tokens(current: int | None, value: int | None) -> int | None:
    if value is None:
        return current
    return value if current is None else current + value
