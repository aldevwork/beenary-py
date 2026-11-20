"""Format detection plugins.

A plugin answers two questions: how much does this file look like my format
(``sniff``), and what lens describes it (``schema``). The registry discovers
plugins from the ``beenary.formats`` entry-point group, so a plugin package
only needs one module and one ``pyproject.toml`` entry.

A broken plugin is logged and skipped by :func:`sniff`. One bad package must
not break detection for every other format.
"""

from __future__ import annotations

import importlib.metadata
import logging
import os
import struct
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from .errors import FormatError, LensError
from .lens import U8, U16, U32, AttrsView, Bytes, Magic, Struct
from .transport import FileTransport, Transport

ENTRY_POINT_GROUP = "beenary.formats"

_LOG = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Plugin contract
# --------------------------------------------------------------------------- #


class FormatPlugin(ABC):
    """Base class for a format plugin.

    Subclasses set :attr:`name` and :attr:`schema_id`, then implement
    :meth:`sniff` and :meth:`schema`. That is the whole contract.

    Examples:
        class ToyPlugin(FormatPlugin):
            name = "toy"
            schema_id = "mypkg.toy"

            def sniff(self, transport):
                return 1.0 if transport.read(0, 4) == b"TOY1" else 0.0

            def schema(self):
                return Struct(magic=Magic(b"TOY1"), version=U16)
    """

    name: ClassVar[str]
    schema_id: ClassVar[str]

    @abstractmethod
    def sniff(self, transport: Transport) -> float:
        """Return confidence from ``0.0`` (no match) to ``1.0`` (certain)."""

    @abstractmethod
    def schema(self) -> Struct:
        """Return the lens that reads and writes this format."""

    def read(self, transport: Transport, offset: int = 0) -> AttrsView:
        """Read this format at ``offset`` and return its view."""
        value, _ = self.schema().read(transport, offset)
        return value


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

_registry: dict[str, FormatPlugin] = {}
_discovered = False


def register_plugin(
    plugin: FormatPlugin | type[FormatPlugin],
    *,
    replace: bool = False,
) -> FormatPlugin:
    """Add a plugin to the registry.

    Args:
        plugin: A plugin instance or subclass.
        replace: Replace an existing plugin with the same name instead of
            keeping the registered one.

    Returns:
        The registered plugin instance.
    """
    instance = plugin() if isinstance(plugin, type) else plugin
    if not isinstance(instance, FormatPlugin):
        raise TypeError("plugin must be a FormatPlugin instance or subclass")
    if not instance.name or not instance.schema_id:
        raise ValueError("plugin needs a non-empty name and schema_id")
    if instance.name in _registry and not replace:
        return _registry[instance.name]
    _registry[instance.name] = instance
    return instance


def get_plugin(name: str) -> FormatPlugin:
    """Return the registered plugin named ``name``."""
    discover_plugins()
    try:
        return _registry[name]
    except KeyError:
        raise KeyError(f"unknown format plugin {name!r}") from None


def plugins() -> tuple[FormatPlugin, ...]:
    """Return every known plugin, sorted by name for deterministic output."""
    discover_plugins()
    return tuple(_registry[name] for name in sorted(_registry))


def discover_plugins() -> None:
    """Load plugins from the ``beenary.formats`` entry-point group once.

    Loading is lazy: importing :mod:`beenary` does not import third-party
    plugin modules. A failing entry point is logged and skipped.
    """
    global _discovered
    if _discovered:
        return
    _discovered = True

    for entry in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        try:
            register_plugin(entry.load())
        except Exception as exc:  # noqa: BLE001 - plugin isolation is the point
            _LOG.warning("skipping format plugin %r: %s", entry.name, exc)


def sniff(source: str | os.PathLike[str] | Transport) -> list[tuple[str, float]]:
    """Score every plugin against ``source``.

    Args:
        source: A path or an already-open :class:`~beenary.transport.Transport`.

    Returns:
        ``(name, score)`` pairs with ``score > 0``, highest first. Ties are
        broken by name, so identical input always produces identical output.
    """
    if isinstance(source, Transport):
        return _sniff_transport(source)

    transport = FileTransport.open(source, "rb")
    try:
        return _sniff_transport(transport)
    finally:
        transport.close()


def _sniff_transport(transport: Transport) -> list[tuple[str, float]]:
    discover_plugins()
    results: list[tuple[str, float]] = []

    for plugin in plugins():
        try:
            score = float(plugin.sniff(transport))
        except Exception as exc:  # noqa: BLE001 - a plugin must not break sniff
            _LOG.warning("format plugin %r failed during sniff: %s", plugin.name, exc)
            continue
        if 0.0 < score <= 1.0:
            results.append((plugin.name, score))

    results.sort(key=lambda item: (-item[1], item[0]))
    return results


def detect(source: str | os.PathLike[str] | Transport) -> FormatPlugin | None:
    """Return the highest-scoring plugin for ``source``, or ``None``."""
    scores = sniff(source)
    return get_plugin(scores[0][0]) if scores else None


# --------------------------------------------------------------------------- #
# Built-in plugins
# --------------------------------------------------------------------------- #


class _RestBytes:
    """Read or write every byte from ``offset`` to the end."""

    def size(self, ctx: Any = None) -> int | None:
        return None

    def read(self, transport: Transport, offset: int, ctx: Any = None) -> tuple[bytes, int]:
        data = transport.read(offset, transport.size() - offset)
        return data, offset + len(data)

    def write(self, transport: Transport, offset: int, value: Any, ctx: Any = None) -> int:
        data = bytes(value)
        transport.write(offset, data)
        return offset + len(data)


class GenericPlugin(FormatPlugin):
    """Fallback plugin: treat the whole file as raw bytes."""

    name = "generic"
    schema_id = "beenary.generic"

    def sniff(self, transport: Transport) -> float:
        return 0.01

    def schema(self) -> Struct:
        return Struct(data=_RestBytes())


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PngPlugin(FormatPlugin):
    """PNG header plus the first ``IHDR`` chunk.

    The remaining chunks are kept as raw bytes in ``rest`` so a read and write
    round-trip does not drop image data.
    """

    name = "png"
    schema_id = "beenary.png"

    def sniff(self, transport: Transport) -> float:
        if transport.size() < len(PNG_SIGNATURE):
            return 0.0
        return 0.99 if transport.read(0, len(PNG_SIGNATURE)) == PNG_SIGNATURE else 0.0

    def schema(self) -> Struct:
        return Struct(
            signature=Magic(PNG_SIGNATURE),
            ihdr_length=U32(endian=">"),
            ihdr_type=Magic(b"IHDR"),
            width=U32(endian=">"),
            height=U32(endian=">"),
            bit_depth=U8,
            color_type=U8,
            compression=U8,
            filter=U8,
            interlace=U8,
            crc=Bytes(4),
            rest=_RestBytes(),
        )


class ToyPlugin(FormatPlugin):
    """Small example format from the documentation."""

    name = "toy"
    schema_id = "beenary.toy"

    def sniff(self, transport: Transport) -> float:
        if transport.size() < 4:
            return 0.0
        return 0.9 if transport.read(0, 4) == b"TOY1" else 0.0

    def schema(self) -> Struct:
        return Struct(magic=Magic(b"TOY1"), version=U16, payload=U32)


class _SizedBytes:
    """Read or write exactly ``ctx.size`` bytes.

    Used by the RIFF chunk struct so a chunk payload does not need a fixed
    ``Bytes(n)`` length.
    """

    def size(self, ctx: Any = None) -> int | None:
        return None

    def read(self, transport: Transport, offset: int, ctx: Any = None) -> tuple[bytes, int]:
        size = _chunk_size(ctx)
        return transport.read(offset, size), offset + size

    def write(self, transport: Transport, offset: int, value: Any, ctx: Any = None) -> int:
        size = _chunk_size(ctx)
        data = bytes(value)
        if len(data) != size:
            raise LensError(f"chunk payload expected {size} bytes, got {len(data)}")
        transport.write(offset, data)
        return offset + size


class _ChunkPad:
    """RIFF pads odd-sized chunks with one byte."""

    def size(self, ctx: Any = None) -> int:
        return 1 if ctx is not None and _chunk_size(ctx) % 2 else 0

    def read(self, transport: Transport, offset: int, ctx: Any = None) -> tuple[bytes, int]:
        length = self.size(ctx)
        return transport.read(offset, length), offset + length

    def write(self, transport: Transport, offset: int, value: Any, ctx: Any = None) -> int:
        length = self.size(ctx)
        raw = bytes(value) if isinstance(value, (bytes, bytearray)) else b""
        if len(raw) != length:
            raw = b"\x00" * length
        transport.write(offset, raw)
        return offset + length


def _chunk_size(ctx: Any) -> int:
    size = getattr(ctx, "size", None)
    if not isinstance(size, int):
        raise LensError("RIFF chunk size is missing from context")
    return size


RiffChunk = Struct(
    id=Bytes(4),
    size=U32,
    data=_SizedBytes(),
    pad=_ChunkPad(),
)


class _RiffChunks:
    """All chunks between the ``WAVE`` tag and the end of the RIFF block."""

    def size(self, ctx: Any = None) -> int | None:
        return None

    def read(
        self, transport: Transport, offset: int, ctx: Any = None
    ) -> tuple[list[AttrsView], int]:
        riff_size = getattr(ctx, "riff_size", None)
        if not isinstance(riff_size, int):
            raise LensError("RIFF size is missing from context")
        if riff_size < 4:
            raise LensError(f"RIFF size {riff_size} is too small for a WAVE header")
        end = 8 + riff_size
        if end > transport.size():
            raise LensError(
                f"RIFF size {riff_size} exceeds file size {transport.size()}"
            )

        chunks: list[AttrsView] = []
        while offset < end:
            if end - offset < 8:
                raise LensError(f"truncated chunk header at offset {offset}")
            size = int.from_bytes(transport.read(offset + 4, 4), "little")
            padded = size + (size & 1)
            if offset + 8 + padded > end:
                raise LensError(
                    f"chunk at offset {offset} claims {size} bytes past RIFF end"
                )
            chunk, offset = RiffChunk.read(transport, offset, ctx)
            chunks.append(chunk)

        return chunks, offset

    def write(self, transport: Transport, offset: int, value: Any, ctx: Any = None) -> int:
        start = offset
        for chunk in value:
            offset = RiffChunk.write(transport, offset, chunk, ctx)

        riff_size = getattr(ctx, "riff_size", None)
        if isinstance(riff_size, int) and offset - start + 4 != riff_size:
            raise LensError(
                f"RIFF size {riff_size} does not match chunk bytes {offset - start + 4}"
            )
        return offset


class WavPlugin(FormatPlugin):
    """RIFF/WAVE audio, including every chunk and odd-size padding.

    Chunks stay in file order as ``view.chunks``. Each chunk has ``id``,
    ``size``, ``data``, and ``pad``. Unknown chunks are preserved, so reading
    and writing back does not lose metadata. Use :meth:`audio_info` to decode
    the ``fmt `` chunk.
    """

    name = "wav"
    schema_id = "beenary.wav"

    def sniff(self, transport: Transport) -> float:
        if transport.size() < 12:
            return 0.0
        if transport.read(0, 4) != b"RIFF" or transport.read(8, 4) != b"WAVE":
            return 0.0
        return 0.99

    def schema(self) -> Struct:
        return Struct(
            riff=Magic(b"RIFF"),
            riff_size=U32,
            wave=Magic(b"WAVE"),
            chunks=_RiffChunks(),
        )

    @staticmethod
    def audio_info(view: AttrsView) -> dict[str, Any]:
        """Decode the ``fmt `` chunk into readable keys.

        Handles plain PCM, IEEE float, A-law, mu-law, and WAVE_FORMAT_EXTENSIBLE
        headers. Raises :class:`FormatError` if the chunk is missing or too
        short to be a valid format chunk.
        """
        for chunk in view.chunks:
            if chunk.id != b"fmt ":
                continue

            data = chunk.data
            if len(data) < 16:
                raise FormatError(f"fmt chunk is {len(data)} bytes, need at least 16")

            (
                audio_format,
                channels,
                sample_rate,
                byte_rate,
                block_align,
                bits_per_sample,
            ) = struct.unpack_from("<HHIIHH", data)

            info: dict[str, Any] = {
                "audio_format": audio_format,
                "channels": channels,
                "sample_rate": sample_rate,
                "byte_rate": byte_rate,
                "block_align": block_align,
                "bits_per_sample": bits_per_sample,
            }

            if audio_format == 0xFFFE and len(data) >= 40:
                cb_size, valid_bits, channel_mask, subformat = struct.unpack_from(
                    "<HHIH", data, 16
                )
                info.update(
                    cb_size=cb_size,
                    valid_bits=valid_bits,
                    channel_mask=channel_mask,
                    subformat=subformat,
                )
            elif len(data) >= 18:
                info["cb_size"] = struct.unpack_from("<H", data, 16)[0]
                if len(data) > 18:
                    info["extra"] = data[18:]

            return info

        raise FormatError("WAV file has no fmt chunk")


for _plugin in (GenericPlugin, PngPlugin, ToyPlugin, WavPlugin):
    register_plugin(_plugin)
