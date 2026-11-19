# beenary.py

beenary reads and writes binary files through lenses. A lens turns bytes at an offset into a Python value and writes that value back. Schemas are ordinary Python objects, so there is no schema language, parser generator, or generated code.

Version 0.1.0 includes lenses, transports, live struct views, and saving. Structural diff and patch, format plugins, protocol dialogues, and the CLI are planned and not in this release. Their intended APIs are in [FUTURE.md](./FUTURE.md).

## Design

- A lens reads and writes. Round-trip tests cover both directions.
- Schemas are plain Python objects. There is no schema language.
- All byte access goes through a transport. Lenses do not open files or sockets.
- Field assignment writes through to the open transport. Saving to a chosen path is always explicit.
- Structural diff and patch will use field paths, not byte offsets. This is planned, not implemented.

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

`open_file` opens files in `r+b` mode by default. Pass `mode="rb"` when the file must stay read-only.

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

- Structural diff and patch with a JSON patch format
- Format detection and plugins with magic-byte sniffing
- Protocol dialogues over streams and sockets
- CLI commands for inspect, diff, and patch
- NumPy views for large numeric arrays
- A Textual-based inspector with live editing

See [FUTURE.md](./FUTURE.md) for the intended APIs, code sketches, and implementation order.

## Creation

Made with using AI, but reread, picked by hand and engineered by [aldevwork](https://github.com/aldevwork). All code was read, handpicked and checked by a human. All the API, features and design principles are hand picked by a human.

## License

MIT © aldevwork