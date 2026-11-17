"""Tests for composite lenses: Array and Struct."""

from __future__ import annotations

import pytest

from beenary.errors import LensError
from beenary.lens import Array, Bytes, Magic, Struct, U8, U16, U32, U64
from beenary.transport import MemoryTransport

from tests.conftest import roundtrip_bytes


# --------------------------------------------------------------------------- #
# Array
# --------------------------------------------------------------------------- #


class TestArrayConstruction:
    def test_accepts_bare_lens_class_as_item(self) -> None:
        assert isinstance(Array(U8).item, U8)

    def test_accepts_lens_instance(self) -> None:
        item = U16(">")
        assert Array(item).item is item

    def test_rejects_non_lens_item(self) -> None:
        with pytest.raises(TypeError, match="Array item must be a Lens"):
            Array(42)

    def test_rejects_invalid_count_type(self) -> None:
        with pytest.raises(TypeError, match="count must be"):
            Array(U8, "3")

    def test_rejects_negative_fixed_count(self) -> None:
        with pytest.raises(ValueError, match="count must be non-negative"):
            Array(U8, -1)

    def test_width_comes_from_item(self) -> None:
        assert Array(U8).width == 1
        assert Array(Bytes(4)).width == 4

    def test_repr(self) -> None:
        assert repr(Array(U8, 2)).startswith("Array(U8(endian='<'), count=2)")


class TestArraySizing:
    def test_fixed_count_size(self) -> None:
        assert Array(U16, 3).size() == 6

    def test_size_is_none_for_dynamic_counts(self) -> None:
        assert Array(U16).size() is None
        assert Array(U16, count=lambda ctx: 3).size() is None

    def test_size_is_none_for_variable_width_items(self) -> None:
        assert Array(Struct(x=Array(U8)), 2).size() is None


class TestArrayRead:
    def test_fixed_count_read(self) -> None:
        values, offset = Array(U8, 3).read(MemoryTransport(b"\x01\x02\x03"), 0)
        assert values == [1, 2, 3]
        assert offset == 3

    def test_fixed_count_roundtrip(self) -> None:
        assert roundtrip_bytes(Array(U16, 2), b"\x01\x00\x02\x00") == b"\x01\x00\x02\x00"

    def test_read_to_end_when_count_is_none(self) -> None:
        values, offset = Array(U8).read(MemoryTransport(b"\x01\x02\x03"), 0)
        assert values == [1, 2, 3]
        assert offset == 3

    def test_read_to_end_respects_start_offset(self) -> None:
        values, offset = Array(U8).read(MemoryTransport(b"xx\x01\x02"), 2)
        assert values == [1, 2]
        assert offset == 4

    def test_read_to_end_requires_aligned_width(self) -> None:
        with pytest.raises(LensError, match="do not align to item width 4"):
            Array(U32).read(MemoryTransport(b"\x00" * 5), 0)

    def test_read_to_end_requires_fixed_width_item(self) -> None:
        with pytest.raises(LensError, match="variable-width items and no count"):
            Array(Struct(x=U8)).read(MemoryTransport(b"\x00"), 0)

    def test_short_input_raises_with_index_path(self) -> None:
        with pytest.raises(LensError) as info:
            Array(U32, 2).read(MemoryTransport(b"\x00\x00\x00\x00"), 0)
        assert info.value.path == (1,)
        assert info.value.message.startswith("[1]: ")

    def test_callable_count_reads_sibling_field(self) -> None:
        schema = Struct(n=U8, items=Array(U8, count=lambda ctx: ctx.n))
        view = schema.read(MemoryTransport(b"\x02\x0a\x0b"), 0)[0]
        assert view.n == 2
        assert view.items == [10, 11]

    def test_callable_count_returning_bool_is_rejected(self) -> None:
        with pytest.raises(LensError, match="expected int >= 0"):
            Array(U8, count=lambda ctx: True).read(MemoryTransport(b""), 0)

    def test_callable_count_returning_negative_is_rejected(self) -> None:
        with pytest.raises(LensError, match="expected int >= 0"):
            Array(U8, count=lambda ctx: -1).read(MemoryTransport(b""), 0)

    def test_callable_count_returning_non_int_is_rejected(self) -> None:
        with pytest.raises(LensError, match="expected int >= 0"):
            Array(U8, count=lambda ctx: "2").read(MemoryTransport(b""), 0)

    def test_callable_count_wraps_arbitrary_exceptions(self) -> None:
        def broken(ctx: object) -> int:
            raise ZeroDivisionError("nope")

        with pytest.raises(LensError, match="count lambda failed"):
            Array(U8, count=broken).read(MemoryTransport(b""), 0)

    def test_callable_count_does_not_wrap_lens_error(self) -> None:
        def broken(ctx: object) -> int:
            raise LensError("inner")

        with pytest.raises(LensError, match="^inner$"):
            Array(U8, count=broken).read(MemoryTransport(b""), 0)


class TestArrayWrite:
    def test_fixed_count_write(self) -> None:
        transport = MemoryTransport()
        assert Array(U8, 3).write(transport, 0, [1, 2, 3]) == 3
        assert transport.to_bytes() == b"\x01\x02\x03"

    def test_write_wrong_element_count_raises(self) -> None:
        with pytest.raises(LensError, match="expected 2 elements, got 1"):
            Array(U8, 2).write(MemoryTransport(), 0, [1])

    def test_inferred_count_write_uses_value_length(self) -> None:
        transport = MemoryTransport()
        assert Array(U8).write(transport, 0, [7, 8, 9]) == 3
        assert transport.to_bytes() == b"\x07\x08\x09"

    def test_inferred_count_roundtrip(self) -> None:
        assert roundtrip_bytes(Array(U8), b"\x01\x02\x03") == b"\x01\x02\x03"

    def test_callable_count_write_checks_length(self) -> None:
        schema = Struct(n=U8, items=Array(U8, count=lambda ctx: ctx.n))
        transport = MemoryTransport()
        with pytest.raises(LensError, match="expected 2 elements"):
            schema.write(transport, 0, {"n": 2, "items": [1]})


# --------------------------------------------------------------------------- #
# Struct
# --------------------------------------------------------------------------- #


class TestStructConstruction:
    def test_bare_lens_classes_are_instantiated(self) -> None:
        schema = Struct(a=U8, b=U16)
        assert isinstance(schema.fields["a"], U8)
        assert isinstance(schema.fields["b"], U16)

    def test_instances_are_kept(self) -> None:
        big = U16(">")
        schema = Struct(a=big)
        assert schema.fields["a"] is big

    def test_rejects_non_lens_field(self) -> None:
        with pytest.raises(TypeError, match="field 'a' is not a Lens"):
            Struct(a=1)

    def test_repr(self) -> None:
        assert repr(Struct(a=U8)) == "Struct(a=U8(endian='<'))"


class TestStructSize:
    def test_fixed_size_sums_fields(self) -> None:
        assert Struct(a=U8, b=U16, c=Bytes(3)).size() == 6

    def test_dynamic_field_makes_size_none(self) -> None:
        assert Struct(n=U8, items=Array(U8, count=lambda ctx: ctx.n)).size() is None

    def test_empty_struct_size_is_zero(self) -> None:
        assert Struct().size() == 0


class TestStructRead:
    def test_reads_all_fields_in_order(self) -> None:
        schema = Struct(magic=Magic(b"BEEN"), version=U8, flags=U16)
        view, offset = schema.read(MemoryTransport(b"BEEN\x03\x34\x12"), 0)
        assert view.magic == b"BEEN"
        assert view.version == 3
        assert view.flags == 0x1234
        assert offset == 7
        assert view._end == 7

    def test_dynamic_array_sees_earlier_fields(self) -> None:
        schema = Struct(count=U32, items=Array(U32, count=lambda ctx: ctx.count))
        data = b"\x03\x00\x00\x00" + b"\x01\x00\x00\x00" * 3
        view = schema.read(MemoryTransport(data), 0)[0]
        assert view.count == 3
        assert view.items == [1, 1, 1]

    def test_error_path_includes_field_name(self) -> None:
        schema = Struct(a=U32)
        with pytest.raises(LensError) as info:
            schema.read(MemoryTransport(b"\x00"), 0)
        assert info.value.path == ("a",)
        assert info.value.message.startswith("a: ")

    def test_nested_struct_read(self) -> None:
        schema = Struct(header=Struct(magic=Magic(b"BEEN"), version=U8), count=U8)
        view = schema.read(MemoryTransport(b"BEEN\x02\x05"), 0)[0]
        assert view.header.version == 2
        assert view.count == 5

    def test_read_at_nonzero_offset(self) -> None:
        schema = Struct(value=U16)
        view, offset = schema.read(MemoryTransport(b"xx\x05\x00"), 2)
        assert view.value == 5
        assert offset == 4


class TestStructWrite:
    def test_roundtrip_through_view(self) -> None:
        schema = Struct(magic=Magic(b"BEEN"), version=U8, flags=U16)
        data = b"BEEN\x02\x34\x12"
        view = schema.read(MemoryTransport(data), 0)[0]
        transport = MemoryTransport()
        assert schema.write(transport, 0, view) == len(data)
        assert transport.to_bytes() == data

    def test_writes_from_plain_mapping(self) -> None:
        schema = Struct(a=U8, b=U16)
        transport = MemoryTransport()
        assert schema.write(transport, 0, {"a": 1, "b": 2}) == 3
        assert transport.to_bytes() == b"\x01\x02\x00"

    def test_missing_field_raises(self) -> None:
        schema = Struct(a=U8, b=U8)
        with pytest.raises(LensError, match="missing field 'b'"):
            schema.write(MemoryTransport(), 0, {"a": 1})

    def test_non_mapping_value_raises(self) -> None:
        with pytest.raises(LensError, match="missing field"):
            Struct(a=U8).write(MemoryTransport(), 0, 42)

    def test_nested_struct_roundtrip(self) -> None:
        schema = Struct(header=Struct(a=U8, b=U16), tail=U8)
        data = b"\x01\x02\x00\x03"
        assert roundtrip_bytes(schema, data) == data

    def test_dynamic_array_roundtrip(self) -> None:
        schema = Struct(count=U32, items=Array(U32, count=lambda ctx: ctx.count))
        data = b"\x02\x00\x00\x00" + b"\x01\x00\x00\x00\x02\x00\x00\x00"
        assert roundtrip_bytes(schema, data) == data
