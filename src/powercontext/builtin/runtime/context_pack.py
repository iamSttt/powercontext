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

"""Self-verifying context packs and prefix-reuse deltas for repeated prepare calls.

A :class:`ContextPack` is the exact UTF-8 bytes of one prepared context split into
bounded, valid-UTF-8 chunks. The wire form is canonical JSON (sorted keys, no extra
whitespace) whose chunks are standard Base64, and whose digest binds only ``schema``
and ``items`` so that a receiver can verify the content without trusting the trailing
bookkeeping fields. :class:`ContextPackDelta` reuses a common chunk prefix of a pack
the receiver already holds and carries only the target suffix, so consecutive
prepares stop re-sending identical bytes.

Everything here is pure and process-local: a receiver reconstructs the target pack
from the serialized base pack and the serialized delta alone.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512

_DIGEST_PREFIX = "sha256:"
_DIGEST_HEX_LENGTH = 64
_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})
_UTF8_CONTINUATION_MASK = 0xC0
_UTF8_CONTINUATION_BYTE = 0x80
_BYTE_COUNT_FIXED_POINT_LIMIT = 8


class ContextPackError(ValueError):
    """A context pack or delta violated the wire protocol or the chunk rules."""


def _canonical(payload: Mapping[str, object]) -> bytes:
    """Render one wire object exactly as the receiver-side contract expects."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _content_digest(unsigned: Mapping[str, object]) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(_canonical(unsigned)).hexdigest()


def _encode_items(items: Iterable[bytes]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(value: object, *, field: str) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError(f"{field} must be an array of Base64 strings")  # noqa: TRY003
    decoded: list[bytes] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ContextPackError(f"{field} entries must be Base64 strings")  # noqa: TRY003
        try:
            decoded.append(base64.b64decode(entry, validate=True))
        except (binascii.Error, ValueError) as error:
            raise ContextPackError(f"{field} contains invalid Base64") from error  # noqa: TRY003
    return tuple(decoded)


def _as_chunk(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray | memoryview):
        return bytes(value)
    raise ContextPackError("context pack chunks must be bytes")  # noqa: TRY003


def _require_object(data: object, *, what: str) -> Mapping[str, object]:
    if not isinstance(data, bytes | bytearray | memoryview):
        raise ContextPackError(f"{what} must be UTF-8 JSON bytes")  # noqa: TRY003
    try:
        parsed = json.loads(bytes(data))
    except (UnicodeDecodeError, ValueError) as error:
        raise ContextPackError(f"{what} is not valid JSON") from error  # noqa: TRY003
    if not isinstance(parsed, dict):
        raise ContextPackError(f"{what} must be a JSON object")  # noqa: TRY003
    return parsed


def _require_exact_fields(payload: Mapping[str, object], expected: frozenset[str], *, what: str) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ContextPackError(f"{what} fields mismatch: missing={missing} extra={extra}")  # noqa: TRY003


def _require_digest(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ContextPackError(f"{field} must be a digest string")  # noqa: TRY003
    if not value.startswith(_DIGEST_PREFIX):
        raise ContextPackError(f"{field} must start with {_DIGEST_PREFIX}")  # noqa: TRY003
    hexdigest = value[len(_DIGEST_PREFIX) :]
    if len(hexdigest) != _DIGEST_HEX_LENGTH or any(char not in "0123456789abcdef" for char in hexdigest):
        raise ContextPackError(f"{field} must be {_DIGEST_HEX_LENGTH} lowercase hex digits")  # noqa: TRY003
    return value


def _require_count(value: object, *, field: str) -> int:
    """Reject bools and floats: a wire count is exactly a JSON integer, never a stand-in."""

    if type(value) is not int or isinstance(value, bool) or value < 0:
        raise ContextPackError(f"{field} must be a non-negative integer")  # noqa: TRY003
    return value


def _self_including_length(payload: Mapping[str, object]) -> int:
    """Find the byte count that makes ``byte_count`` equal the rendered document length."""

    guess = 0
    for _ in range(_BYTE_COUNT_FIXED_POINT_LIMIT):
        rendered = len(_canonical({**payload, "byte_count": guess}))
        if rendered == guess:
            return guess
        guess = rendered
    raise ContextPackError("byte_count does not reach a fixed point")  # noqa: TRY003


def _chunk_utf8(data: bytes, limit: int) -> tuple[bytes, ...]:
    """Split ``data`` into deterministic chunks of valid UTF-8 no longer than ``limit``."""

    boundaries = [0]
    for index in range(1, len(data)):
        if data[index] & _UTF8_CONTINUATION_MASK != _UTF8_CONTINUATION_BYTE:
            boundaries.append(index)
    boundaries.append(len(data))
    chunks: list[bytes] = []
    start = 0
    while start < len(data):
        if len(data) - start <= limit:
            chunks.append(data[start:])
            break
        end = boundaries[bisect_right(boundaries, start + limit) - 1]
        if end <= start:
            raise ContextPackError(f"no valid UTF-8 boundary within {limit} bytes")  # noqa: TRY003
        chunks.append(data[start:end])
        start = end
    return tuple(chunks)


@dataclass(frozen=True)
class ContextPack:
    """One complete context body as bounded, verified UTF-8 chunks."""

    items: tuple[bytes, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(_as_chunk(item) for item in self.items))

    @classmethod
    def build(cls, items: Iterable[bytes]) -> ContextPack:
        """Build a complete pack from already-chosen chunks."""

        return cls(tuple(items))

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Split ``content`` deterministically, preserving its exact UTF-8 bytes."""

        if type(chunk_bytes) is not int or isinstance(chunk_bytes, bool) or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        try:
            data = content.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ContextPackError("content is not encodable as UTF-8") from error  # noqa: TRY003
        if not data:
            return cls(())
        return cls(_chunk_utf8(data, chunk_bytes))

    @property
    def digest(self) -> str:
        """Digest over ``schema`` and ``items`` only, so bookkeeping cannot mask edits."""

        return _content_digest(self._unsigned())

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        unsigned = self._unsigned()
        payload: dict[str, object] = {**unsigned, "digest": _content_digest(unsigned)}
        payload["byte_count"] = _self_including_length(payload)
        return _canonical(payload)

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        """Decode and fully verify one complete pack."""

        payload = _require_object(data, what="context pack")
        _require_exact_fields(payload, _PACK_FIELDS, what="context pack")
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError(f"context pack schema must be {PACK_SCHEMA}")  # noqa: TRY003
        pack = cls.build(_decode_items(payload["items"], field="context pack items"))
        if _require_digest(payload["digest"], field="context pack digest") != pack.digest:
            raise ContextPackError("context pack digest does not match its items")  # noqa: TRY003
        if _require_count(payload["byte_count"], field="context pack byte_count") != pack.byte_count:
            raise ContextPackError("context pack byte_count does not match its encoding")  # noqa: TRY003
        return pack

    def _unsigned(self) -> dict[str, object]:
        return {"schema": PACK_SCHEMA, "items": _encode_items(self.items)}


@dataclass(frozen=True)
class ContextPackDelta:
    """A reused base-prefix plus the target suffix needed to rebuild the target pack."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        _require_digest(self.base_digest, field="delta base_digest")
        _require_digest(self.target_digest, field="delta target_digest")
        _require_count(self.prefix_items, field="delta prefix_items")
        object.__setattr__(self, "appended", tuple(_as_chunk(item) for item in self.appended))

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Keep the longest valid common chunk prefix and carry the remaining target chunks."""

        prefix_items = 0
        for base_item, target_item in zip(base.items, target.items, strict=False):
            if base_item != target_item:
                break
            prefix_items += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix_items,
            appended=target.items[prefix_items:],
        )

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _canonical({
            "schema": DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode_items(self.appended),
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        """Decode and structurally verify one delta; base and target are checked on apply."""

        payload = _require_object(data, what="context pack delta")
        _require_exact_fields(payload, _DELTA_FIELDS, what="context pack delta")
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError(f"context pack delta schema must be {DELTA_SCHEMA}")  # noqa: TRY003
        return cls(
            base_digest=_require_digest(payload["base_digest"], field="delta base_digest"),
            target_digest=_require_digest(payload["target_digest"], field="delta target_digest"),
            prefix_items=_require_count(payload["prefix_items"], field="delta prefix_items"),
            appended=_decode_items(payload["appended"], field="context pack delta appended"),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild the target pack from a held base pack and one received delta."""

    if base.digest != delta.base_digest:
        raise ContextPackError("delta base_digest does not match the held base pack")  # noqa: TRY003
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix_items exceeds the base pack chunk count")  # noqa: TRY003
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target_digest does not match the rebuilt pack")  # noqa: TRY003
    return target


__all__ = [
    "DEFAULT_CHUNK_BYTES",
    "DELTA_SCHEMA",
    "PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
