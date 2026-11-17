"""Tests for the transport layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from beenary.errors import LensError
from beenary.transport import (
    BytesIOSink,
    BytesIOSource,
    FileTransport,
    MemoryTransport,
    MmapTransport,
    MockTransport,
    StreamSink,
    StreamSource,
    Transport,
)

# --------------------------------------------------------------------------- #
# Abstract interfaces
# --------------------------------------------------------------------------- #


def test_transport_is_abstract() -> None:
    with pytest.raises(TypeError):
        Transport()  # type: ignore[abstract]


def test_stream_bases_are_abstract() -> None:
    with pytest.raises(TypeError):
        StreamSource()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        StreamSink()  # type: ignore[abstract]


# --------------------------------------------------------------------------- #
# MemoryTransport
# --------------------------------------------------------------------------- #


class TestMemoryTransport:
    def test_default_is_empty(self) -> None:
        assert MemoryTransport().size() == 0
        assert MemoryTransport().to_bytes() == b""

    def test_initial_data_is_copied(self) -> None:
        source = bytearray(b"abc")
        transport = MemoryTransport(source)
        source[0] = ord("z")
        assert transport.read(0, 3) == b"abc"
        assert transport.size() == 3

    def test_read_sub_range(self) -> None:
        transport = MemoryTransport(b"abcdef")
        assert transport.read(2, 3) == b"cde"
        assert transport.read(6, 0) == b""

    def test_read_out_of_bounds_raises(self) -> None:
        transport = MemoryTransport(b"abc")
        with pytest.raises(LensError, match="exceeds size 3"):
            transport.read(2, 2)

    def test_negative_offset_raises(self) -> None:
        with pytest.raises(LensError, match="negative offset/length"):
            MemoryTransport(b"abc").read(-1, 1)

    def test_negative_length_raises(self) -> None:
        with pytest.raises(LensError, match="negative offset/length"):
            MemoryTransport(b"abc").read(0, -1)

    def test_write_overwrites(self) -> None:
        transport = MemoryTransport(b"abcdef")
        transport.write(2, b"XY")
        assert transport.read(0, 6) == b"abXYef"

    def test_write_extends_with_zeroes(self) -> None:
        transport = MemoryTransport(b"ab")
        transport.write(4, b"cd")
        assert transport.to_bytes() == b"ab\x00\x00cd"

    def test_write_negative_offset_raises(self) -> None:
        with pytest.raises(LensError, match="negative offset"):
            MemoryTransport().write(-1, b"x")

    def test_to_bytes_returns_copy(self) -> None:
        transport = MemoryTransport(b"abc")
        snapshot = transport.to_bytes()
        transport.write(0, b"z")
        assert snapshot == b"abc"
        assert transport.to_bytes() == b"zbc"


# --------------------------------------------------------------------------- #
# FileTransport
# --------------------------------------------------------------------------- #


class TestFileTransport:
    def test_read_file(self, tmp_path: Path) -> None:
        path = tmp_path / "data.bin"
        path.write_bytes(b"hello world")
        transport = FileTransport.open(path)
        try:
            assert transport.size() == 11
            assert transport.read(0, 5) == b"hello"
            assert transport.read(6, 5) == b"world"
        finally:
            transport.close()

    def test_read_out_of_bounds(self, tmp_path: Path) -> None:
        path = tmp_path / "data.bin"
        path.write_bytes(b"hello")
        transport = FileTransport.open(path)
        try:
            with pytest.raises(LensError, match="exceeds size 5"):
                transport.read(3, 5)
        finally:
            transport.close()

    def test_write_requires_writable_mode(self, tmp_path: Path) -> None:
        path = tmp_path / "data.bin"
        path.write_bytes(b"hello")
        transport = FileTransport.open(path, "rb")
        try:
            assert transport.size() == 5
            with pytest.raises(LensError, match="read-only"):
                transport.write(0, b"x")
        finally:
            transport.close()

    def test_write_in_write_mode(self, tmp_path: Path) -> None:
        path = tmp_path / "data.bin"
        transport = FileTransport.open(path, "wb")
        try:
            transport.write(0, b"abc")
            transport.write(1, b"Z")
        finally:
            transport.close()
        assert path.read_bytes() == b"aZc"

    def test_write_beyond_end_extends_file(self, tmp_path: Path) -> None:
        path = tmp_path / "data.bin"
        transport = FileTransport.open(path, "wb")
        try:
            transport.write(3, b"x")
        finally:
            transport.close()
        assert path.read_bytes() == b"\x00\x00\x00x"

    def test_writable_flag_for_append_and_plus_modes(self, tmp_path: Path) -> None:
        path = tmp_path / "data.bin"
        path.write_bytes(b"")
        for mode in ("r+b", "w+b", "ab"):
            transport = FileTransport.open(path, mode)
            try:
                transport.write(0, b"ok")
            finally:
                transport.close()


# --------------------------------------------------------------------------- #
# MmapTransport
# --------------------------------------------------------------------------- #


class TestMmapTransport:
    def test_read_mapped_file(self, tmp_path: Path) -> None:
        path = tmp_path / "data.bin"
        path.write_bytes(b"mmap-data")
        transport = MmapTransport(path)
        try:
            assert transport.size() == 9
            assert transport.read(0, 4) == b"mmap"
            assert transport.read(5, 4) == b"data"
            with pytest.raises(LensError, match="exceeds size 9"):
                transport.read(8, 2)
        finally:
            transport.close()

    def test_write_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "data.bin"
        path.write_bytes(b"data")
        transport = MmapTransport(path)
        try:
            with pytest.raises(LensError, match="read-only"):
                transport.write(0, b"x")
        finally:
            transport.close()

    def test_empty_file_is_supported(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.bin"
        path.write_bytes(b"")
        transport = MmapTransport(path)
        try:
            assert transport.size() == 0
            assert transport.read(0, 0) == b""
        finally:
            transport.close()


# --------------------------------------------------------------------------- #
# Stream adapters
# --------------------------------------------------------------------------- #


class TestBytesIOAdapters:
    def test_source_reads_exactly_requested(self) -> None:
        source = BytesIOSource(b"abcdef")
        assert source.read_stream(2) == b"ab"
        assert source.read_stream(3) == b"cde"
        assert source.read_stream(1) == b"f"

    def test_source_exhaustion_raises(self) -> None:
        source = BytesIOSource(b"ab")
        with pytest.raises(LensError, match="stream exhausted"):
            source.read_stream(3)

    def test_sink_collects_writes(self) -> None:
        sink = BytesIOSink()
        sink.write_stream(b"ab")
        sink.write_stream(b"cd")
        assert sink.getvalue() == b"abcd"

    def test_sink_starts_empty(self) -> None:
        assert BytesIOSink().getvalue() == b""


# --------------------------------------------------------------------------- #
# MockTransport
# --------------------------------------------------------------------------- #


class TestMockTransport:
    def test_preload_extends_incoming(self) -> None:
        mock = MockTransport(b"ab")
        mock.preload_incoming(b"cd")
        assert mock.size() == 4
        assert mock.read(2, 2) == b"cd"

    def test_read_stream_consumes_incoming(self) -> None:
        mock = MockTransport(b"abcdef")
        assert mock.read_stream(2) == b"ab"
        assert mock.size() == 4
        assert mock.read(0, 4) == b"cdef"

    def test_read_stream_exhaustion_raises(self) -> None:
        mock = MockTransport(b"ab")
        with pytest.raises(LensError, match="inbound buffer exhausted"):
            mock.read_stream(3)

    def test_write_stream_and_transport_write_go_to_outgoing(self) -> None:
        mock = MockTransport(b"in")
        mock.write_stream(b"out-")
        mock.write(0, b"more")
        assert mock.outgoing() == b"out-more"
        # Incoming bytes are untouched by writes.
        assert mock.read(0, 2) == b"in"
        assert mock.size() == 2

    def test_read_out_of_bounds_raises(self) -> None:
        with pytest.raises(LensError):
            MockTransport(b"a").read(0, 2)
