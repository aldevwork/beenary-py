"""Diff two versions of a small binary file and apply the patch."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from beenary import (
    apply_patch,
    diff,
    open_file,
    preview,
    schema_save_to_file,
    with_default,
)
from beenary.lens import U8, U16, U32, Array, Bytes, Magic, Struct

Schema = Struct(
    header=Struct(
        magic=Magic(b"BEEN"),
        version=U8,
        flags=U16,
    ),
    count=U32,
    items=Array(U32, count=lambda ctx: ctx.count),
    trailer=Bytes(4),
) # The structure of the binary file, called Struct (Nestable)


V1 = with_default(
    Schema,
    lambda: {
        "header": {"magic": b"BEEN", "version": 1, "flags": 0},
        "count": 2,
        "items": [1, 2],
        "trailer": b"\0\0\0\0",
    },
) # Making a structure with a default value with function with_default

V2 = with_default(
    Schema,
    lambda: {
        "header": {"magic": b"BEEN", "version": 2, "flags": 0},
        "count": 3,
        "items": [2, 9],
        "trailer": b"\0\0\0\0",
    },
)


def main() -> None:
    with TemporaryDirectory() as tmp:
        v1_path = Path(tmp) / "v1.bin"
        v2_path = Path(tmp) / "v2.bin"
        schema_save_to_file(V1, None, v1_path)
        # None writes down the default from a structure that has a default
        schema_save_to_file(V2, None, v2_path)

        with (
            open_file(v1_path, schema=Schema, mode="rb") as a,
            open_file(v2_path, schema=Schema, mode="rb") as b,
        ):
            patch = diff(a, b)
            # Get diff from the two files
            print(preview(patch))
            patch.save("update.json")

            # Apply to a writable copy so the source file stays untouched.
            target_path = Path(tmp) / "target.bin"
            target_path.write_bytes(v1_path.read_bytes())
            with open_file(target_path, schema=Schema) as target:
                apply_patch(target, patch)
                target.save("v2_from_patch.bin")


if __name__ == "__main__":
    main()
