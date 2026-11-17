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

doc = open_file("sample_modified.bin", schema=Schema)
print(doc.header.version)     # 2
print(doc.items[3])           # 12345

print(doc.items)

doc.header.version = 3        # write-through
doc.items[3] = 99999
doc.save("sample_modified.bin")
