"""Transport layer: the only place in beenary that touches raw I/O.

A :class:`Transport` is a random-access byte buffer. Lenses read and write
through it and never touch files, sockets, or paths themselves. Stream-oriented
sources/sinks (:class:`StreamSource`, :class:`StreamSink`) adapt sequential I/O
(``io.BytesIO``, pipes, sockets) to the random-access :class:`Transport`
interface used by the dialogue simulator.

Concrete transports:
    - :class:`MemoryTransport` — backed by a ``bytearray``.
    - :class:`FileTransport` — backed by a file handle (read or read/write).
    - :class:`MmapTransport` — memory-mapped file for large inputs.
    - :class:`MockTransport` — preload incoming bytes, inspect outgoing bytes.
"""

from __future__ import annotations

import io
import mmap
import os
from abc import ABC, abstractmethod
from typing import BinaryIO, cast

from .errors import LensError


class Transport(ABC):
    """Random-access byte buffer used by lenses.

    Implementers must support bounds-checked :meth:`read`/:meth:`write` and
    report their size. Reads beyond end raise :class:`LensError`.
    """

    @abstractmethod
    def size(self) -> int:
        """Return the total number of bytes addressable."""

    @abstractmethod
    def read(self, offset: int, length: int) -> bytes:
        """Return ``length`` bytes starting at ``offset``."""

    @abstractmethod
    def write(self, offset: int, data: bytes) -> None:
        """Write ``data`` starting at ``offset``, extending if needed."""

    def _check_read(self, offset: int, length: int) -> None:
        if offset < 0 or length < 0:
            raise LensError(f"negative offset/length ({offset}, {length})")
        end = offset + length
        if end > self.size():
            raise LensError(
                f"read of {length} bytes at offset {offset} exceeds size {self.size()}"
            )


class MemoryTransport(Transport):
    """Transport backed by an in-memory ``bytearray``."""

    def __init__(self, data: bytes | bytearray = b"") -> None:
        self._buf = bytearray(data)

    def size(self) -> int:
        return len(self._buf)

    def read(self, offset: int, length: int) -> bytes:
        self._check_read(offset, length)
        return bytes(self._buf[offset : offset + length])

    def write(self, offset: int, data: bytes) -> None:
        if offset < 0:
            raise LensError(f"negative offset {offset}")
        end = offset + len(data)
        if end > len(self._buf):
            self._buf.extend(b"\x00" * (end - len(self._buf)))
        self._buf[offset:end] = data

    def to_bytes(self) -> bytes:
        """Return a copy of the underlying bytes."""
        return bytes(self._buf)


class FileTransport(Transport):
    """Transport backed by an open binary file handle."""

    def __init__(self, fh: BinaryIO, *, writable: bool = False) -> None:
        self._fh = fh
        self._writable = writable

    @classmethod
    def open(cls, path: str | os.PathLike[str], mode: str = "rb") -> FileTransport:
        """Open ``path`` and wrap it in a transport."""
        fh = cast(BinaryIO, open(path, mode))  # noqa: SIM115 - lifetime owned by transport
        return cls(fh, writable="+" in mode or "w" in mode or "a" in mode)

    def size(self) -> int:
        cur = self._fh.tell()
        self._fh.seek(0, io.SEEK_END)
        end = self._fh.tell()
        self._fh.seek(cur)
        return end

    def read(self, offset: int, length: int) -> bytes:
        self._check_read(offset, length)
        self._fh.seek(offset)
        data = self._fh.read(length)
        if len(data) != length:
            raise LensError(
                f"short read at offset {offset}: wanted {length}, got {len(data)}"
            )
        return data

    def write(self, offset: int, data: bytes) -> None:
        if not self._writable:
            raise LensError("transport is read-only")
        self._fh.seek(offset)
        self._fh.write(data)
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class MmapTransport(Transport):
    """Transport backed by a memory-mapped file (read-only)."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._fh: BinaryIO = open(path, "rb")  # noqa: SIM115
        self._size = os.fstat(self._fh.fileno()).st_size
        if self._size == 0:
            # Memory-mapping zero bytes is invalid, so back the empty view with
            # a one-byte anonymous map and report size 0. Reads stay blocked by
            # the bounds check.
            self._mmap = mmap.mmap(-1, 1)
        else:
            self._mmap = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)

    def size(self) -> int:
        return self._size

    def read(self, offset: int, length: int) -> bytes:
        self._check_read(offset, length)
        return bytes(self._mmap[offset : offset + length])

    def write(self, offset: int, data: bytes) -> None:
        raise LensError("mmap transport is read-only")

    def close(self) -> None:
        self._mmap.close()
        self._fh.close()


class StreamSource(ABC):
    """Sequential source of inbound bytes (a socket, pipe, or BytesIO)."""

    @abstractmethod
    def read_stream(self, n: int) -> bytes:
        """Read exactly ``n`` bytes, blocking until available."""


class StreamSink(ABC):
    """Sequential sink for outbound bytes."""

    @abstractmethod
    def write_stream(self, data: bytes) -> None:
        """Write ``data`` to the sink."""


class BytesIOSource(StreamSource):
    """A :class:`StreamSource` backed by an ``io.BytesIO``."""

    def __init__(self, data: bytes = b"") -> None:
        self._buf = io.BytesIO(data)

    def read_stream(self, n: int) -> bytes:
        data = self._buf.read(n)
        if len(data) != n:
            raise LensError(f"stream exhausted: wanted {n}, got {len(data)}")
        return data


class BytesIOSink(StreamSink):
    """A :class:`StreamSink` backed by an ``io.BytesIO``."""

    def __init__(self) -> None:
        self._buf = io.BytesIO()

    def write_stream(self, data: bytes) -> None:
        self._buf.write(data)

    def getvalue(self) -> bytes:
        """Return everything written so far."""
        return self._buf.getvalue()


class MockTransport(Transport, StreamSource, StreamSink):
    """A test double usable both as random-access transport and as a stream.

    Incoming bytes are preloaded with :meth:`preload_incoming`; outgoing bytes
    are accumulated and inspected with :meth:`outgoing`.
    """

    def __init__(self, initial: bytes = b"") -> None:
        self._incoming = bytearray(initial)
        self._outgoing = bytearray()

    def preload_incoming(self, data: bytes) -> None:
        """Append ``data`` to the inbound buffer."""
        self._incoming.extend(data)

    def outgoing(self) -> bytes:
        """Return (and keep) all bytes written so far."""
        return bytes(self._outgoing)

    # --- StreamSource / StreamSink ---
    def read_stream(self, n: int) -> bytes:
        if n > len(self._incoming):
            raise LensError(f"inbound buffer exhausted: wanted {n}, got {len(self._incoming)}")
        data = bytes(self._incoming[:n])
        del self._incoming[:n]
        return data

    def write_stream(self, data: bytes) -> None:
        self._outgoing.extend(data)

    # --- Transport ---
    def size(self) -> int:
        return len(self._incoming)

    def read(self, offset: int, length: int) -> bytes:
        self._check_read(offset, length)
        return bytes(self._incoming[offset : offset + length])

    def write(self, offset: int, data: bytes) -> None:
        # Writes to a mock land in the outgoing buffer.
        self._outgoing.extend(data)
