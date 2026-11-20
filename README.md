# beenary.py

> **All you need, diffs - bidirectional lenses, all in pure Python without any DSLs**

[![CI](https://github.com/aldevwork/beenary-py/actions/workflows/ci.yml/badge.svg)](https://github.com/aldevwork/beenary-py/actions)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Beenary** is a library for working with binary files as if it's your second language. It lets you treat binary files as living structures in your python code.

*Beenary reads and writes binary files through lenses. A lens turns bytes at an offset into a Python value and writes that value back. Schemas are ordinary Python objects, so there is no schema language, parser generator, or generated code.*

*Version 0.1.0 includes lenses, transports, live struct views, saving, structural diff and patch, and format plugins. Protocol dialogues and the CLI are planned and not in this release. Their intended APIs are in [FUTURE.md](./FUTURE.md).*

## Why another binary library?

Existing tools split into two camps:

- **Declarative mappers** (`KonFoo`, `rombob`, `Kaitai Struct`) — great for
  read-only parsing, awkward for writing and diffing.
- **Hex editors** — precise but blind to structure.

`beenary.py` fills the gap: **bidirectional by design**, **structural diff/patch**,
and **pluggable format detection**, in a small pure-Python core.

| Feature                    | beenary | KonFoo | Kaitai | construct |
|----------------------------|:-------:|:------:|:------:|:---------:|
| Read                       |    ✅   |   ✅   |   ✅   |    ✅     |
| Write / round-trip         |    ✅   |   ~    |   ❌   |    ✅     |
| Structural diff / patch    |    ✅   |   ❌   |   ❌   |    ❌     |
| Plugin-based format sniff  |    ✅   |   ❌   |   ~    |    ❌     |
| No DSL (plain Python)      |    ✅   |   ✅   |   ❌   |    ✅     |
| Dialogue / stream mode     |    ✅   |   ❌   |   ❌   |    ~      |


## Design

- **Bidirectional by design.** A lens reads and writes. Round-trip tests cover both directions.
- **No DSLs.** Schemas are plain Python objects. There is no schema language.
- **Transport-agnostic I/O.** All byte access goes through a transport. Lenses do not open files or sockets.
- **No hidden save files.** Only explicit saving. Field assignment writes through to the open transport. Saving to a chosen path is always explicit.
- **Structural over byte-level.** Diff and patch use field paths, not byte offsets.

## Creation

Made with using AI, but reread, picked by hand and engineered by [aldevwork](https://github.com/aldevwork). All code was read, handpicked and checked by a human. And so the API, features and design principles are ALSO hand picked and perfected by a human.

## Install

From a checkout:

```bash
python -m pip install -e .
python -m pip install -e ".[dev]"   # tests and tooling
```

Requires Python 3.11 or newer. The core has no runtime dependencies.

## Define a schema

```python
from beenary import open_file
from beenary.lens import Array, Bytes, Magic, Struct, U8, U16, U32

Schema = Struct(
    header=Struct(
        magic=Magic(b"BEEN"),
        version=U8,
        flags=U16,
    ),
    count=U32,
    items=Array(U32, count=lambda ctx: ctx.count),
    trailer=Bytes(4),
)
```

`Array(U32, count=lambda ctx: ctx.count)` reads its element count from the `count` field read before it. `count` can also be an int, or `None` to read items until the transport ends.

`open_file` opens files in `r+b` mode by default. Pass `mode="rb"` when the file must stay read-only. It always reads an existing file, so write-only modes such as `"wb"` are rejected before they can truncate anything. To create a new file, use `schema_save_to_file`.

Omit the schema to let a format plugin describe the file: `open_file("sound.wav")`.

## Read, edit, save

```python
doc = open_file("sample.bin", schema=Schema)

print(doc.header.version)
print(doc.items)
print(doc.to_dict())

doc.header.version = 4
doc.items[0] = 7
doc.save("sample_modified.bin")
```

See [examples/basic-file.py](./examples/basic-file.py) for a runnable version.

Assigning to a struct field writes the new value through to the open transport immediately. `save` writes the complete struct from its current values to the given path and overwrites that file.

`doc.items[0] = 7` changes the list returned by the array lens. The list is serialized on `save`, but mutating it does not update the transport at that moment. Nested structs return their own `AttrsView`, so `doc.header.version` reads and writes the same way as a top-level field.

## Create a file

`schema_save_to_file` is the explicit-save counterpart to `open_file`. It creates or overwrites the destination:

```python
from beenary import schema_save_to_file

schema_save_to_file(Schema, {
    "header": {"magic": b"BEEN", "version": 1, "flags": 0},
    "count": 2,
    "items": [1, 2],
    "trailer": b"\0\0\0\0",
}, "sample.bin")
```

`schema_write` does the same into any transport at an optional offset and returns the offset after the struct.

A struct can carry a default value. The factory runs on every `new()` call, so each view starts fresh:

```python
from beenary import schema_save_to_file, with_default

V1 = with_default(Schema, lambda: {
    "header": {"magic": b"BEEN", "version": 1, "flags": 0},
    "count": 2,
    "items": [1, 2],
    "trailer": b"\0\0\0\0",
})

doc = V1.new()
schema_save_to_file(V1, None, "sample.bin")
```

The default lives outside the `Struct(...)` constructor. A schema can still have a field named `default`.

## Diff and patch

```python
from beenary import apply_patch, diff, preview

a = open_file("v1.bin", schema=Schema)
b = open_file("v2.bin", schema=Schema)

patch = diff(a, b)
print(preview(patch))
# header.version: 1 -> 2
# items[1]: 2 -> 9

patch.save("update.json")
apply_patch(a, patch)
a.save("v2_from_patch.bin")
```

`diff` walks fields in schema order and list elements in index order, so identical inputs produce identical patches. A patch stores `old` and `new` values and refuses to apply when the document no longer matches `old`. `Patch.save` writes versioned JSON; `Patch.load` reads it back. Values containing bytes are base64-encoded.

See [examples/basic-diff.py](./examples/basic-diff.py); run it with `python -m examples.basic-diff`.


## Format plugins

`sniff` scores every registered plugin against a path or transport. Scores run from `0.0` to `1.0`, highest first. `generic` is the fallback at `0.01`.

```python
from beenary import open_file, sniff

print(sniff("sound.wav"))
# [('wav', 0.99), ('generic', 0.01)]

doc = open_file("sound.wav")       # no schema: use the detected plugin
print(doc.chunks[0].id)            # b'fmt '
print(doc.chunks[-1].data[:4])
```

Built-in plugins are `generic`, `png`, `toy`, and `wav`. The WAV plugin keeps every RIFF chunk in file order, including unknown chunks and odd-size padding. `WavPlugin.audio_info(doc)` decodes the `fmt ` chunk into channels, sample rate, bit depth, and the extensible-header fields. See [examples/wav.py](./examples/wav.py) for a runnable version using `examples/a.wav`; run it with `python -m examples.wav`.

A plugin is one class. Put it in one module, then register it with an entry point:

```python
# mypkg/toy.py
from beenary.formats import FormatPlugin
from beenary.lens import Magic, Struct, U16, U32

class ToyPlugin(FormatPlugin):
    name = "toy"
    schema_id = "mypkg.toy"

    def sniff(self, transport):
        if transport.size() < 4:
            return 0.0
        return 1.0 if transport.read(0, 4) == b"TOY1" else 0.0

    def schema(self):
        return Struct(magic=Magic(b"TOY1"), version=U16, payload=U32)
```

```toml
[project.entry-points."beenary.formats"]
toy = "mypkg.toy:ToyPlugin"
```

For a local plugin, skip the entry point and call `register_plugin(ToyPlugin())`. Entry points load lazily on the first `sniff`, `detect`, `plugins`, or `get_plugin` call. A plugin that raises during sniffing is logged and skipped, so one broken package cannot break detection for the rest.


## Lenses

| Lens | Python value | Notes |
|---|---|---|
| `U8`, `U16`, `U32`, `U64` | `int` | Unsigned integers. Little-endian by default. |
| `I8`, `I16`, `I32`, `I64` | `int` | Signed integers. Little-endian by default. |
| `F32`, `F64` | `float` | IEEE 754 floats. Little-endian by default. |
| `Magic(b"BEEN")` | `bytes` | Reads fail if the bytes do not match. Writes the sentinel. |
| `Bytes(4)` | `bytes` | Fixed-length byte string. Writes require the same length. |
| `Padding(4, fill_byte=0)` | `bytes` | Fixed-length padding. Writes fill bytes, not the value read. |
| `Array(U32, count=4)` | `list` | Fixed or computed length. `count=None` reads to the end. |
| `Struct(field=U8, ...)` | `AttrsView` | Named fields in declaration order. |

Numeric lenses accept an endian argument: `U16(">")` for big-endian, `U16("<")` for little-endian, or `U16("!")` for network byte order.

## Transports

A transport is a random-access byte buffer. Lenses only call `size`, `read`, and `write`, so the same schema works with each implementation.

| Transport | Backing store |
|---|---|
| `MemoryTransport(data=b"")` | `bytearray` |
| `FileTransport.open(path, mode="rb")` | File handle |
| `MmapTransport(path)` | Read-only memory map |
| `MockTransport(initial=b"")` | Inbound and outbound test buffers |

`MockTransport` also implements `StreamSource` and `StreamSink`. Use `preload_incoming(data)` to queue inbound bytes and `outgoing()` to inspect bytes written outbound. `BytesIOSource` and `BytesIOSink` adapt `io.BytesIO` to the same stream interfaces.

`open_file` uses `FileTransport`. For memory-mapped access, construct `MmapTransport` and call `Schema.read(transport, 0)` directly.

## Errors

All library errors derive from `BeenaryError`. `LensError` carries a field path when one is known, so a failed read can report `header.version` or `items[3]`. Reads past the end of a transport raise `LensError` instead of returning short data.

## Development

```bash
python -m pytest
mypy --strict beenary/
ruff check beenary tests
```

CI runs the test suite on Python 3.11, 3.12, and 3.13, then runs `mypy` and `ruff`.

## Planned

These features are designed but not implemented in 0.1.0:

- Protocol dialogues over streams and sockets
- CLI commands for inspect, diff, and patch
- NumPy views for large numeric arrays
- A Textual-based inspector with live editing

See [FUTURE.md](./FUTURE.md) for the intended APIs, code sketches, and implementation order.


## License

MIT © aldevwork