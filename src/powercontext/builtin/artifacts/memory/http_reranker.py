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

"""HTTP cross-encoder reranking over one coarse Memory candidate pool.

The remote service scores query/candidate pairs but caps how many candidates one request may
carry, so the pool is split into bounded batches. Batches never renumber candidates: every
request carries the candidate's one-based rank in the *complete* input, which is what lets the
local side stitch the per-batch scores back into one global ordering.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

HTTP_MEMORY_RERANK_POLICY_ID = "http-cross-encoder-v1"
DEFAULT_HTTP_RERANK_BATCH_SIZE = 32


class HTTPMemoryReranker:
    """Score Memory candidates with one batch-bounded HTTP cross-encoder service."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = DEFAULT_HTTP_RERANK_BATCH_SIZE,
        policy_id: str = HTTP_MEMORY_RERANK_POLICY_ID,
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("rerank endpoint must be a non-empty URL")  # noqa: TRY003 - Public argument contract.
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("rerank batch_size must be a positive integer")  # noqa: TRY003
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise ValueError("rerank policy_id must be a non-empty identifier")  # noqa: TRY003
        self.policy_id = policy_id
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size

    async def rerank(
        self,
        query: str,
        candidates: Sequence[MemoryHit],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score every candidate exactly once and return the global stable top ``limit``.

        Selection is decided over the complete pool, so a later batch's high score competes
        with an earlier batch's equally high score on the score itself and then on the
        original global rank, never on the batch that happened to carry it.
        """

        candidate_count = len(candidates)
        _validate_bounds(candidate_count, limit)
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, candidate_count, self._batch_size):
            batch = candidates[start : start + self._batch_size]
            ranks = tuple(range(start + 1, start + len(batch) + 1))
            response = await self._client.post(
                self._endpoint,
                json={
                    "query": query,
                    "candidates": [
                        {"rank": rank, "text": candidate.text}
                        for rank, candidate in zip(ranks, batch, strict=True)
                    ],
                },
            )
            requests += 1
            response.raise_for_status()
            scores.update(_parse_batch_scores(response, expected_ranks=ranks))
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return MemoryRerankDecision(
            selected_ranks=tuple(rank for rank, _ in ordered[:limit]),
            usage=InferenceUsage(requests=requests),
        )


def _parse_batch_scores(
    response: httpx.Response,
    *,
    expected_ranks: tuple[int, ...],
) -> dict[int, float]:
    """Validate one batch response and return it keyed by its original global ranks."""

    payload = _decode_object(response)
    if set(payload) != {"scores"}:
        raise ValueError("rerank response must contain exactly the scores field")  # noqa: TRY003
    records = payload["scores"]
    if not isinstance(records, list):
        raise ValueError("rerank response scores must be a list")  # noqa: TRY003
    expected = set(expected_ranks)
    scores: dict[int, float] = {}
    for record in records:
        rank, score = _decode_score_record(record)
        if rank not in expected:
            raise ValueError(f"rerank response rank {rank} is outside this batch")  # noqa: TRY003
        if rank in scores:
            raise ValueError(f"rerank response repeated rank {rank}")  # noqa: TRY003
        scores[rank] = score
    if scores.keys() != expected:
        missing = sorted(expected.difference(scores))
        raise ValueError(f"rerank response omitted ranks {missing}")  # noqa: TRY003
    return scores


def _decode_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as error:
        raise ValueError("rerank response is not valid JSON") from error  # noqa: TRY003
    if not isinstance(payload, dict):
        raise ValueError("rerank response must be a JSON object")  # noqa: TRY003
    return payload


def _decode_score_record(record: Any) -> tuple[int, float]:
    if not isinstance(record, dict) or set(record) != {"rank", "score"}:
        raise ValueError("rerank score records must contain exactly rank and score")  # noqa: TRY003
    rank = record["rank"]
    if type(rank) is not int:
        raise ValueError("rerank response rank must be an integer")  # noqa: TRY003
    score = record["score"]
    if type(score) is not int and type(score) is not float:
        raise ValueError("rerank response score must be a number")  # noqa: TRY003
    value = float(score)
    if not math.isfinite(value):
        raise ValueError("rerank response score must be finite")  # noqa: TRY003
    return rank, value


def _validate_bounds(candidate_count: int, limit: int) -> None:
    if candidate_count < 1:
        raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
    if limit < 1 or limit > candidate_count:
        raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003


__all__ = [
    "DEFAULT_HTTP_RERANK_BATCH_SIZE",
    "HTTP_MEMORY_RERANK_POLICY_ID",
    "HTTPMemoryReranker",
]
