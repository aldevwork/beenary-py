"""Tests for structural diff, JSON patches, and patch application."""

from __future__ import annotations

import random
import struct
from pathlib import Path

import pytest

from beenary import Patch, apply_patch, diff, preview
from beenary.errors import PatchError
from beenary.lens import U8, U16, U32, Array, AttrsView, Bytes, Struct
from beenary.transport import MemoryTransport

SCHEMA = Struct(
    header=Struct(version=U8, flags=U16),
    count=U32,
    items=Array(U32, count=lambda ctx: ctx.count),
    trailer=Bytes(4),
)


def snapshot(value: object) -> object:
    if isinstance(value, AttrsView):
        return {key: snapshot(value[key]) for key in value.keys()}
    if isinstance(value, list):
        return [snapshot(item) for item in value]
    return value


def make_doc(
    version: int = 1,
    flags: int = 0,
    items: list[int] | None = None,
    trailer: bytes = b"tail",
) -> AttrsView:
    items = [1, 2] if items is None else items
    data = (
        struct.pack("<BH", version, flags)
        + struct.pack("<I", len(items))
        + b"".join(struct.pack("<I", item) for item in items)
        + trailer
    )
    view, _ = SCHEMA.read(MemoryTransport(data), 0)
    return view


def test_diff_identical_documents_is_empty() -> None:
    patch = diff(make_doc(), make_doc())
    assert patch.schema_id == "custom"
    assert patch.changes == []


def test_diff_reports_nested_and_indexed_paths_in_order() -> None:
    a = make_doc(version=1, items=[1, 2, 3])
    b = make_doc(version=2, items=[1, 9, 3])

    patch = diff(a, b)

    assert [(change.action, change.path) for change in patch.changes] == [
        ("set", ("header", "version")),
        ("set", ("items", 1)),
    ]
    assert patch.changes[0].old == 1
    assert patch.changes[0].new == 2
    assert patch.changes[1].old == 2
    assert patch.changes[1].new == 9


def test_diff_of_resized_array_includes_count_and_whole_list() -> None:
    patch = diff(make_doc(items=[1, 2]), make_doc(items=[1, 2, 3]))
    assert [change.path for change in patch.changes] == [("count",), ("items",)]
    assert patch.changes[1].old == [1, 2]
    assert patch.changes[1].new == [1, 2, 3]


def test_apply_patch_mutates_and_roundtrips_through_save(tmp_path: Path) -> None:
    a = make_doc(version=1, flags=2, items=[1, 2], trailer=b"old!")
    b = make_doc(version=7, flags=9, items=[1, 5, 6], trailer=b"new!")
    patch = diff(a, b)

    assert apply_patch(a, patch) is a
    assert snapshot(a) == snapshot(b)

    out = tmp_path / "patched.bin"
    a.save(out)
    reopened, _ = SCHEMA.read(MemoryTransport(out.read_bytes()), 0)
    assert snapshot(reopened) == snapshot(b)


def test_apply_patch_checks_old_value_before_writing() -> None:
    patch = diff(make_doc(items=[1, 2]), make_doc(items=[1, 3]))
    target = make_doc(items=[1, 4])

    with pytest.raises(PatchError, match="expects 2, found 4"):
        apply_patch(target, patch)

    assert target.items == [1, 4]


def test_apply_patch_rejects_mismatched_schema_id() -> None:
    patch = diff(make_doc(), make_doc(version=2), schema_id="other")
    with pytest.raises(PatchError, match="does not match document schema"):
        apply_patch(make_doc(), patch)


def test_diff_rejects_documents_with_different_fields() -> None:
    left, _ = Struct(a=U8).read(MemoryTransport(b"\x01"), 0)
    right, _ = Struct(b=U8).read(MemoryTransport(b"\x01"), 0)
    with pytest.raises(PatchError, match="different fields"):
        diff(left, right)


def test_patch_save_and_load_roundtrip_bytes_and_paths(tmp_path: Path) -> None:
    patch = diff(make_doc(trailer=b"old!"), make_doc(trailer=b"new!"))
    path = tmp_path / "update.json"
    patch.save(path)

    text = path.read_text()
    assert '"beenary_patch_version": 1' in text
    assert '"$bytes"' in text
    assert Patch.load(path) == patch


def test_patch_load_rejects_unknown_format_version(tmp_path: Path) -> None:
    path = tmp_path / "update.json"
    path.write_text('{"beenary_patch_version": 2, "schema_id": "custom", "changes": []}')
    with pytest.raises(PatchError, match="unsupported patch format version"):
        Patch.load(path)


def test_preview_lists_changes_without_applying() -> None:
    a = make_doc(version=1, items=[1, 2])
    b = make_doc(version=2, items=[1, 9])
    assert preview(diff(a, b)) == [
        "header.version: 1 -> 2",
        "items[1]: 2 -> 9",
    ]
    assert snapshot(a) == snapshot(make_doc(version=1, items=[1, 2]))


def test_random_roundtrips_apply_to_destination() -> None:
    rng = random.Random(1234)
    for _ in range(30):
        a = make_doc(
            version=rng.randrange(256),
            flags=rng.randrange(1 << 16),
            items=[rng.randrange(1 << 32) for _ in range(rng.randrange(5))],
            trailer=bytes(rng.randrange(256) for _ in range(4)),
        )
        b = make_doc(
            version=rng.randrange(256),
            flags=rng.randrange(1 << 16),
            items=[rng.randrange(1 << 32) for _ in range(rng.randrange(5))],
            trailer=bytes(rng.randrange(256) for _ in range(4)),
        )
        patch = diff(a, b)
        assert patch == diff(a, b)
        assert snapshot(apply_patch(a, patch)) == snapshot(b)


def _random_lens(rng: random.Random, depth: int = 0) -> object:
    if depth >= 3:
        return rng.choice([U8, U16, U32, Bytes(4)])
    kind = rng.randrange(4)
    if kind == 0:
        fields = {f"f{index}": _random_lens(rng, depth + 1) for index in range(rng.randrange(1, 4))}
        return Struct(**fields)
    if kind == 1:
        return Array(_random_lens(rng, depth + 1), count=rng.randrange(1, 4))
    if kind == 2:
        return Bytes(rng.randrange(1, 5))
    return rng.choice([U8, U16, U32])


def _random_value(rng: random.Random, lens: object) -> object:
    if isinstance(lens, Struct):
        return {name: _random_value(rng, field) for name, field in lens.fields.items()}
    if isinstance(lens, Array):
        count = lens.count
        assert isinstance(count, int)
        return [_random_value(rng, lens.item) for _ in range(count)]
    if isinstance(lens, Bytes):
        return bytes(rng.randrange(256) for _ in range(lens.length))
    if isinstance(lens, U8):
        return rng.randrange(1 << 8)
    if isinstance(lens, U16):
        return rng.randrange(1 << 16)
    if isinstance(lens, U32):
        return rng.randrange(1 << 32)
    raise AssertionError(f"unexpected lens {lens!r}")


def _read_value(lens: Struct, value: object) -> AttrsView:
    transport = MemoryTransport()
    lens.write(transport, 0, value)
    view, _ = lens.read(MemoryTransport(transport.to_bytes()), 0)
    return view


def test_random_schemas_roundtrip_through_patch() -> None:
    rng = random.Random(99)
    for _ in range(20):
        schema = Struct(
            left=_random_lens(rng),
            right=_random_lens(rng, 1),
        )
        a = _read_value(schema, _random_value(rng, schema))
        b = _read_value(schema, _random_value(rng, schema))
        patch = diff(a, b)
        assert patch == diff(a, b)
        assert snapshot(apply_patch(a, patch)) == snapshot(b)
