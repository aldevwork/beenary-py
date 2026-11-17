"""Tests for AttrsView: access, write-through, and ``save``."""

from __future__ import annotations

from pathlib import Path

import pytest

from beenary.errors import LensError
from beenary.lens import U8, U16, U32, Array, AttrsView, Bytes, Magic, Struct, view_ctx
from beenary.transport import MemoryTransport

SIMPLE_DATA = b"BEEN\x02\x34\x12"
SIMPLE = Struct(magic=Magic(b"BEEN"), version=U8, flags=U16)

DYNAMIC_DATA = b"\x02\x00\x00\x00" + b"\x01\x00\x00\x00\x02\x00\x00\x00" + b"tail"
DYNAMIC = Struct(
    count=U32,
    items=Array(U32, count=lambda ctx: ctx.count),
    trailer=Bytes(4),
)


def read_view(schema: Struct = SIMPLE, data: bytes = SIMPLE_DATA) -> AttrsView:
    view, _ = schema.read(MemoryTransport(data), 0)
    return view


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


class TestAccess:
    def test_attribute_and_item_access_agree(self) -> None:
        view = read_view()
        assert view.version == view["version"] == 2

    def test_keys_in_declaration_order(self) -> None:
        assert read_view().keys() == ["magic", "version", "flags"]

    def test_to_dict_snapshot(self) -> None:
        view = read_view()
        assert view.to_dict() == {"magic": b"BEEN", "version": 2, "flags": 0x1234}

    def test_unknown_attribute_raises(self) -> None:
        with pytest.raises(AttributeError, match="not a field"):
            _ = read_view().nope

    def test_unknown_field_assignment_raises(self) -> None:
        with pytest.raises(AttributeError, match="not a field"):
            read_view().nope = 1

    def test_lazy_read_from_fresh_view(self) -> None:
        transport = MemoryTransport(SIMPLE_DATA)
        view = AttrsView(transport, SIMPLE, start=0)
        assert view.flags == 0x1234
        assert view.magic == b"BEEN"

    def test_nested_view_attributes(self) -> None:
        schema = Struct(header=Struct(magic=Magic(b"BEEN"), version=U8), tail=U8)
        view, _ = schema.read(MemoryTransport(b"BEEN\x03\x09"), 0)
        assert view.header.magic == b"BEEN"
        assert view.header.version == 3
        assert view.tail == 9

    def test_dynamic_fields_resolve_offsets(self) -> None:
        view = read_view(DYNAMIC, DYNAMIC_DATA)
        assert view.count == 2
        assert view.items == [1, 2]
        assert view.trailer == b"tail"


# --------------------------------------------------------------------------- #
# Write-through
# --------------------------------------------------------------------------- #


class TestWriteThrough:
    def test_attribute_assignment_updates_transport(self) -> None:
        transport = MemoryTransport(SIMPLE_DATA)
        view, _ = SIMPLE.read(transport, 0)
        view.version = 7
        assert transport.read(4, 1) == b"\x07"
        assert view.version == 7

    def test_item_assignment_updates_transport(self) -> None:
        transport = MemoryTransport(SIMPLE_DATA)
        view, _ = SIMPLE.read(transport, 0)
        view["flags"] = 0xABCD
        assert transport.read(5, 2) == b"\xcd\xab"

    def test_nested_assignment_updates_transport(self) -> None:
        schema = Struct(header=Struct(version=U8), tail=U8)
        transport = MemoryTransport(b"\x01\x02")
        view, _ = schema.read(transport, 0)
        view.header.version = 9
        assert transport.to_bytes() == b"\x09\x02"

    def test_assignment_raises_on_read_only_transport(self) -> None:
        import os
        import tempfile

        from beenary.transport import MmapTransport

        fd, name = tempfile.mkstemp()
        os.write(fd, SIMPLE_DATA)
        os.close(fd)
        try:
            transport = MmapTransport(name)
            try:
                view, _ = SIMPLE.read(transport, 0)
                with pytest.raises(LensError, match="read-only"):
                    view.version = 1
            finally:
                transport.close()
        finally:
            os.unlink(name)

    def test_invalid_value_for_field_raises(self) -> None:
        view = read_view(SIMPLE, SIMPLE_DATA)
        with pytest.raises(LensError, match="cannot pack"):
            view.version = 999


# --------------------------------------------------------------------------- #
# save()
# --------------------------------------------------------------------------- #


class TestSave:
    def test_save_roundtrips_bytes(self, tmp_path: Path) -> None:
        view = read_view()
        out = tmp_path / "out.bin"
        assert view.save(out) is None
        assert out.read_bytes() == SIMPLE_DATA

    def test_save_uses_modified_values(self, tmp_path: Path) -> None:
        view = read_view()
        view.version = 7
        view.flags = 0x0102
        out = tmp_path / "out.bin"
        view.save(out)
        assert out.read_bytes() == b"BEEN\x07\x02\x01"

    def test_save_with_dynamic_fields(self, tmp_path: Path) -> None:
        view = read_view(DYNAMIC, DYNAMIC_DATA)
        out = tmp_path / "out.bin"
        view.save(out)
        assert out.read_bytes() == DYNAMIC_DATA

    def test_save_after_list_edit(self, tmp_path: Path) -> None:
        view = read_view(DYNAMIC, DYNAMIC_DATA)
        view.items[1] = 99
        out = tmp_path / "out.bin"
        view.save(out)
        reopened, _ = DYNAMIC.read(MemoryTransport(out.read_bytes()), 0)
        assert reopened.items == [1, 99]

    def test_save_inferred_length_array(self, tmp_path: Path) -> None:
        schema = Struct(items=Array(U8))
        view, _ = schema.read(MemoryTransport(b"\x01\x02\x03"), 0)
        out = tmp_path / "out.bin"
        view.save(out)
        assert out.read_bytes() == b"\x01\x02\x03"

    def test_save_overwrites_longer_file(self, tmp_path: Path) -> None:
        out = tmp_path / "out.bin"
        out.write_bytes(b"x" * 100)
        read_view().save(out)
        assert out.read_bytes() == SIMPLE_DATA

    def test_saved_file_can_be_opened(self, tmp_path: Path) -> None:
        from beenary import open_file

        view = read_view()
        out = tmp_path / "out.bin"
        view.save(out)
        reopened = open_file(out, SIMPLE)
        assert reopened.version == 2
        assert reopened.flags == 0x1234

    def test_save_accepts_pathlike(self, tmp_path: Path) -> None:
        read_view().save(tmp_path / "nested.bin")
        assert (tmp_path / "nested.bin").exists()


# --------------------------------------------------------------------------- #
# Helpers / repr
# --------------------------------------------------------------------------- #


def test_view_ctx_prefers_attrs_view() -> None:
    view = read_view()
    other = object()
    assert view_ctx(view, other) is view


def test_view_ctx_wraps_mappings() -> None:
    ctx = view_ctx({"a": 1}, None)
    assert ctx.a == 1
    with pytest.raises(AttributeError):
        _ = ctx.missing


def test_view_ctx_passes_through_other_values() -> None:
    other = object()
    assert view_ctx(42, other) is other


def test_repr_lists_fields() -> None:
    text = repr(read_view())
    assert text.startswith("AttrsView(")
    assert "magic=" in text
    assert "version=2" in text
    assert "flags=4660" in text


def test_repr_reports_unreadable_field() -> None:
    transport = MemoryTransport(SIMPLE_DATA)
    broken = AttrsView(transport, SIMPLE, start=100)
    assert "<error:" in repr(broken)
