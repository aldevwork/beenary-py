# AGENTS.md — beenary.py

Instructions for AI coding agents working on this repository.

## Mission

`beenary.py` is a Python library for **reading, writing, and structurally diffing
binary files** using composable bidirectional "lenses", auto-detected format
plugins, and a stream-based dialogue simulator for protocol testing.

**Not** a DSL. **Not** a schema language. **Not** a parser generator.
Schemas are ordinary Python objects; format plugins are ordinary Python classes.

## Code looks

The code's ABI for users of the library shall be easily-usable, scalable and with use of
all heavily cited known principles of writing good and scaleable Python code.
All the code shall be reviewed by a human being, so make it readable and easy to understand.
Do not waste tokens and after every prompt of a human always show the diffs and write a small
review about what you have changed

## Non-negotiable invariants

1. **Bidirectional lenses.** Every `Lens` must read *and* write. A round-trip
   test (`write(read(x)) == x`) must exist for every primitive and composite.
2. **No new grammar.** Do not invent a schema syntax, YAML format, or
   instruction set. If you feel the urge, stop and ask the human.
3. **No magic I/O.** `Lens.read/write` operate on a `Transport`. They never
   open files, call `os.path`, or import `socket`. All I/O lives in
   `beenary/transport.py`.
4. **Safe by default.** All offset math must be bounds-checked against
   `transport.size()`. Out-of-bounds → `LensError` with the failing path.
5. **Deterministic diff.** `diff(a, b)` must produce identical output for
   identical inputs. No hash-based iteration, no `set` ordering.
6. **Plugin isolation.** A broken plugin must not crash `sniff()` — catch,
   log, skip.

## Repository layout

```
beenary/
  __init__.py          # public API (open, open_bytes, sniff, diff, apply_patch)
  transport.py         # Transport, StreamSource, StreamSink + Memory/File/Mmap/Mock
  lens.py              # Lens ABC, primitives, Struct/Array/Optional/Switch, AttrsView
  formats.py           # FormatPlugin, registry, sniff, entry-point discovery
  diff.py              # FieldChange, Patch, diff, apply, preview
  dialogue.py          # StreamReader, StreamWriter, Dialogue
  cli.py               # CLI entry point (typer)
tests/
  test_lens_roundtrip.py
  test_struct_view.py
  test_diff.py
  test_patch_apply.py
  test_transport.py
  test_dialogue.py
  test_plugins.py
examples/
  01_read_write_png.py
  02_diff_patch.py
  03_plugin.py
  04_dialogue.py
docs/
  demo.gif             # TUI/CLI demo for README
```

## Python conventions

- Target **Python 3.11+**.
- Full type hints. `from __future__ import annotations` at top of every file.
- No third-party deps in the **core**. Optional extras in `pyproject.toml`:
  - `[project.optional-dependencies] cli = ["typer", "rich"]`
  - `dev = ["pytest", "pytest-cov", "mypy", "ruff"]`
- Public API in `beenary/__init__.py` only; everything else is internal by
  convention (leading underscore or documented as "not stable").
- Docstrings: Google style.
- Errors: subclass `BeenaryError` → `LensError`, `FormatError`, `PatchError`.

## Testing rules

- Every public function gets at least one test.
- Every primitive and composite lens gets a round-trip test.
- Diff/patch: property test that `apply(diff(a, b), a) == b` for random schemas.
- Dialogue: golden-file test with fixed byte sequences in `tests/fixtures/`.
- CI runs `pytest`, `mypy --strict beenary/`, `ruff check`.

## Feature workflow

When asked to add a feature:

1. Add or update the **protocol/ABC** in the relevant module first.
2. Add **round-trip or property tests** before implementation.
3. Implement the smallest thing that passes.
4. Update `README.md` "Features" only if the feature is user-visible.
5. Update this file only if invariants or layout change.

## Things to avoid

- New external dependencies in core.
- Silent coercion of `bytes` ↔ `str` without explicit encoding.
- Auto-writing to disk. Saving is always explicit (`doc.save(path)`).
- Changing the JSON patch format without bumping `beenary_patch_version`.
- Monkey-patching or importing plugin modules eagerly at import time.

## Definition of done (v0.1)

- [x] `Transport` + `MemoryTransport`, `FileTransport`, `MmapTransport`, `MockTransport`
- [x] Primitives: `U8..U64`, `I8..I64`, `F32/F64`, `Bytes`, `CString`, `Magic`, `Padding`
- [ ] Composites: `Struct`, `Array`, `Optional`, `Switch`
- [ ] `AttrsView` with attribute + item access, write-through
- [x] `diff` / `Patch` / `apply` / `preview` with JSON serialization
- [x] `FormatPlugin` ABC + registry + `sniff()` + built-in `generic`, `png`, `toy`, `wav` plugins
- [x] `Dialogue` + `MockTransport` with preload/inspect
- [ ] CLI: `beenary sniff|inspect|diff|patch`
- [ ] Full test suite green, mypy --strict clean
- [ ] README with demo GIF
- [ ] GitHub Actions CI

## When in doubt

Ask. Especially if a task seems to require a new DSL, a new file format, or a
new grammar — that's a signal the task is out of scope for this library
