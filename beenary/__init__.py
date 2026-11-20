"""Public API for beenary."""

from __future__ import annotations

import os
from typing import Any, cast

from beenary.diff import FieldChange, Patch, apply_patch, diff, preview
from beenary.errors import FormatError
from beenary.formats import (
    FormatPlugin,
    detect,
    get_plugin,
    plugins,
    register_plugin,
    sniff,
)
from beenary.lens import AttrsView, Struct, with_default
from beenary.transport import FileTransport, MemoryTransport, Transport

__all__ = [
    "FieldChange",
    "FormatPlugin",
    "Patch",
    "apply_patch",
    "detect",
    "diff",
    "get_plugin",
    "open_file",
    "plugins",
    "preview",
    "register_plugin",
    "schema_save_to_file",
    "schema_write",
    "sniff",
    "with_default",
]


def open_file(
    path: str | os.PathLike[str],
    schema: Any | None = None,
    mode: str = "r+b",
) -> AttrsView:
    """Read a file through ``schema``, or through the detected format.

    Args:
        path: File to open for reading.
        schema: A lens (typically :class:`beenary.lens.Struct`) describing the
            layout. ``None`` picks the best format plugin with :func:`detect`.
        mode: A readable file mode. Use ``"rb"`` for read-only access or
            ``"r+b"`` for write-through field edits.

    Returns:
        The value produced by the schema's ``read`` — an
        :class:`beenary.lens.AttrsView` for a :class:`Struct`.

    Raises:
        FormatError: If ``schema`` is ``None`` and no plugin matches.
    """
    if not mode.startswith("r"):
        raise ValueError(
            f"open_file needs a read mode such as 'rb' or 'r+b', got {mode!r}"
        )
    transport = FileTransport.open(path, mode)  # or MmapTransport for big files
    if schema is None:
        plugin = detect(transport)
        if plugin is None:
            transport.close()
            raise FormatError(f"no format plugin matched {str(path)!r}")
        schema = plugin.schema()
    try:
        value, _ = schema.read(transport, 0)  # ctx=None → Struct makes its own view
    except Exception:
        transport.close()
        raise
    return cast(AttrsView, value)


def schema_write(
    schema: Struct,
    value: Any | None,
    out_transport: Transport,
    offset: int = 0,
) -> int:
    """Write ``value`` through ``schema`` into ``out_transport``.

    Args:
        schema: Struct describing the layout.
        value: Mapping or :class:`AttrsView` to write. ``None`` uses the
            struct's default, which requires :func:`with_default`.
        out_transport: Destination transport.
        offset: Byte offset to start writing at.

    Returns:
        The offset immediately after the written struct.
    """
    if value is None:
        value = schema.new()
    return schema.write(out_transport, offset, value)


def schema_save_to_file(
    schema: Struct,
    value: Any | None,
    path: str | os.PathLike[str],
) -> None:
    """Serialize ``value`` through ``schema`` and write it to ``path``.

    This is the explicit-save counterpart to :func:`open_file`. It creates or
    overwrites ``path``.

    Args:
        schema: Struct describing the layout.
        value: Mapping or :class:`AttrsView` to write. ``None`` uses the
            struct's default, which requires :func:`with_default`.
        path: Destination file.
    """
    transport = MemoryTransport()
    schema_write(schema, value, transport)
    dest = FileTransport.open(path, "wb")
    try:
        dest.write(0, transport.to_bytes())
    finally:
        dest.close()
