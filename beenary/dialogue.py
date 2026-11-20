"""Protocol dialogues: exchange lens-defined messages over a stream.

A :class:`Dialogue` decodes inbound bytes with a lens and encodes outbound
values with the same lens, so one schema describes both directions. Streams are
:class:`~beenary.transport.StreamSource` and
:class:`~beenary.transport.StreamSink`, which makes sockets, pipes,
``io.BytesIO``, and :class:`~beenary.transport.MockTransport` interchangeable.

Receiving needs a message length up front, because a stream has no end to read
to. Fixed-size schemas get it from ``schema.size()``; for schemas whose size
depends on the data (e.g. ``Array(U8, count=lambda ctx: ctx.n)``) pass
``size=`` explicitly.
"""

from __future__ import annotations

from typing import Any

from .errors import LensError
from .transport import MemoryTransport, StreamSink, StreamSource


class Dialogue:
    """Send and receive messages over a pair of byte streams.

    Args:
        source: Stream to read inbound messages from.
        sink: Stream to write outbound messages to. Defaults to ``source`` when
            it is also a :class:`StreamSink` (as ``MockTransport`` and
            ``SocketStream`` are), which fits request/response protocols. A
            dialogue without a sink can still :meth:`receive`.
    """

    def __init__(self, source: StreamSource, sink: StreamSink | None = None) -> None:
        self._source = source
        self._sink = sink if sink is not None else (
            source if isinstance(source, StreamSink) else None
        )

    def send(self, schema: Any, value: Any = None, /, **fields: Any) -> None:
        """Encode one message and write it to the sink.

        Args:
            schema: A lens, usually a :class:`~beenary.lens.Struct`.
            value: Mapping (or ``AttrsView``) supplying the field values. It is
                positional-only, so a schema field may itself be named
                ``value``: ``send(Response, status=200, value=42)``.
            **fields: Field values as keywords, used when ``value`` is omitted.

        Raises:
            TypeError: If both ``value`` and keyword fields are given.
            LensError: If a value is missing or does not fit the schema.
        """
        if self._sink is None:
            raise TypeError("dialogue has no sink; pass sink= to send")
            # @result
        if value is not None and fields:
            raise TypeError("pass either a mapping or keyword fields, not both")
            # @result
        payload = fields if value is None else value
        buffer = MemoryTransport()
        end = schema.write(buffer, 0, payload)
        self._sink.write_stream(buffer.read(0, end))

    def receive(self, schema: Any, *, size: int | None = None) -> Any:
        """Read one message from the source and decode it with ``schema``.

        Args:
            schema: A lens, usually a :class:`~beenary.lens.Struct`.
            size: Message length in bytes. Defaults to ``schema.size()``.

        Returns:
            The decoded value — an :class:`~beenary.lens.AttrsView` for a
            ``Struct``.

        Raises:
            LensError: If the schema has no static size and ``size`` was not
                given, if the stream ends early, or if decoding fails.
        """
        length = schema.size() if size is None else size
        if length is None:
            raise LensError(
                f"cannot receive {schema!r}: its size depends on the data; "
                "pass size= with the expected message length"
            )
            # @result
        data = self._source.read_stream(length)
        value, _ = schema.read(MemoryTransport(data), 0)
        return value
