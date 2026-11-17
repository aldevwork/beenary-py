"""Exception hierarchy for beenary.

All beenary errors derive from :class:`BeenaryError`. Specific subclasses map to
the major subsystem that raised them.

Attributes on the exceptions carry context so callers can react programmatically
(e.g. the failing path for a :class:`LensError`).
"""

from __future__ import annotations


class BeenaryError(Exception):
    """Base class for every error raised by beenary."""


class LensError(BeenaryError):
    """Raised by lens read/write operations.

    Attributes:
        message: Human readable description.
        path: The schema path (tuple of field names / indices) that failed, if
            known.
    """

    def __init__(self, message: str, path: tuple[str | int, ...] | None = None) -> None:
        self.path = path
        if path:
            message = f"{_format_path(path)}: {message}"
        self.message = message
        super().__init__(message)


class FormatError(BeenaryError):
    """Raised when a format plugin cannot parse or is misconfigured."""


class PatchError(BeenaryError):
    """Raised when a patch cannot be applied or is invalid."""


def _format_path(path: tuple[str | int, ...]) -> str:
    """Render a field path as ``a.b[0].c``."""
    out = ""
    for part in path:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else part
    return out
