import pathlib

ARRAY = '''

class Array:
    """A homogeneous, contiguous sequence of elements.

    The element lens is applied repeatedly, back to back. The number of
    elements is either fixed (``count`` is an ``int``), computed lazily from
    the read context (``count`` is a ``callable(ctx) -> int``), or inferred by
    reading until the transport is exhausted (``count=None`` and the item lens
    has a fixed width).

    This matches the README usage::

        Array(U32, count=lambda ctx: ctx.count)   # length from a sibling field
        Array(U8)                                 # read to end of transport

    Attributes:
        item: The element lens.
        count: The count spec passed to the constructor.
        name: The optional field name, used to build error paths.
        width: ``item.width`` when the item lens is fixed-width, else ``None``.
    """

    def __init__(
        self,
        item: Any,
        count: int | Callable[[Any], int] | None = None,
        *,
        name: str | None = None,
    ) -> None:
        if not (hasattr(item, "read") and hasattr(item, "write")):
            raise TypeError("Array item must be a Lens (implement read/write)")
        if count is not None and not isinstance(count, int) and not callable(count):
            raise TypeError("count must be an int, a callable(ctx)->int, or None")
        if isinstance(count, int) and count < 0:
            raise ValueError("count must be non-negative")
        self.item = item
        self.count = count
        self.name = name
        self.width: int | None = getattr(item, "width", None)

    def _label(self) -> str:
        return self.name or type(self.item).__name__

    def _path(self, index: int | None = None) -> tuple[str | int, ...] | None:
        parts: tuple[str | int, ...] = (self.name,) if self.name else ()
        if index is not None:
            parts = (*parts, index)
        return parts or None

    def _resolve_count(self, transport: Transport, offset: int, ctx: object) -> int:
        """Determine how many elements to read/write.

        Args:
            transport: Source/sink, used to compute remaining bytes when
                reading to exhaustion.
            offset: Current position.
            ctx: Struct view used to evaluate a callable ``count``.

        Returns:
            A non-negative element count.

        Raises:
            LensError: If the count cannot be determined or is invalid.
        """
        if self.count is None:
            if self.width is None:
                raise LensError(
                    f"Array({self._label()}) has variable-width items and no "
                    "count; cannot infer a length",
                    path=self._path(),
                )
            remaining = transport.size() - offset
            if remaining < 0 or remaining % self.width != 0:
                raise LensError(
                    f"Array({self._label()}) has {remaining} trailing bytes that "
                    f"do not align to item width {self.width}",
                    path=self._path(),
                )
            return remaining // self.width

        if callable(self.count):
            try:
                n = self.count(ctx)
            except LensError:
                raise
            except Exception as exc:  # noqa: BLE001 - re-wrap with path context
                raise LensError(
                    f"Array({self._label()}) count lambda failed: {exc}",
                    path=self._path(),
                ) from exc
            if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                raise LensError(
                    f"Array({self._label()}) count lambda returned {n!r}, "
                    "expected int >= 0",
                    path=self._path(),
                )
            return n

        return self.count

    def read(self, transport: Transport, offset: int, ctx: object) -> tuple[list[Any], int]:
        """Read ``count`` items, returning ``(values, next_offset)``."""
        n = self._resolve_count(transport, offset, ctx)
        out: list[Any] = []
        for i in range(n):
            try:
                value, offset = self.item.read(transport, offset, ctx)
            except LensError as exc:
                raise LensError(exc.message, path=self._path(i)) from exc
            out.append(value)
        return out, offset

    def write(
        self,
        transport: Transport,
        offset: int,
        value: list[Any] | tuple[Any, ...],
        ctx: object,
    ) -> int:
        """Write each element in order, returning the next offset."""
        expected = self._resolve_count(transport, offset, ctx)
        if len(value) != expected:
            raise LensError(
                f"Array({self._label()}) expected {expected} elements, "
                f"got {len(value)}",
                path=self._path(),
            )
        for i, element in enumerate(value):
            try:
                offset = self.item.write(transport, offset, element, ctx)
            except LensError as exc:
                raise LensError(exc.message, path=self._path(i)) from exc
        return offset
'''

p = pathlib.Path("beenary/lens.py")
text = p.read_text(encoding="utf-8")
if "class Array" not in text:
    p.write_text(text.rstrip("\n") + "\n" + ARRAY, encoding="utf-8")
print("appended")
