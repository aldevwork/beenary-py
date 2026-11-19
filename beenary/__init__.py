"""Public API for beenary."""

from __future__ import annotations

import os
from typing import Any, cast

from beenary.lens import AttrsView
from beenary.transport import FileTransport


def open_file(path: str | os.PathLike[str], schema: Any, mode: str = "r+b") -> AttrsView:
    """Read ``schema`` from the start of the file at ``path``.

    Args:
        path: File to open for reading.
        schema: A lens (typically :class:`beenary.lens.Struct`) describing the
            layout.
        mode: A mode with which to open a file

    Returns:
        The value produced by ``schema.read`` — an
        :class:`beenary.lens.AttrsView` for a :class:`Struct`.
    """
    transport = FileTransport.open(path, mode)  # or MmapTransport for big files
    value, _ = schema.read(transport, 0)  # ctx=None → Struct makes its own view
    return cast(AttrsView, value)
