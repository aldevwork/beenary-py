"""Tests for primitive lenses: numerics, Magic, Bytes and Padding."""

from __future__ import annotations

import struct

import pytest

from beenary.errors import LensError
from beenary.lens import (
    F32,
    F64,
    I8,
    I16,
    I32,
    I64,
    U8,
    U16,
    U32,
    U64,
    Bytes,
    Magic,
    Padding,
    Primitives,
)
from beenary.transport import MemoryTransport

from tests.conftest import roundtrip_bytes

NUMERIC_CASES = [
    (U8, 0),
    (U8, 255),
    (U16, 0),
    (U16, 65535),
    (U32, 0),
    (U32, 2**32 - 1),
    (U64, 2**64 - 1),
    (I8, -128),
    (I8, 127),
    (I16, -32768),
    (I32, -2**31),
    (I64, -2**63),
    (F32, 1.5),
    (F64, -3.25),
]


@pytest.mark.parametrize("lens_cls,value", NUMERIC_CASES)
def test_numeric_roundtrip(lens_cls: type[Primitives], value: object) -> None:
    lens = lens_cls()
    transport = MemoryTransport()
    next_offset = lens.write(transport, 0, value)
    assert next_offset == lens.size()
    read_value, read_next = lens.read(transport, 0)
    assert read_value == value
    assert read_next == next_offset


@pytest.mark.parametrize("lens_cls,value", NUMERIC_CASES)
def test_numeric_roundtrip_via_helper(lens_cls: type[Primitives], value: object) -> None:
    lens = lens_cls()
    packed = struct.pack(lens.endian + lens.code, value)
    assert roundtrip_bytes(lens, packed) == packed


@pytest.mark.parametrize(
    "lens_cls,value",
    [(U16, 0x1234), (U32, 0x12345678), (U64, 0x123456789ABCDEF0), (F64, 1.5)],
)
def test_big_endian_choice(lens_cls: type[Primitives], value: object) -> None:
    little = lens_cls("<")
    big = lens_cls(">")
    assert little.endian == "<"
    assert big.endian == ">"

    small = MemoryTransport()
    large = MemoryTransport()
    little.write(small, 0, value)
    big.write(large, 0, value)
    assert small.to_bytes() == struct.pack("<" + lens_cls.code, value)
    assert large.to_bytes() == struct.pack(">" + lens_cls.code, value)
    assert big.read(large, 0)[0] == value


def test_invalid_endian_raises() -> None:
    with pytest.raises(ValueError, match="endian"):
        U16("^")


def test_size_matches_struct_calcsize() -> None:
    for lens_cls in (U8, U16, U32, U64, I8, I16, I32, I64, F32, F64):
        lens = lens_cls()
        assert lens.size() == struct.calcsize("<" + lens.code)
        assert lens.width == lens.size()


def test_out_of_range_value_raises() -> None:
    with pytest.raises(LensError, match="cannot pack"):
        U8().write(MemoryTransport(), 0, 256)


def test_short_read_raises_lens_error() -> None:
    with pytest.raises(LensError, match="exceeds size"):
        U32().read(MemoryTransport(b"\x00"), 0)


def test_repr_includes_endian() -> None:
    assert repr(U16()) == "U16(endian='<')"
    assert repr(I32(">")) == "I32(endian='>')"


# --------------------------------------------------------------------------- #
# Magic
# --------------------------------------------------------------------------- #


class TestMagic:
    def test_read_matching_sentinel(self) -> None:
        magic = Magic(b"BEEN")
        value, offset = magic.read(MemoryTransport(b"BEENrest"), 0)
        assert value == b"BEEN"
        assert offset == 4
        assert magic.size() == 4

    def test_read_mismatch_raises(self) -> None:
        with pytest.raises(LensError, match="magic mismatch"):
            Magic(b"BEEN").read(MemoryTransport(b"BAAD"), 0)

    def test_write_sentinel(self) -> None:
        magic = Magic(b"BEEN")
        transport = MemoryTransport()
        assert magic.write(transport, 0, b"BEEN") == 4
        assert transport.to_bytes() == b"BEEN"

    def test_write_none_uses_sentinel(self) -> None:
        transport = MemoryTransport()
        Magic(b"XY").write(transport, 0, None)
        assert transport.to_bytes() == b"XY"

    def test_write_wrong_value_raises(self) -> None:
        with pytest.raises(LensError, match="magic write value"):
            Magic(b"BEEN").write(MemoryTransport(), 0, b"BAAD")

    def test_sentinel_must_be_bytes(self) -> None:
        with pytest.raises(TypeError, match="sentinel must be bytes"):
            Magic("BEEN")  # type: ignore[arg-type]

    def test_bytearray_sentinel_is_copied(self) -> None:
        source = bytearray(b"XY")
        magic = Magic(source)
        source[0] = ord("Z")
        assert magic.sentinel == b"XY"

    def test_roundtrip(self) -> None:
        assert roundtrip_bytes(Magic(b"BEEN"), b"BEEN") == b"BEEN"

    def test_repr(self) -> None:
        assert repr(Magic(b"AB")) == "Magic(b'AB')"


# --------------------------------------------------------------------------- #
# Bytes
# --------------------------------------------------------------------------- #


class TestBytes:
    def test_read_fixed_length(self) -> None:
        lens = Bytes(3)
        value, offset = lens.read(MemoryTransport(b"abcde"), 1)
        assert value == b"bcd"
        assert offset == 4
        assert lens.size() == 3

    def test_write_bytes_and_bytearray(self) -> None:
        lens = Bytes(2)
        transport = MemoryTransport()
        assert lens.write(transport, 0, b"ab") == 2
        transport.write(2, b"")
        transport.write(2, b"cd")
        lens.write(transport, 2, bytearray(b"cd"))
        assert transport.to_bytes() == b"abcd"

    def test_wrong_type_raises(self) -> None:
        with pytest.raises(LensError, match="expected bytes"):
            Bytes(2).write(MemoryTransport(), 0, "ab")

    def test_wrong_length_raises(self) -> None:
        with pytest.raises(LensError, match="expected 2 bytes"):
            Bytes(2).write(MemoryTransport(), 0, b"abc")

    def test_roundtrip(self) -> None:
        assert roundtrip_bytes(Bytes(3), b"abc") == b"abc"

    def test_repr(self) -> None:
        assert repr(Bytes(3)) == "Bytes(3)"


# --------------------------------------------------------------------------- #
# Padding
# --------------------------------------------------------------------------- #


class TestPadding:
    def test_read_returns_raw_bytes(self) -> None:
        value, offset = Padding(2).read(MemoryTransport(b"\xaa\xbb"), 0)
        assert value == b"\xaa\xbb"
        assert offset == 2

    def test_write_fills_with_zero_by_default(self) -> None:
        transport = MemoryTransport(b"existing")
        assert Padding(3).write(transport, 0, b"ignored") == 3
        assert transport.to_bytes() == b"\x00\x00\x00sting"

    def test_write_custom_fill_byte(self) -> None:
        transport = MemoryTransport()
        Padding(2, fill_byte=0xAA).write(transport, 0, None)
        assert transport.to_bytes() == b"\xaa\xaa"

    def test_size_and_repr(self) -> None:
        lens = Padding(4)
        assert lens.size() == 4
        assert repr(lens) == "Padding(4)"

    def test_write_discards_padding_content(self) -> None:
        # Padding is output-only: reading exposes the bytes, writing refills
        # them with the configured fill byte.
        assert roundtrip_bytes(Padding(2), b"\x11\x22") == b"\x00\x00"
