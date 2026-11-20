"""Tests for protocol dialogues and the socket stream adapter."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from beenary import Dialogue
from beenary.errors import LensError
from beenary.lens import U8, U16, U32, Array, Bytes, Struct
from beenary.transport import BytesIOSink, BytesIOSource, MockTransport, SocketStream

# The wire format from FUTURE.md is big-endian, so the schemas say so.
Request = Struct(op=U16(">"), payload=Bytes(8))
Response = Struct(status=U16(">"), value=U32(">"))

FIXTURES = Path(__file__).with_name("fixtures")


# --------------------------------------------------------------------------- #
# Dialogue
# --------------------------------------------------------------------------- #


class TestDialogue:
    def test_future_md_example(self) -> None:
        mock = MockTransport()
        mock.preload_incoming(b"\x00\xc8\x00\x00\x00\x2a")

        dialogue = Dialogue(mock)
        dialogue.send(Request, op=0x0001, payload=b"\x00" * 8)
        response = dialogue.receive(Response)

        assert response.status == 200
        assert response.value == 42
        assert mock.outgoing() == b"\x00\x01" + b"\x00" * 8

    def test_matches_recorded_bytes(self) -> None:
        mock = MockTransport()
        dialogue = Dialogue(mock)
        dialogue.send(Request, op=0x0001, payload=b"\x00" * 8)
        assert mock.outgoing() == (FIXTURES / "dialogue_request.bin").read_bytes()

        mock.preload_incoming((FIXTURES / "dialogue_response.bin").read_bytes())
        response = dialogue.receive(Response)
        assert (response.status, response.value) == (200, 42)

    def test_send_accepts_a_mapping(self) -> None:
        mock = MockTransport()
        Dialogue(mock).send(Response, {"status": 200, "value": 42})
        assert mock.outgoing() == b"\x00\xc8\x00\x00\x00\x2a"

    def test_send_rejects_mapping_and_fields_together(self) -> None:
        with pytest.raises(TypeError, match="either"):
            Dialogue(MockTransport()).send(Request, {"op": 1}, payload=b"")

    def test_send_reports_missing_field(self) -> None:
        with pytest.raises(LensError, match="missing field"):
            Dialogue(MockTransport()).send(Response, status=200)

    def test_separate_source_and_sink(self) -> None:
        sink = BytesIOSink()
        dialogue = Dialogue(BytesIOSource(b"\x00\xc8\x00\x00\x00\x2a"), sink)
        dialogue.send(Response, {"status": 1, "value": 2})
        assert sink.getvalue() == b"\x00\x01\x00\x00\x00\x02"
        assert dialogue.receive(Response).value == 42

    def test_send_without_a_sink_raises(self) -> None:
        with pytest.raises(TypeError, match="no sink"):
            Dialogue(BytesIOSource()).send(Response, status=200, value=42)

    def test_receive_needs_size_for_dynamic_schema(self) -> None:
        dynamic = Struct(n=U8, items=Array(U8, count=lambda ctx: ctx.n))
        with pytest.raises(LensError, match="size="):
            Dialogue(BytesIOSource(b"")).receive(dynamic)

    def test_receive_uses_explicit_size_for_dynamic_schema(self) -> None:
        dynamic = Struct(n=U8, items=Array(U8, count=lambda ctx: ctx.n))
        value = Dialogue(BytesIOSource(b"\x03\x0a\x0b\x0c")).receive(dynamic, size=4)
        assert value.items == [10, 11, 12]

    def test_receive_raises_when_stream_ends_early(self) -> None:
        with pytest.raises(LensError, match="stream exhausted"):
            Dialogue(BytesIOSource(b"\x00")).receive(Response)

    def test_receive_reports_lens_error_from_bad_magic(self) -> None:
        framed = Struct(magic=Bytes(2), value=U8)
        with pytest.raises(LensError):
            Dialogue(BytesIOSource(b"\x00\x01\x02")).receive(framed, size=1)


# --------------------------------------------------------------------------- #
# SocketStream
# --------------------------------------------------------------------------- #


class _TrickleSocket:
    """Fake socket that returns at most one byte per ``recv`` call."""

    def __init__(self, incoming: bytes) -> None:
        self._incoming = incoming
        self.sent = bytearray()
        self.closed = False

    def recv(self, bufsize: int) -> bytes:
        if not self._incoming:
            return b""
        chunk, self._incoming = self._incoming[:1], self._incoming[1:]
        return chunk

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def close(self) -> None:
        self.closed = True


class TestSocketStream:
    def test_read_stream_loops_over_partial_recvs(self) -> None:
        stream = SocketStream(_TrickleSocket(b"abcd"))
        assert stream.read_stream(4) == b"abcd"

    def test_write_stream_sends_everything(self) -> None:
        sock = _TrickleSocket(b"")
        SocketStream(sock).write_stream(b"hello")
        assert bytes(sock.sent) == b"hello"

    def test_peer_close_raises_lens_error(self) -> None:
        with pytest.raises(LensError, match="socket closed: wanted 4 bytes, got 2"):
            SocketStream(_TrickleSocket(b"ab")).read_stream(4)

    def test_zero_length_read_does_not_touch_socket(self) -> None:
        sock = _TrickleSocket(b"")
        assert SocketStream(sock).read_stream(0) == b""

    def test_negative_length_raises(self) -> None:
        with pytest.raises(LensError, match="negative read length"):
            SocketStream(_TrickleSocket(b"")).read_stream(-1)

    def test_close_closes_the_socket(self) -> None:
        sock = _TrickleSocket(b"")
        SocketStream(sock).close()
        assert sock.closed

    def test_context_manager_closes_the_socket(self) -> None:
        sock = _TrickleSocket(b"")
        with SocketStream(sock) as stream:
            stream.write_stream(b"x")
        assert sock.closed

    def test_dialogue_over_a_real_socket_pair(self) -> None:
        client, server = socket.socketpair()
        try:
            client_stream = SocketStream(client)
            server_stream = SocketStream(server)
            client_dialogue = Dialogue(client_stream)
            server_dialogue = Dialogue(server_stream)

            client_dialogue.send(Request, op=0x0001, payload=b"\x00" * 8)
            request = server_dialogue.receive(Request)
            assert request.op == 1

            server_dialogue.send(Response, status=200, value=42)
            response = client_dialogue.receive(Response)
            assert (response.status, response.value) == (200, 42)
        finally:
            client.close()
            server.close()
