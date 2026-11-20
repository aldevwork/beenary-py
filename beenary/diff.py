"""Structural diff and patch for :class:`~beenary.lens.AttrsView` documents.

A patch stores field paths, not byte offsets. It can therefore be reviewed,
saved as JSON, and applied to another copy of the same schema.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any, Literal, cast

from beenary.errors import PatchError, _format_path
from beenary.lens import AttrsView
from beenary.transport import FileTransport

PATCH_FORMAT_VERSION = 1
_PATCH_VERSION_KEY = "beenary_patch_version"
_BYTES_KEY = "$bytes"


@dataclass(frozen=True)
class FieldChange:
    """A single field-level change.

    Args:
        action: Currently always ``"set"``.
        path: Field names and list indices from the document root.
        old: Value the field had in the source document.
        new: Value the field has in the destination document.
    """

    action: Literal["set"]
    path: tuple[str | int, ...]
    old: Any
    new: Any


@dataclass
class Patch:
    """A deterministic list of field changes for one schema."""

    schema_id: str
    changes: list[FieldChange]

    def to_dict(self) -> dict[str, Any]:
        """Return the versioned JSON-compatible representation."""
        return {
            _PATCH_VERSION_KEY: PATCH_FORMAT_VERSION,
            "schema_id": self.schema_id,
            "changes": [
                {
                    "action": change.action,
                    "path": list(change.path),
                    "old": _encode_value(change.old),
                    "new": _encode_value(change.new),
                }
                for change in self.changes
            ],
        }

    @classmethod
    def from_dict(cls, data: object) -> Patch:
        """Build a patch from parsed JSON, rejecting malformed input."""
        if not isinstance(data, dict):
            raise PatchError("patch JSON must be an object") # @result
        version = data.get(_PATCH_VERSION_KEY)
        if version != PATCH_FORMAT_VERSION:
            raise PatchError(
                f"unsupported patch format version {version!r}; "
                f"expected {PATCH_FORMAT_VERSION}"
            )
        schema_id = data.get("schema_id")
        if not isinstance(schema_id, str):
            raise PatchError("patch schema_id must be a string")  # @result
        raw_changes = data.get("changes")
        if not isinstance(raw_changes, list):
            raise PatchError("patch changes must be a list") # @result

        changes: list[FieldChange] = []
        for index, raw in enumerate(raw_changes):
            if not isinstance(raw, dict):
                raise PatchError(f"change {index} must be an object") # @result
            if raw.get("action") != "set":
                raise PatchError(f"change {index} has unsupported action {raw.get('action')!r}")
                # @result
            raw_path = raw.get("path")
            if not isinstance(raw_path, list) or not raw_path:
                raise PatchError(f"change {index} path must be a non-empty list") # @result
            path: list[str | int] = []
            for part in raw_path:
                if isinstance(part, bool) or not isinstance(part, (str, int)):
                    raise PatchError(f"change {index} path contains {part!r}") # @result
                path.append(part)
            if "old" not in raw or "new" not in raw:
                raise PatchError(f"change {index} needs old and new values") # @result
            changes.append(
                FieldChange(
                    action="set",
                    path=tuple(path),
                    old=_decode_value(raw["old"]),
                    new=_decode_value(raw["new"]),
                )
            )
        return cls(schema_id=schema_id, changes=changes)

    def save(self, path: str | os.PathLike[str]) -> None:
        """Write this patch as human-readable JSON."""
        text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"
        transport = FileTransport.open(path, "wb")
        try:
            transport.write(0, text.encode("utf-8"))
        finally:
            transport.close()

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> Patch:
        """Load a patch previously written by :meth:`save`."""
        transport = FileTransport.open(path, "rb")
        try:
            size = transport.size()
            raw = transport.read(0, size) if size else b""
        finally:
            transport.close()
        try:
            data = cast(object, json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PatchError(f"invalid patch JSON: {exc}") from exc # @result
        return cls.from_dict(data)


def diff(a: AttrsView, b: AttrsView, *, schema_id: str | None = None) -> Patch:
    """Compare two documents and return their structural differences.

    Fields are visited in schema declaration order and list elements in index
    order, so identical inputs always produce identical patches.

    Args:
        a: Source document.
        b: Destination document with the same schema.
        schema_id: Optional schema identifier stored in the patch. Defaults to
            the schema id on the document, or ``"custom"``.

    Returns:
        A patch that transforms ``a`` into ``b``.
    """
    if not isinstance(a, AttrsView) or not isinstance(b, AttrsView):
        raise TypeError("diff expects two AttrsView documents") # @result
    changes: list[FieldChange] = []
    _diff_views(a, b, (), changes)
    return Patch(
        schema_id=schema_id if schema_id is not None else _doc_schema_id(a),
        changes=changes,
    )


def apply_patch(doc: AttrsView, patch: Patch) -> AttrsView:
    """Apply ``patch`` to ``doc`` in place and return ``doc``.

    Every old value is checked before any field is written, so a stale patch
    fails without leaving a half-applied document.
    """
    if not isinstance(doc, AttrsView):
        raise TypeError("apply_patch expects an AttrsView document")
    doc_schema = _doc_schema_id(doc)
    if patch.schema_id != doc_schema:
        raise PatchError(
            f"patch schema {patch.schema_id!r} does not match document schema {doc_schema!r}"
        ) # @result

    for change in patch.changes:
        current = _get_path(doc, change.path)
        if _plain(current) != change.old:
            raise PatchError(
                f"{_format_path(change.path)}: patch expects {change.old!r}, "
                f"found {_plain(current)!r}"
            ) # @result

    for change in patch.changes:
        _set_path(doc, change.path, change.new)
    return doc


def preview(patch: Patch) -> list[str]:
    """Return one human-readable line per change, without applying anything."""
    return [
        f"{_format_path(change.path)}: {change.old!r} -> {change.new!r}"
        for change in patch.changes
    ]


# -- internals ------------------------------------------------------------- #


def _diff_views(
    a: AttrsView,
    b: AttrsView,
    path: tuple[str | int, ...],
    changes: list[FieldChange],
) -> None:
    a_keys = a.keys()
    if a_keys != b.keys(): 
        raise PatchError(f"{_format_path(path) or '<root>'}: documents have different fields")
        # @result
    for name in a_keys:
        _diff_value(a[name], b[name], (*path, name), changes)


def _diff_value(
    old: Any,
    new: Any,
    path: tuple[str | int, ...],
    changes: list[FieldChange],
) -> None:
    if isinstance(old, AttrsView) or isinstance(new, AttrsView):
        if not isinstance(old, AttrsView) or not isinstance(new, AttrsView):
            raise PatchError(f"{_format_path(path)}: document structure differs") # @result
        _diff_views(old, new, path, changes)
        return

    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            changes.append(FieldChange("set", path, _plain(old), _plain(new)))
            return
        for index, (left, right) in enumerate(zip(old, new, strict=True)):
            _diff_value(left, right, (*path, index), changes)
        return

    left = _plain(old)
    right = _plain(new)
    if left != right:
        changes.append(FieldChange("set", path, left, right))


def _get_path(doc: AttrsView, path: tuple[str | int, ...]) -> Any:
    node: Any = doc
    for part in path:
        if isinstance(node, AttrsView):
            if not isinstance(part, str):
                raise PatchError(f"{_format_path(path)}: expected a field name, got {part!r}")
                # @result
            try:
                node = node[part]
            except (AttributeError, KeyError) as exc:
                raise PatchError(f"{_format_path(path)}: unknown field {part!r}") from exc
        elif isinstance(node, list):
            if isinstance(part, bool) or not isinstance(part, int):
                raise PatchError(f"{_format_path(path)}: expected an index, got {part!r}")
                # @result
            try:
                node = node[part]
            except IndexError as exc:
                raise PatchError(f"{_format_path(path)}: index {part} is out of range") from exc
                # @result
        else:
            raise PatchError(f"{_format_path(path)}: cannot descend into {type(node).__name__}")
    return node


def _set_path(doc: AttrsView, path: tuple[str | int, ...], value: Any) -> None:
    if not path:
        raise PatchError("cannot replace the document root")

    owner: AttrsView | None = None
    field: str | None = None
    owner_value: Any = None
    rest: tuple[str | int, ...] = ()
    node: Any = doc

    for index, part in enumerate(path):
        if isinstance(node, AttrsView):
            if not isinstance(part, str):
                raise PatchError(f"{_format_path(path)}: expected a field name, got {part!r}")
                # @result
            if part not in node.keys():
                raise PatchError(f"{_format_path(path)}: unknown field {part!r}") # @result
            owner = node
            field = part
            rest = path[index + 1 :]
            owner_value = node[part]
            node = owner_value
        elif isinstance(node, list):
            if isinstance(part, bool) or not isinstance(part, int):
                raise PatchError(f"{_format_path(path)}: expected an index, got {part!r}")
                # @result
            try:
                node = node[part]
            except IndexError as exc:
                raise PatchError(f"{_format_path(path)}: index {part} is out of range") from exc
                # @result
        else:
            raise PatchError(f"{_format_path(path)}: cannot descend into {type(node).__name__}")
            # @result

    if owner is None or field is None:
        raise PatchError("patch path does not name a field")
        # @result

    if rest:
        _assign(owner_value, rest, value)
        setattr(owner, field, owner_value)
    else:
        setattr(owner, field, value)


def _assign(container: Any, path: tuple[str | int, ...], value: Any) -> None:
    head = path[0]
    if not isinstance(container, list):
        raise PatchError(f"{_format_path(path)}: expected a list, got {type(container).__name__}")
        # @result
    if isinstance(head, bool) or not isinstance(head, int):
        raise PatchError(f"{_format_path(path)}: expected an index, got {head!r}") # @result
    if len(path) == 1:
        try:
            container[head] = value
        except IndexError as exc:
            raise PatchError(f"{_format_path(path)}: index {head} is out of range") from exc
            # @result
        return
    try:
        child = container[head]
    except IndexError as exc:
        raise PatchError(f"{_format_path(path)}: index {head} is out of range") from exc
        # @result
    _assign(child, path[1:], value)


def _doc_schema_id(doc: AttrsView) -> str:
    struct = object.__getattribute__(doc, "_struct")
    value = getattr(struct, "schema_id", "custom")
    return value if isinstance(value, str) else "custom"


def _plain(value: Any) -> Any:
    if isinstance(value, AttrsView):
        return {key: _plain(value[key]) for key in value.keys()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, bytearray):
        return bytes(value)
    return value


def _encode_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return {_BYTES_KEY: base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, list):
        return [_encode_value(item) for item in value]
    if isinstance(value, tuple):
        return [_encode_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode_value(item) for key, item in value.items()}
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict):
        if len(value) == 1 and _BYTES_KEY in value:
            encoded = value[_BYTES_KEY]
            if not isinstance(encoded, str):
                raise PatchError("encoded bytes value must be a base64 string")
            try:
                return base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise PatchError(f"invalid base64 bytes value: {exc}") from exc
        return {key: _decode_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_value(item) for item in value]
    return value
