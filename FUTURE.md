# Future plans

This file records features that are designed but not implemented in 0.1.0. Nothing here can be imported or run today. The code sketches show the intended API so the design does not get lost before implementation.

## Structural diff and patch

The plan is to compare two documents by field path instead of byte offset.

```python
from beenary import open_file, diff, apply_patch

a = open_file("v1.bin", schema=Schema)
b = open_file("v2.bin", schema=Schema)

patch = diff(a, b)
print(patch)
# Patch(schema_id='custom', changes=[
#   FieldChange(action='set', path=('header', 'version'), old=1, new=2),
#   FieldChange(action='set', path=('items', 3), old=12345, new=99999),
# ])

patch.save("update.json")
apply_patch(a, patch)
a.save("v2_from_patch.bin")
```

Intended behavior:

- `diff(a, b)` returns a `Patch` containing `schema_id` and a list of `FieldChange` objects.
- Each change names an action, a field path, the old value, and the new value.
- `Patch.save(path)` writes JSON that a person can read and a program can reload.
- `apply_patch(doc, patch)` checks the schema id and format version before writing.
- `preview(patch)` reports what would change without applying it.

The current package has no `diff`, `Patch`, `apply_patch`, or `preview`.

## Format detection and plugins

The plan is to identify common formats from magic bytes and let packages register their own formats.

```python
import beenary

doc = beenary.open("image.png")
print(doc.format.name)       # 'png'
print(doc.view.ihdr.width)   # 800

beenary.sniff("image.png")
# [('png', 0.99), ('generic', 0.01)]
```

A plugin would provide a name, a schema id, a sniff score, and a schema:

```python
from beenary.formats import FormatPlugin
from beenary.lens import Magic, Struct, U16, U32

class ToyPlugin(FormatPlugin):
    name = "toy"
    schema_id = "mypkg.toy"

    def sniff(self, transport):
        return 1.0 if transport.read(0, 4) == b"TOY1" else 0.0

    def schema(self):
        return Struct(magic=Magic(b"TOY1"), version=U16, payload=U32)
```

Plugins would be discovered through the `beenary.formats` entry point group:

```toml
[project.entry-points."beenary.formats"]
toy = "mypkg.formats.toy:ToyPlugin"
```

A plugin that raises during sniffing should be logged and skipped, so one broken package cannot break detection for every other format.

The current package has no `beenary.formats` module and no `sniff` function.

## Protocol dialogues

The plan is to send and receive lens-defined messages over any stream, with the same code used for sockets, pipes, and `io.BytesIO`.

```python
from beenary import Dialogue
from beenary.lens import Bytes, Struct, U16, U32
from beenary.transport import MockTransport

Request = Struct(op=U16, payload=Bytes(8))
Response = Struct(status=U16, value=U32)

mock = MockTransport()
mock.preload_incoming(b"\x00\xc8\x00\x00\x00\x2a")

dialogue = Dialogue(mock)
dialogue.send(Request, op=0x0001, payload=b"\x00" * 8)
response = dialogue.receive(Response)

assert response.status == 200
assert response.value == 42
assert mock.outgoing() == b"\x00\x01" + b"\x00" * 8
```

`Dialogue` would read and write through `StreamSource` and `StreamSink`, so a socket adapter only has to implement those two interfaces.

The current package has `StreamSource`, `StreamSink`, `BytesIOSource`, `BytesIOSink`, and `MockTransport`, but no `Dialogue` class.

## Command-line interface

The plan is a small CLI for the operations the library already supports, plus the planned diff and patch commands.

```console
$ beenary sniff image.png
png      0.99
generic  0.01

$ beenary inspect image.png --format png
ihdr.width      800
ihdr.height     600
ihdr.bit_depth  8

$ beenary diff v1.bin v2.bin --format toy
header.version  1 -> 2
items[3]        12345 -> 99999

$ beenary patch v1.bin update.json -o v2.bin
applied 2 changes to v1.bin -> v2.bin
```

`pyproject.toml` already declares a `beenary` console script that points at `beenary.cli:app`, but `beenary/cli.py` does not exist yet, so the command will fail if called.

## Roadmap

1. Finish the core lens and transport API.
2. Add structural diff, patch, and preview.
3. Add format detection and the plugin registry.
4. Add protocol dialogues and a socket transport.
5. Add the CLI.
6. Add NumPy views for large numeric arrays.
7. Add a Textual-based inspector with live editing.

## Non-goals

- No schema language, YAML format, or external grammar.
- No generated parser code.
- No hidden writes to disk. Saving to a chosen path happens only through `save`, and field assignment writes only through the open transport.
- No eager import of plugin modules when `beenary` is imported.
