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

Stream adapters:
    - :class:`BytesIOSource` / :class:`BytesIOSink` — ``io.BytesIO``.
    - :class:`SocketStream` — a connected socket.
"""

from __future__ import annotations

import io
import mmap
import os
import socket
from abc import ABC, abstractmethod
from types import TracebackType
from typing import BinaryIO, Protocol, cast

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

    def close(self) -> None:  # noqa: B027 - optional hook, no cleanup for memory
        """Release any resources held by the transport.

        In-memory transports keep their bytes; file-backed transports close
        their handle.
        """

    # Basic checks of a read function.
    def _check_read(self, offset: int, length: int) -> None:
        # TODO (@result): Add an option to return basic exceptions instead of raising them.
        # Better when using type safe-ish Python's mypy.
        # But unknowing API user could accidentally ignore the error, hence why only an option
        # If we used Rust or something like that, we'd consider using Result<T, U>
        if offset < 0 or length < 0:
            raise LensError(f"negative offset/length ({offset}, {length})") # @result
        end = offset + length
        if end > self.size():
            raise LensError(
                f"read of {length} bytes at offset {offset} exceeds size {self.size()}"
            ) # @result


class MemoryTransport(Transport):
    """Transport backed by an in-memory ``bytearray`` type."""

    def __init__(self, data: bytes | bytearray = b"") -> None:
        self._buf = bytearray(data)

    def size(self) -> int:
        return len(self._buf)

    def read(self, offset: int, length: int) -> bytes:
        self._check_read(offset, length)
        return bytes(self._buf[offset : offset + length])

    def write(self, offset: int, data: bytes) -> None:
        if offset < 0:
            raise LensError(f"negative offset {offset}") # @result
        end = offset + len(data)
        if end > len(self._buf):
            self._buf.extend(b"\x00" * (end - self.size()))
        self._buf[offset:end] = data

    def to_bytes(self) -> bytes:
        """Return a copy of the underlying bytes."""
        return bytes(self._buf)


class FileTransport(Transport):
    """Transport backed by an open binary file handle."""

    def __init__(self, fh: BinaryIO, *, writable: bool = False) -> None:
        self._file_handle = fh
        self._writable = writable

    @classmethod
    def open(cls, path: str | os.PathLike[str], mode: str = "rb") -> FileTransport:
        """Open ``path`` and wrap it in a transport."""
        fh = cast(BinaryIO, open(path, mode))  # noqa: SIM115 - lifetime owned by transport
        return cls(fh, writable="+" in mode or "w" in mode or "a" in mode)

    def size(self) -> int:
        cur = self._file_handle.tell()
        self._file_handle.seek(0, io.SEEK_END)
        end = self._file_handle.tell()
        self._file_handle.seek(cur)
        return end

    def read(self, offset: int, length: int) -> bytes:
        self._check_read(offset, length)
        self._file_handle.seek(offset)
        data = self._file_handle.read(length)
        if len(data) != length:
            raise LensError(
                f"short read at offset {offset}: wanted {length}, got {len(data)}"
            ) # @result
        return data

    def write(self, offset: int, data: bytes) -> None:
        if not self._writable:
            raise LensError("transport is read-only") # @result
        self._file_handle.seek(offset)
        self._file_handle.write(data)
        self._file_handle.flush()

    def close(self) -> None:
        self._file_handle.close()


class MmapTransport(Transport):
    """Transport backed by a memory-mapped file (read-only)."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._file_handle: BinaryIO = open(path, "rb")  # noqa: SIM115 - lifetime owned by transport
        self._size = os.fstat(self._file_handle.fileno()).st_size
        if self._size == 0:
            # Memory-mapping zero bytes is invalid, so back the empty view with
            # a one-byte anonymous map and report size 0. Reads stay blocked by
            # the bounds check.
            self._mmap = mmap.mmap(-1, 1)
        else:
            self._mmap = mmap.mmap(self._file_handle.fileno(), 0, access=mmap.ACCESS_READ)

    def size(self) -> int:
        return self._size

    def read(self, offset: int, length: int) -> bytes:
        self._check_read(offset, length)
        return bytes(self._mmap[offset : offset + length])

    def write(self, offset: int, data: bytes) -> None:
        raise LensError("mmap transport is read-only") # @result

    def close(self) -> None:
        self._mmap.close()
        self._file_handle.close()


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
            raise LensError(f"stream exhausted: wanted {n}, got {len(data)}") # @result
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


class SocketLike(Protocol):
    """The slice of ``socket.socket`` that :class:`SocketStream` uses.

    Typing the adapter against this protocol keeps it testable with any object
    exposing ``recv``, ``sendall``, and ``close``.
    """

    def recv(self, bufsize: int) -> bytes:
        """Receive up to ``bufsize`` bytes; return ``b""`` when the peer closes."""

    def sendall(self, data: bytes) -> None:
        """Send all of ``data``."""

    def close(self) -> None:
        """Close the connection."""


class SocketStream(StreamSource, StreamSink):
    """A connected socket exposed as a readable and writable byte stream.

    ``recv`` may return fewer bytes than requested, so :meth:`read_stream`
    loops until the full message is in hand. A closed socket is reported as
    :class:`LensError`, the same way any other short read is.
    """

    def __init__(self, sock: SocketLike) -> None:
        self._socket = sock

    @classmethod
    def connect(
        cls, host: str, port: int, *, timeout: float | None = None
    ) -> SocketStream:
        """Connect a TCP socket and wrap it in a stream."""
        return cls(socket.create_connection((host, port), timeout))

    def read_stream(self, n: int) -> bytes:
        if n < 0:
            raise LensError(f"negative read length {n}") # @result
        chunks: list[bytes] = []
        remaining = n
        while remaining:
            chunk = self._socket.recv(remaining)
            if not chunk:
                raise LensError(
                    f"socket closed: wanted {n} bytes, got {n - remaining}"
                ) # @result
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def write_stream(self, data: bytes) -> None:
        self._socket.sendall(data)

    def close(self) -> None:
        """Close the underlying socket."""
        self._socket.close()

    def __enter__(self) -> SocketStream:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


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
            # @result
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
