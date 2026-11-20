"""Inspect a WAV file through the built-in WavPlugin.

Run with ``python -m examples.wav``.
"""

from __future__ import annotations

from pathlib import Path

from beenary import detect, open_file, sniff
from beenary.formats import WavPlugin

WAV_PATH = Path(__file__).with_name("a.wav")


def main() -> None:
    print("scores:", sniff(WAV_PATH))
    plugin = detect(WAV_PATH)
    assert isinstance(plugin, WavPlugin)

    with open_file(WAV_PATH, mode="rb") as wav:
        info = WavPlugin.audio_info(wav)
        print(
            f"{info['channels']} channels, "
            f"{info['sample_rate']} Hz, "
            f"{info['bits_per_sample']} bits"
        )

        for chunk in wav.chunks:
            print(f"{chunk.id.decode('ascii', 'replace'):4} {chunk.size:>8} bytes")


if __name__ == "__main__":
    main()
