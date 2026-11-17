"""Tests for the public ``open_file`` API and the README workflow."""

from __future__ import annotations

from pathlib import Path

import pytest

from beenary import open_file
from beenary.errors import LensError
from beenary.lens import Array, AttrsView, Bytes, Magic, Struct, U8, U16, U32

SCHEMA = Struct(
    header=Struct(magic=Magic(b"BEEN"), version=U8, flags=U16),
    count=U32,
    items=Array(U32, count=lambda ctx: ctx.count),
    trailer=Bytes(4),
)

DATA = (
    b"BEEN\x02\x00\x00"  # header: magic, version, flags
    b"\x03\x00\x00\x00"  # count = 3
    b"\x01\x00\x00\x00\x02\x00\x00\x00\x03\x00\x00\x00"  # items
    b"tail"
)


def write_sample(tmp_path: Path) -> Path:
    path = tmp_path / "sample.bin"
    path.write_bytes(DATA)
    return path


def test_open_file_returns_attrs_view(tmp_path: Path) -> None:
    doc = open_file(write_sample(tmp_path), SCHEMA)
    assert isinstance(doc, AttrsView)
    assert doc.header.version == 2
    assert doc.header.flags == 0
    assert doc.count == 3
    assert doc.items == [1, 2, 3]
    assert doc.trailer == b"tail"


def test_open_file_accepts_schema_keyword(tmp_path: Path) -> None:
    doc = open_file(write_sample(tmp_path), schema=SCHEMA)
    assert doc.count == 3


def test_open_file_accepts_pathlike(tmp_path: Path) -> None:
    doc = open_file(write_sample(tmp_path), SCHEMA)
    assert doc.items[0] == 1


def test_open_file_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        open_file(tmp_path / "nope.bin", SCHEMA)


def test_default_mode_allows_write_through(tmp_path: Path) -> None:
    path = write_sample(tmp_path)
    doc = open_file(path, SCHEMA)
    doc.header.version = 9
    assert path.read_bytes()[4] == 9


def test_read_only_mode_rejects_write_through(tmp_path: Path) -> None:
    path = write_sample(tmp_path)
    doc = open_file(path, SCHEMA, mode="rb")
    with pytest.raises(LensError, match="read-only"):
        doc.header.version = 9


def test_write_through_then_save_roundtrip(tmp_path: Path) -> None:
    path = write_sample(tmp_path)
    doc = open_file(path, SCHEMA)
    doc.header.version = 3
    doc.items[2] = 99999
    out = tmp_path / "modified.bin"
    doc.save(out)

    reopened = open_file(out, SCHEMA)
    assert reopened.header.version == 3
    assert reopened.items == [1, 2, 99999]
    assert reopened.trailer == b"tail"


def test_open_file_exposes_lazy_views(tmp_path: Path) -> None:
    doc = open_file(write_sample(tmp_path), SCHEMA)
    # Nested structs are AttrsViews too.
    assert isinstance(doc.header, AttrsView)
    assert doc.header.keys() == ["magic", "version", "flags"]
