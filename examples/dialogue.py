"""Send a request and read the response over an in-memory stream.

Run with ``python -m examples.dialogue``.
"""

from __future__ import annotations

from beenary import Dialogue
from beenary.lens import U16, U32, Bytes, Struct
from beenary.transport import MockTransport

# Big-endian, so the bytes below read the same way a hex dump does.
Request = Struct(op=U16(">"), payload=Bytes(8))
Response = Struct(status=U16(">"), value=U32(">"))


def main() -> None:
    mock = MockTransport()
    mock.preload_incoming(b"\x00\xc8\x00\x00\x00\x2a")  # status=200, value=42

    dialogue = Dialogue(mock)
    dialogue.send(Request, op=0x0001, payload=b"\x00" * 8)
    print("sent    ", mock.outgoing().hex(" "))

    response = dialogue.receive(Response)
    print(f"received status={response.status} value={response.value}")


if __name__ == "__main__":
    main()
