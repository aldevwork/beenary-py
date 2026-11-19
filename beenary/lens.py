"""Bidirectional lenses: read *and* write binary structures.

Every lens exposes ``read(transport, offset, ctx) -> (value, next_offset)`` and
``write(transport, offset, value, ctx) -> next_offset``. ``size(ctx)`` returns a
fixed byte length or ``None`` for dynamic lenses.

``ctx`` is the value namespace used to resolve cross-field references (e.g.
``Array(U8, count=lambda ctx: ctx.count)``). During a :class:`Struct` read the
live :class:`AttrsView` doubles as the ``ctx`` and is populated field-by-field so
later fields can reference earlier ones.

Offsets in :class:`AttrsView` are resolved by a one-pass recompute: nothing is
cached except parsed values and each field's own ``next_offset``. Dynamic field
extents come from the lens's ``read`` return value, never from guessed sizes.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Mapping
from typing import Any, cast

from .errors import LensError
from .transport import FileTransport, MemoryTransport, Transport

Ctx = Any
CountSpec = "int | Callable[[Ctx], int] | None"


def _instantiate_primitive(spec: Any) -> Any:
    """Turn a bare primitive class (e.g. ``U32``) into a lens instance.

    The README uses primitive classes directly inside :class:`Struct` and
    :class:`Array`; instantiating them here keeps that spelling working while
    still allowing configured instances such as ``U32(endian='>')``.
    """
    if isinstance(spec, type) and _is_lens(spec):
        return spec()
    return spec

def _is_lens(a: Any) -> bool:
    return hasattr(a, "read") and hasattr(a, "write")


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #


class Primitives:
    """Base for fixed-width numeric primitives backed by :mod:`struct`."""

    code = ""
    width = 0

    def __init__(self, endian: str = "<") -> None:
        # TODO: Add enum for basic endianness for those non-tech-savvy people
        if endian not in ("<", ">", "!"):
            raise ValueError("endian must be '<', '>' or '!'") # @result
        self.endian = endian
        self._fmt = endian + self.code
        self.width = struct.calcsize(self._fmt)

    def size(self, ctx: Ctx = None) -> int | None:
        return self.width

    def read(self, transport: Transport, offset: int, ctx: Ctx = None) -> tuple[Any, int]:
        try:
            raw = transport.read(offset, self.width)
        except LensError as exc:
            raise LensError(exc.message, _path(ctx)) from exc # @result
        (value,) = struct.unpack(self._fmt, raw)
        return value, offset + self.width

    def write(self, transport: Transport, offset: int, value: Any, ctx: Ctx = None) -> int:
        try:
            raw = struct.pack(self._fmt, value)
        except struct.error as exc:
            raise LensError(
                f"cannot pack {value!r} as {self._fmt}: {exc}", _path(ctx)
            ) from exc # @result
        transport.write(offset, raw)
        return offset + self.width

    def __repr__(self) -> str:
        return f"{type(self).__name__}(endian={self.endian!r})"


class U8(Primitives):
    code = "B"


class U16(Primitives):
    code = "H"


class U32(Primitives):
    code = "I"


class U64(Primitives):
    code = "Q"


class I8(Primitives):
    code = "b"


class I16(Primitives):
    code = "h" # As in halfword, from printf, scanf in C. Not just python-specific.


class I32(Primitives):
    code = "i"


class I64(Primitives):
    code = "q"


class F32(Primitives):
    code = "f"


class F64(Primitives):
    code = "d"


class Magic:
    """A fixed sentinel byte sequence that must match on read."""

    def __init__(self, sentinel: bytes) -> None:
        if not isinstance(sentinel, (bytes, bytearray)):
            raise TypeError("sentinel must be bytes") # @result
        self.sentinel = bytes(sentinel)
        self.width = len(self.sentinel)

    def size(self, ctx: Ctx = None) -> int | None:
        return self.width

    def read(self, transport: Transport, offset: int, ctx: Ctx = None) -> tuple[bytes, int]:
        try:
            raw = transport.read(offset, self.width)
        except LensError as exc:
            raise LensError(exc.message, _path(ctx)) from exc # @result
        if raw != self.sentinel:
            raise LensError(
                f"magic mismatch: expected {self.sentinel!r}, got {raw!r}", _path(ctx)
            ) # @result 
        return raw, offset + self.width

    def write(self, transport: Transport, offset: int, value: Any, ctx: Ctx = None) -> int:
        if value not in (None, self.sentinel):
            raise LensError(
                f"magic write value must be {self.sentinel!r}, got {value!r}", _path(ctx)
            ) # @result
        transport.write(offset, self.sentinel)
        return offset + self.width

    def __repr__(self) -> str:
        return f"Magic({self.sentinel!r})"


class Bytes:
    """A fixed-length raw byte string."""

    def __init__(self, length: int) -> None:
        self.length = length
        self.width = length

    def size(self, ctx: Ctx = None) -> int | None:
        return self.length

    def read(self, transport: Transport, offset: int, ctx: Ctx = None) -> tuple[bytes, int]:
        try:
            data = transport.read(offset, self.length)
        except LensError as exc:
            raise LensError(exc.message, _path(ctx)) from exc # @result
        return data, offset + self.length

    def write(self, transport: Transport, offset: int, value: Any, ctx: Ctx = None) -> int:
        if not isinstance(value, (bytes, bytearray)):
            raise LensError(f"expected bytes, got {type(value).__name__}", _path(ctx)) # @result
        if len(value) != self.length:
            raise LensError(f"expected {self.length} bytes, got {len(value)}", _path(ctx)) # @result
        transport.write(offset, bytes(value))
        return offset + self.length

    def __repr__(self) -> str:
        return f"Bytes({self.length})"

# Seemingly only used in tests. No user code for now.
class Padding:
    """A fixed number of pad bytes; filled with ``fill_byte`` on write."""

    def __init__(self, length: int, fill_byte: int = 0) -> None:
        self.length = length
        self.fill_byte = fill_byte
        self.width = length

    def size(self, ctx: Ctx = None) -> int | None:
        return self.length

    def read(self, transport: Transport, offset: int, ctx: Ctx = None) -> tuple[bytes, int]:
        try:
            data = transport.read(offset, self.length)
        except LensError as exc:
            raise LensError(exc.message, _path(ctx)) from exc # @result 
        return data, offset + self.length

    def write(self, transport: Transport, offset: int, value: Any, ctx: Ctx = None) -> int:
        transport.write(offset, bytes([self.fill_byte]) * self.length)
        return offset + self.length

    def __repr__(self) -> str:
        return f"Padding({self.length})"


# --------------------------------------------------------------------------- #
# Composites
# --------------------------------------------------------------------------- #


class Array:
    """A homogeneous, contiguous sequence of elements.

    ``count`` may be:

    - an ``int`` — fixed number of elements;
    - a ``callable(ctx) -> int`` — resolved against the surrounding context;
    - ``None`` — read to the end of the transport (only valid for fixed-width
        items).

    Examples:
        Array(U32, count=lambda ctx: ctx.count)
        Array(U8)
    """

    def __init__(
        self,
        item: Any,
        count: Any = None,
        *,
        name: str | None = None,
    ) -> None:
        item = _instantiate_primitive(item)
        if not _is_lens(item):
            raise TypeError("Array item must be a Lens (implement read/write)") # @result
        if count is not None and not isinstance(count, int) and not callable(count):
            raise TypeError("count must be an int, a callable(ctx)->int, or None") # @result
        if isinstance(count, int) and count < 0:
            raise ValueError("count must be non-negative") # @result
        self.item = item
        self.count = count
        self.name = name
        self.width: int | None = getattr(item, "width", None) 
        # The `width` of one element. sizeof(arr[0]) if you please.

    def _label(self) -> str:
        return self.name or type(self.item).__name__

    def _path(self, index: int | None = None) -> tuple[str | int, ...] | None:
        parts: tuple[str | int, ...] = (self.name,) if self.name else ()
        if index is not None:
            parts = (*parts, index)
        return parts or None

    def size(self, ctx: Ctx = None) -> int | None:
        if self.count is None or callable(self.count):
            return None
        item_size = self.item.size(ctx) if hasattr(self.item, "size") else self.width
        return None if item_size is None else item_size * self.count

    def _resolve_count(self, transport: Transport, offset: int, ctx: Ctx) -> int:
        if self.count is None:
            if self.width is None:
                raise LensError(
                    f"Array({self._label()}) has variable-width items and no count",
                    path=self._path(),
                ) # @result
            remaining = transport.size() - offset
            if remaining < 0 or remaining % self.width != 0:
                raise LensError(
                    f"Array({self._label()}) has {remaining} trailing bytes that "
                    f"do not align to item width {self.width}",
                    path=self._path(),
                ) # @result
            return remaining // self.width
        if callable(self.count):
            try:
                n = self.count(ctx)
            except LensError:
                raise # @result
            except Exception as exc:  # noqa: BLE001 - re-wrap with path context
                raise LensError(
                    f"Array({self._label()}) count lambda failed: {exc}",
                    path=self._path(),
                ) from exc # @result
            if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                raise LensError(
                    f"Array({self._label()}) count returned {n!r}, expected int >= 0",
                    path=self._path(),
                ) # @result
            return n
        return cast(int, self.count)

    def read(self, transport: Transport, offset: int, ctx: Ctx = None) -> tuple[list[Any], int]:
        n = self._resolve_count(transport, offset, ctx)
        out: list[Any] = []
        for i in range(n):
            try:
                value, offset = self.item.read(transport, offset, ctx)
            except LensError as exc:
                raise LensError(exc.message, path=self._path(i)) from exc # @result
            out.append(value)
        return out, offset

    def write(self, transport: Transport, offset: int, value: Any, ctx: Ctx = None) -> int:
        # A `count=None` array is read to EOF, so on write its length is the
        # value's length rather than something inferred from the destination.
        expected = (
            len(value) if self.count is None else self._resolve_count(transport, offset, ctx)
        )
        if len(value) != expected:
            raise LensError(
                f"Array({self._label()}) expected {expected} elements, got {len(value)}",
                path=self._path(),
            ) # @result
        for i, element in enumerate(value):
            try:
                offset = self.item.write(transport, offset, element, ctx)
            except LensError as exc:
                raise LensError(exc.message, path=self._path(i)) from exc # @result
        return offset

    def __repr__(self) -> str:
        return f"Array({self.item!r}, count={self.count!r})"


class Struct:
    """An ordered sequence of named fields.

    Reading returns an :class:`AttrsView`. Writing accepts an :class:`AttrsView`
    or any mapping supporting ``value[name]``.
    """

    def __init__(self, **fields: Any) -> None:
        normalized = {
            name: _instantiate_primitive(lens) for name, lens in fields.items()
        }
        for name, lens in normalized.items():
            if not _is_lens(lens):
                raise TypeError(f"field {name!r} is not a Lens") # @result
        self.fields = normalized

    # Returns None if there's any variadic length Attrs
    def size(self, ctx: Ctx = None) -> int | None:
        total = 0
        for lens in self.fields.values():
            s = lens.size(ctx) if hasattr(lens, "size") else None
            if s is None:
                return None
            total += s
        return total

    def read(self, transport: Transport, offset: int, ctx: Ctx = None) -> tuple[AttrsView, int]:
        view = AttrsView(transport, self, start=offset)
        cur = offset
        for name, lens in self.fields.items():
            try:
                value, cur = lens.read(transport, cur, view)
            except LensError as exc:
                raise LensError(exc.message, _path(ctx) + (name,)) from exc # @result
            view._store(name, value, cur)
        view._end = cur
        return view, cur

    def write(self, transport: Transport, offset: int, value: Any, ctx: Ctx = None) -> int:
        cur = offset
        for name, lens in self.fields.items():
            try:
                field_value = value[name]
            except (KeyError, TypeError) as exc:
                raise LensError(f"missing field {name!r}", _path(ctx) + (name,)) from exc # @result
            cur = lens.write(transport, cur, field_value, view_ctx(value, ctx))
        return cur

    def __repr__(self) -> str:
        inner = ", ".join(f"{k}={v!r}" for k, v in self.fields.items())
        return f"Struct({inner})"


# --------------------------------------------------------------------------- #
# AttrsView
# --------------------------------------------------------------------------- #


class AttrsView:
    """Live view over a :class:`Struct` in a transport.

    Attribute and item access are equivalent (``view.header`` == ``view["header"]``).
    Setting a field writes it back through the transport (write-through).
    Offsets are recomputed on each access via a single forward pass; dynamic
    field extents come from each lens's ``read`` return value.

    Args:
        transport: Backing transport.
        struct: The :class:`Struct` describing the layout.
        start: Byte offset where the struct begins.
    """

    __slots__ = ("_transport", "_struct", "_start", "_end", "_values", "_ends")

    def __init__(self, transport: Transport, struct: Struct, start: int) -> None:
        object.__setattr__(self, "_transport", transport)
        object.__setattr__(self, "_struct", struct)
        object.__setattr__(self, "_start", start)
        object.__setattr__(self, "_end", start)
        object.__setattr__(self, "_values", {})
        object.__setattr__(self, "_ends", {})

    # -- internal ---------------------------------------------------------- #
    def _store(self, name: str, value: Any, next_offset: int) -> None:
        object.__getattribute__(self, "_values")[name] = value
        object.__getattribute__(self, "_ends")[name] = next_offset

    def _locate(self, target: str) -> tuple[Any, int]:
        struct = object.__getattribute__(self, "_struct")
        if target not in struct.fields:
            raise KeyError(target) # @result
        cur = object.__getattribute__(self, "_start")
        for name, lens in struct.fields.items():
            if name == target:
                return lens, cur
            cur = self._advance(name, lens, cur)
        raise KeyError(target)  # pragma: no cover # @result

    def _advance(self, name: str, lens: Any, offset: int) -> int:
        values = object.__getattribute__(self, "_values")
        ends = object.__getattribute__(self, "_ends")
        transport = object.__getattribute__(self, "_transport")
        if name in values:
            size = (
                cast("int | None", lens.size(self)) if hasattr(lens, "size") else None
            )
            if size is not None:
                return offset + size
            return cast(int, ends[name])
        value, nxt = cast("tuple[Any, int]", lens.read(transport, offset, self))
        self._store(name, value, nxt)
        return nxt

    # -- attribute / item access ------------------------------------------- #
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name) # @result
        struct = object.__getattribute__(self, "_struct")
        if name not in struct.fields:
            raise AttributeError(f"{name!r} is not a field of this struct") # @result
        values = object.__getattribute__(self, "_values")
        if name in values:
            return values[name]
        lens, offset = self._locate(name)
        transport = object.__getattribute__(self, "_transport")
        value, nxt = lens.read(transport, offset, self)
        self._store(name, value, nxt)
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        struct = object.__getattribute__(self, "_struct")
        if name not in struct.fields:
            raise AttributeError(f"{name!r} is not a field of this struct") # @result
        lens, offset = self._locate(name)
        transport = object.__getattribute__(self, "_transport")
        nxt = lens.write(transport, offset, value, self)
        self._store(name, value, nxt)

    def __getitem__(self, name: str) -> Any:
        return getattr(self, name)

    def __setitem__(self, name: str, value: Any) -> None:
        setattr(self, name, value)

    def save(self, file_name: str | os.PathLike[str]) -> None:
        """Serialize this struct and write it to ``file_name``.

        The struct is re-serialized from its current field values, so the
        destination is byte-for-byte what :meth:`Struct.write` would produce.
        Existing files are overwritten.

        Args:
            file_name: Destination path.
        """
        buf = MemoryTransport()
        struct = object.__getattribute__(self, "_struct")
        struct.write(buf, 0, self, self)
        dest = FileTransport.open(file_name, "wb")
        try:
            dest.write(0, buf.to_bytes())
        finally:
            dest.close()

    # -- introspection ----------------------------------------------------- #
    def keys(self) -> list[str]:
        """Return field names in declaration order."""
        return list(object.__getattribute__(self, "_struct").fields)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict snapshot."""
        return {k: getattr(self, k) for k in self.keys()}

    def __repr__(self) -> str:
        parts = []
        for k in self.keys():
            try:
                parts.append(f"{k}={_debug(getattr(self, k))}")
            except LensError as exc:
                parts.append(f"{k}=<error: {exc.message}>")
        return f"AttrsView({', '.join(parts)})"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class _MappingCtx:
    """Attribute access over a plain mapping, used as a write context."""

    __slots__ = ("_mapping",)

    def __init__(self, mapping: Mapping[str, Any]) -> None:
        self._mapping = mapping

    def __getattr__(self, name: str) -> Any:
        try:
            return self._mapping[name]
        except KeyError:
            raise AttributeError(name) from None # @result


def view_ctx(value: Any, ctx: Ctx) -> Ctx:
    """Use a Struct value as its own write context, else pass through.

    Accepts both an :class:`AttrsView` (read from a transport) and a plain
    mapping (write from a dict), so dynamic fields resolve the same way on
    both sides of a round-trip.
    """
    if isinstance(value, AttrsView):
        return value
    if isinstance(value, Mapping):
        return _MappingCtx(value)
    return ctx


def _debug(value: Any) -> str:
    """Format a value for a debug-friendly repr."""
    if isinstance(value, (bytes, bytearray)):
        b = bytes(value)
        if len(b) <= 16:
            try:
                text = b.decode("ascii")
                if text.isprintable():
                    return f"{b!r} ({text!r})"
            except UnicodeDecodeError:
                pass
            return repr(b)
        return f"<{len(b)} bytes {b[:8].hex()}...>"
    if isinstance(value, list):
        if len(value) <= 12:
            return repr(value)
        head = ", ".join(repr(v) for v in value[:12])
        return f"[{head}, ... {len(value)} items]"
    if isinstance(value, AttrsView):
        return repr(value)
    return repr(value)


def _path(ctx: Ctx) -> tuple[str | int, ...]:
    """Return the accumulated path from a context, or empty."""
    path = getattr(ctx, "_path", None)
    return path if isinstance(path, tuple) else ()
