"""Tests for schema-level writing, saving, and default values."""

from __future__ import annotations

from pathlib import Path

import pytest

from beenary import schema_save_to_file, schema_write, with_default
from beenary.lens import U8, U16, U32, Array, Struct
from beenary.transport import MemoryTransport

SCHEMA = Struct(
    version=U8,
    count=U32,
    items=Array(U8, count=lambda ctx: ctx.count),
)
VALUE = {"version": 2, "count": 3, "items": [10, 11, 12]}
DATA = b"\x02\x03\x00\x00\x00\x0a\x0b\x0c"


class TestSchemaWrite:
    def test_writes_to_transport_at_offset(self) -> None:
        transport = MemoryTransport(b"xx")
        end = schema_write(SCHEMA, VALUE, transport, offset=2)
        assert end == 2 + len(DATA)
        assert transport.to_bytes() == b"xx" + DATA

    def test_returns_end_offset(self) -> None:
        transport = MemoryTransport()
        assert schema_write(SCHEMA, VALUE, transport) == len(DATA)

    def test_missing_field_raises_lens_error(self) -> None:
        from beenary.errors import LensError

        with pytest.raises(LensError, match="missing field 'items'"):
            schema_write(SCHEMA, {"version": 1, "count": 0}, MemoryTransport())

    def test_none_uses_struct_default(self) -> None:
        defaulted = with_default(SCHEMA, lambda: VALUE)
        transport = MemoryTransport()
        schema_write(defaulted, None, transport)
        assert transport.to_bytes() == DATA


class TestSchemaSaveToFile:
    def test_writes_exact_bytes(self, tmp_path: Path) -> None:
        path = tmp_path / "out.bin"
        schema_save_to_file(SCHEMA, VALUE, path)
        assert path.read_bytes() == DATA

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "out.bin"
        path.write_bytes(b"old data that is longer")
        schema_save_to_file(SCHEMA, VALUE, path)
        assert path.read_bytes() == DATA

    def test_accepts_pathlike(self, tmp_path: Path) -> None:
        path = tmp_path / "out.bin"
        schema_save_to_file(SCHEMA, VALUE, path)
        assert len(path.read_bytes()) == len(DATA)


class TestWithDefault:
    def test_new_returns_live_view(self) -> None:
        schema = with_default(SCHEMA, lambda: VALUE)
        view = schema.new()
        assert view.version == 2
        assert view.items == [10, 11, 12]

    def test_factory_runs_for_every_new(self) -> None:
        calls = 0

        def factory() -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {**VALUE, "version": calls}

        schema = with_default(SCHEMA, factory)
        assert schema.new().version == 1
        assert schema.new().version == 2
        assert calls == 2

    def test_does_not_mutate_original_schema(self) -> None:
        schema = with_default(SCHEMA, lambda: VALUE)
        assert schema is not SCHEMA
        with pytest.raises(ValueError, match="no default"):
            SCHEMA.new()

    def test_new_without_default_raises(self) -> None:
        with pytest.raises(ValueError, match="with_default"):
            SCHEMA.new()

    def test_field_named_default_is_allowed(self) -> None:
        schema = with_default(Struct(default=U8, value=U16), lambda: {"default": 7, "value": 300})
        view = schema.new()
        assert view.default == 7
        assert view.value == 300

    def test_rejects_non_struct(self) -> None:
        with pytest.raises(TypeError, match="expects a Struct"):
            with_default(U8, lambda: 1)  # type: ignore[arg-type]

    def test_rejects_non_callable(self) -> None:
        with pytest.raises(TypeError, match="must be callable"):
            with_default(SCHEMA, VALUE)  # type: ignore[arg-type]
