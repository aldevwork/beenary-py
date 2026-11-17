"""Shared helpers for the beenary test suite."""

from __future__ import annotations

from typing import Any

from beenary.transport import MemoryTransport


def roundtrip_bytes(lens: Any, data: bytes) -> bytes:
    """Read ``data`` with ``lens``, then write the value back to fresh bytes."""
    src = MemoryTransport(data)
    value, _ = lens.read(src, 0)
    dst = MemoryTransport()
    lens.write(dst, 0, value)
    return dst.to_bytes()
