"""Tests for format plugins, detection, and the built-in WAV plugin."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

import beenary.formats as formats
from beenary import detect, get_plugin, open_file, plugins, register_plugin, sniff
from beenary.errors import FormatError, LensError
from beenary.formats import (
    GenericPlugin,
    PngPlugin,
    ToyPlugin,
    WavPlugin,
    _registry,
)
from beenary.transport import MemoryTransport

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def make_wav(
    *,
    fmt_tag: int = 1,
    channels: int = 1,
    sample_rate: int = 8000,
    bits: int = 8,
    data: bytes = b"\x80\x81",
    extra: tuple[tuple[bytes, bytes], ...] = (),
    fmt_extra: bytes = b"",
    include_fmt: bool = True,
) -> bytes:
    block_align = channels * bits // 8
    fmt = struct.pack(
        "<HHIIHH",
        fmt_tag,
        channels,
        sample_rate,
        sample_rate * block_align,
        block_align,
        bits,
    ) + fmt_extra

    chunks = [(b"fmt ", fmt)] if include_fmt else []
    chunks.extend([*extra, (b"data", data)])
    body = b"WAVE"
    for chunk_id, payload in chunks:
        body += chunk_id + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            body += b"\x00"
    return b"RIFF" + struct.pack("<I", len(body)) + body


def make_png(width: int = 2, height: int = 3) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"
    return PNG_SIGNATURE + chunk + b"rest"


class TestRegistry:
    def test_builtins_are_registered(self) -> None:
        names = [plugin.name for plugin in plugins()]
        assert names == ["generic", "png", "toy", "wav"]

    def test_register_accepts_class(self) -> None:
        class Jsonish(WavPlugin):
            name = "jsonish"

        plugin = register_plugin(Jsonish)
        try:
            assert get_plugin("jsonish") is plugin
        finally:
            _registry.pop("jsonish")

    def test_register_accepts_instance(self) -> None:
        plugin = PngPlugin()
        assert register_plugin(plugin) is get_plugin("png")

    def test_duplicate_name_keeps_existing_plugin(self) -> None:
        class OtherPng(PngPlugin):
            name = "png"

        original = get_plugin("png")
        assert register_plugin(OtherPng) is original
        assert get_plugin("png") is original

    def test_replace_swaps_plugin(self) -> None:
        original = get_plugin("png")
        replacement = PngPlugin()
        try:
            assert register_plugin(replacement, replace=True) is replacement
            assert get_plugin("png") is replacement
        finally:
            register_plugin(original, replace=True)

    def test_rejects_non_plugin(self) -> None:
        with pytest.raises(TypeError, match="FormatPlugin"):
            register_plugin(object)  # type: ignore[arg-type]

    def test_get_unknown_plugin(self) -> None:
        with pytest.raises(KeyError, match="nope"):
            get_plugin("nope")

    def test_entry_points_load_and_failures_are_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Local(WavPlugin):
            name = "local"
            schema_id = "test.local"

        class Entry:
            name = "local"

            def load(self) -> type[WavPlugin]:
                return Local

        class BadEntry:
            name = "bad"

            def load(self) -> None:
                raise RuntimeError("no plugin here")

        monkeypatch.setattr(
            formats.importlib.metadata,
            "entry_points",
            lambda **kwargs: [Entry(), BadEntry()],
        )
        monkeypatch.setattr(formats, "_discovered", False)
        try:
            assert "local" in [plugin.name for plugin in plugins()]
        finally:
            _registry.pop("local", None)


class TestSniff:
    def test_wav_scores_above_generic(self) -> None:
        scores = sniff(MemoryTransport(make_wav()))
        assert scores[0] == ("wav", 0.99)
        assert ("generic", 0.01) in scores

    def test_png_scores_above_generic(self) -> None:
        scores = sniff(MemoryTransport(make_png()))
        assert scores[0] == ("png", 0.99)

    def test_toy_scores_above_generic(self) -> None:
        scores = sniff(MemoryTransport(b"TOY1\x02\x00\x00\x00\x00"))
        assert scores[0] == ("toy", 0.9)

    def test_unknown_file_falls_back_to_generic(self) -> None:
        assert sniff(MemoryTransport(b"nothing here")) == [("generic", 0.01)]

    def test_sniff_accepts_path(self, tmp_path: Path) -> None:
        path = tmp_path / "sample.wav"
        path.write_bytes(make_wav())
        assert sniff(path)[0] == ("wav", 0.99)

    def test_broken_plugin_is_skipped(self) -> None:
        class Broken(WavPlugin):
            name = "broken"

            def sniff(self, transport: MemoryTransport) -> float:
                raise RuntimeError("boom")

        register_plugin(Broken)
        try:
            assert sniff(MemoryTransport(make_wav()))[0] == ("wav", 0.99)
        finally:
            _registry.pop("broken")

    def test_detect_returns_plugin(self) -> None:
        plugin = detect(MemoryTransport(make_wav()))
        assert isinstance(plugin, WavPlugin)

    def test_ties_sort_by_name(self) -> None:
        class Zed(WavPlugin):
            name = "zed"

            def sniff(self, transport: MemoryTransport) -> float:
                return 0.5

        class Alpha(WavPlugin):
            name = "alpha"

            def sniff(self, transport: MemoryTransport) -> float:
                return 0.5

        register_plugin(Zed)
        register_plugin(Alpha)
        try:
            names = [name for name, score in sniff(MemoryTransport(b"")) if score == 0.5]
            assert names == ["alpha", "zed"]
        finally:
            _registry.pop("zed")
            _registry.pop("alpha")


class TestWavPlugin:
    def read(self, data: bytes) -> object:
        return WavPlugin().read(MemoryTransport(data))

    def test_reads_every_chunk(self) -> None:
        view = self.read(make_wav(extra=((b"LIST", b"INFOabc"),)))
        assert [chunk.id for chunk in view.chunks] == [b"fmt ", b"LIST", b"data"]  # type: ignore[attr-defined]

    def test_audio_info_pcm(self) -> None:
        view = self.read(make_wav(channels=2, sample_rate=44100, bits=16))
        info = WavPlugin.audio_info(view)  # type: ignore[arg-type]
        assert info["audio_format"] == 1
        assert info["channels"] == 2
        assert info["sample_rate"] == 44100
        assert info["bits_per_sample"] == 16

    def test_audio_info_reads_cb_size(self) -> None:
        view = self.read(make_wav(fmt_extra=struct.pack("<H", 0)))
        info = WavPlugin.audio_info(view)  # type: ignore[arg-type]
        assert info["cb_size"] == 0

    def test_audio_info_extensible(self) -> None:
        fmt_extra = struct.pack("<HHI", 22, 24, 3) + struct.pack("<H", 1) + b"\x00" * 14
        view = self.read(make_wav(fmt_tag=0xFFFE, bits=24, fmt_extra=fmt_extra))
        info = WavPlugin.audio_info(view)  # type: ignore[arg-type]
        assert info["audio_format"] == 0xFFFE
        assert info["valid_bits"] == 24
        assert info["channel_mask"] == 3
        assert info["subformat"] == 1

    def test_odd_chunk_padding_is_preserved(self) -> None:
        view = self.read(make_wav(data=b"\x01\x02\x03"))
        chunk = view.chunks[-1]  # type: ignore[attr-defined]
        assert chunk.data == b"\x01\x02\x03"
        assert chunk.pad == b"\x00"

    def test_roundtrip_is_byte_exact(self) -> None:
        data = make_wav(
            data=b"\x01\x02\x03",
            extra=((b"cue ", b"abc"), (b"junk", b"xyz")),
        )
        view = self.read(data)
        out = MemoryTransport()
        WavPlugin().schema().write(out, 0, view)
        assert out.to_bytes() == data

    def test_missing_fmt_chunk_raises(self) -> None:
        view = self.read(make_wav(include_fmt=False))
        with pytest.raises(FormatError, match="no fmt chunk"):
            WavPlugin.audio_info(view)  # type: ignore[arg-type]

    def test_truncated_file_raises(self) -> None:
        data = make_wav()
        with pytest.raises(LensError, match="exceeds file size"):
            self.read(data[:-1])

    def test_truncated_chunk_header_raises(self) -> None:
        body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + b"\x00" * 16 + b"da"
        data = b"RIFF" + struct.pack("<I", len(body)) + body
        with pytest.raises(LensError, match="truncated chunk header"):
            self.read(data)

    def test_open_file_detects_wav(self, tmp_path: Path) -> None:
        path = tmp_path / "sound.wav"
        path.write_bytes(make_wav(data=b"\x01\x02\x03\x04"))
        doc = open_file(path)
        try:
            assert doc.chunks[0].id == b"fmt "
            assert WavPlugin.audio_info(doc)["sample_rate"] == 8000
        finally:
            doc.close()


class TestBuiltinPlugins:
    def test_generic_reads_whole_file(self) -> None:
        view = GenericPlugin().read(MemoryTransport(b"abc"))
        assert view.data == b"abc"  # type: ignore[attr-defined]

    def test_png_header(self) -> None:
        view = PngPlugin().read(MemoryTransport(make_png(width=7, height=9)))
        assert view.width == 7  # type: ignore[attr-defined]
        assert view.height == 9  # type: ignore[attr-defined]
        assert view.rest == b"rest"  # type: ignore[attr-defined]

    def test_toy_header(self) -> None:
        view = ToyPlugin().read(MemoryTransport(b"TOY1\x02\x00\x2a\x00\x00\x00"))
        assert view.version == 2  # type: ignore[attr-defined]
        assert view.payload == 42  # type: ignore[attr-defined]

    def test_png_roundtrip(self) -> None:
        data = make_png()
        view = PngPlugin().read(MemoryTransport(data))
        out = MemoryTransport()
        PngPlugin().schema().write(out, 0, view)
        assert out.to_bytes() == data

    def test_short_file_does_not_crash_sniff(self) -> None:
        assert sniff(MemoryTransport(b"")) == [("generic", 0.01)]

    def test_open_file_falls_back_to_generic(self, tmp_path: Path) -> None:
        path = tmp_path / "raw.bin"
        path.write_bytes(b"abc")
        doc = open_file(path)
        try:
            assert doc.data == b"abc"  # type: ignore[attr-defined]
        finally:
            doc.close()
