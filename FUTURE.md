# Future plans

This file records features that are designed but not implemented in 0.1.0. Nothing here can be imported or run today. The code sketches show the intended API so the design does not get lost before implementation.

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
2. Add the CLI.
3. Add NumPy views for large numeric arrays.
4. Add a Textual-based inspector with live editing.

## Non-goals

- No schema language, YAML format, or external grammar.
- No generated parser code.
- No hidden writes to disk. Saving to a chosen path happens only through `save`, and field assignment writes only through the open transport.
- No eager import of plugin modules when `beenary` is imported.
