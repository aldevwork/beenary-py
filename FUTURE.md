# Future plans

This file records features that are designed but not implemented in 0.1.0. Nothing here can be imported or run today. The code sketches show the intended API so the design does not get lost before implementation.

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
2. Add protocol dialogues and a socket transport.
3. Add the CLI.
4. Add NumPy views for large numeric arrays.
5. Add a Textual-based inspector with live editing.

## Non-goals

- No schema language, YAML format, or external grammar.
- No generated parser code.
- No hidden writes to disk. Saving to a chosen path happens only through `save`, and field assignment writes only through the open transport.
- No eager import of plugin modules when `beenary` is imported.
