# beenary.py

> **Bidirectional lenses, structural diffs, and protocol dialogues for binary files — without a DSL.**

[![CI](https://github.com/USER/beenary/actions/workflows/ci.yml/badge.svg)](https://github.com/USER/beenary/actions)
[![PyPI](https://img.shields.io/pypi/v/beenary.svg)](https://pypi.org/project/beenary/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

![beenary demo](docs/demo.gif)

`beenary.py` lets you treat binary files as **live, mutable Python objects**.
Read a field, change it, save — the bytes update. Diff two files structurally,
not byte-by-byte. Simulate protocol dialogues without opening a socket.
Auto-detect formats with pluggable adapters. All with plain Python objects and
**zero new grammar to learn**.

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

## Install

```bash
pip install beenary            # core, no dependencies
pip install beenary[cli]       # + typer/rich for the CLI
```

## Quick start

```python
from beenary import open_file
from beenary.lens import Struct, U8, U16, U32, Magic, Bytes, Array

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

doc = open_file("sample.bin", schema=Schema)
print(doc.header.version)     # 2
print(doc.items[3])           # 12345

doc.header.version = 3        # write-through
doc.items[3] = 99999
doc.save("sample_modified.bin")
```

### Structural diff & patch

```python
from beenary import open_file, diff, apply_patch

a = open_file("v1.bin", schema=Schema)
b = open_file("v2.bin", schema=Schema)

patch = diff(a, b)
print(patch)
# Patch(schema_id='custom', changes=[
#   FieldChange(action='set',  path=('header','version'), old=1, new=2),
#   FieldChange(action='set',  path=('items', 3),         old=12345, new=99999),
# ])

patch.save("update.json")     # JSON, human-readable, versioned
apply_patch(a, patch)         # validated against schema_id + format_version
a.save("v2_from_patch.bin")
```

### Auto-detected formats

```python
import beenary

doc = beenary.open("image.png")          # plugin sniffed via magic bytes
print(doc.format.name)                   # 'png'
print(doc.view.ihdr.width)               # 800

beenary.sniff("image.png")
# [('png', 0.99), ('generic', 0.01)]
```

Register your own format as a plugin:

```python
# my_pkg/formats/toy.py
from beenary.formats import FormatPlugin
from beenary.lens import Struct, Magic, U16, U32

class ToyPlugin(FormatPlugin):
    name = "toy"
    schema_id = "mypkg.toy"

    def sniff(self, t):
        return 1.0 if t.read(0, 4) == b"TOY1" else 0.0

    def schema(self):
        return Struct(magic=Magic(b"TOY1"), version=U16, payload=U32)
```

```toml
# pyproject.toml
[project.entry-points."beenary.formats"]
toy = "my_pkg.formats.toy:ToyPlugin"
```

### Protocol dialogues (no sockets required)

```python
from beenary import Dialogue
from beenary.transport import MockTransport
from beenary.lens import Struct, U16, U32, Bytes

Request  = Struct(op=U16, payload=Bytes(8))
Response = Struct(status=U16, value=U32)

mock = MockTransport()
mock.preload_incoming(b"\x00\xc8\x00\x00\x00\x2a")   # pre-recorded response

d = Dialogue(mock)
d.send(Request, op=0x0001, payload=b"\x00" * 8)
resp = d.receive(Response)

assert resp.status == 200
assert resp.value == 42
assert mock.outgoing() == b"\x00\x01" + b"\x00" * 8
```

The same `Dialogue` works over real sockets, pipes, or `io.BytesIO` — those are
just other `StreamSource` / `StreamSink` implementations. Your protocol logic
stays the same.

## CLI

```bash
$ beenary sniff image.png
png      0.99
generic  0.01

$ beenary inspect image.png --format png
ihdr.width           800
ihdr.height          600
ihdr.bit_depth       8
...

$ beenary diff v1.bin v2.bin --format toy
header.version   1 -> 2
items[3]         12345 -> 99999

$ beenary patch v1.bin update.json -o v2.bin
applied 2 changes to v1.bin -> v2.bin
```

## Design principles

1. **Bidirectional by design.** One `Lens`, both directions. Round-trip is tested.
2. **No DSL.** Schemas are Python objects. Lambdas resolve dynamic sizes.
3. **Transport-agnostic I/O.** Files, mmap, memory, mocks, and (soon) sockets share
   the same dialogue API.
4. **Structural over byte-level.** Diffs and patches speak in field paths, not offsets.
5. **Explicit saving.** No hidden writes to disk. `doc.save(path)` only.

## Roadmap

- [ ] v0.1 — lenses, diff/patch, plugins, dialogue, CLI
- [ ] v0.2 — `SocketTransport` + async dialogue
- [ ] v0.3 — `numpy` views for large numeric arrays
- [ ] v0.4 — TUI inspector (Textual) with live editing

## License

MIT © YOUR NAME