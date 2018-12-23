import asyncio
import enum
from dataclasses import dataclass, field

from starlette.types import ASGIApp, Scope, Message

from aiobufpro.utils import get_server_headers
from aiobufpro.parsers.websocket import WebSocketOpcode, WebSocketError


class ASGIConnectionState(enum.Enum):
    """Current state of the ASGI connection."""

    REQUEST = enum.auto()
    RESPONSE = enum.auto()
    STREAMING = enum.auto()
    CLOSED = enum.auto()


@dataclass
class ASGIConnection:
    """
    Base ASGI connection class. Defines the common connection behaviour of HTTP and
    WebSocket protocol interfaces.
    """

    protocol: asyncio.BaseTransport
    state: ASGIConnectionState = field(default=ASGIConnectionState.REQUEST, init=False)
    app_queue: asyncio.Queue = field(default=None, init=False)
    content_length: int = field(default=None, init=False)

    def run_asgi(self, app: ASGIApp, scope: Scope):
        """
        Instantiate the ASGI application.
        https://asgi.readthedocs.io/en/latest/specs/main.html#applications
        """
        asgi_instance = app(scope)
        self.app_queue = asyncio.Queue()
        asyncio.create_task(asgi_instance(self.receive, self.send))

    def put_message(self, message: Message) -> None:
        """Put a message in the queue to be received by the application."""
        if self.state is ASGIConnectionState.CLOSED:
            return

        self.app_queue.put_nowait(message)

    async def receive(self) -> Message:
        """Awaited by the application to handle server messages."""
        message = await self.app_queue.get()
        return message

    async def send(self, message: Message) -> None:
        """
        Receive and dispatch the incoming message event from the application.

        Each event is a dict with a top-level type key that contains a unicode string
        of the message type.

        For convenience, we parse the message type and dispatch the event to a
        specified handler method.
        """
        message_type = message["type"]
        handler_name = f"on_{message_type.replace('.', '_')}"
        handler = getattr(self, handler_name)

        await handler(message)

        if self.state is ASGIConnectionState.CLOSED:
            # The handler determined that the response is complete and the connection
            # should now be closed.
            self.on_connection_complete()

    def update_connection_state(self, new_state: ASGIConnectionState) -> None:
        """Update the connection state."""
        assert self.state is not new_state, "Invalid ASGI connection state transition"
        self.state = new_state

    def on_connection_complete(self):
        """Override in connection class. Called when the connection is complete."""
        raise NotImplementedError


class ASGIHTTPConnection(ASGIConnection):
    def run_asgi(self, app: ASGIApp, scope: Scope) -> None:
        super().run_asgi(app, scope)
        # Place an initial `http.request` message type in the application queue to
        # indicate an incoming request.
        self.put_message({"type": "http.request", "body": b""})

    async def on_http_response_start(self, message: Message) -> None:
        """
        Handler for the initial HTTP response event.
        https://asgi.readthedocs.io/en/latest/specs/www.html#response-start
        """
        if self.state is not ASGIConnectionState.REQUEST:
            raise Exception(
                "Invalid `http.response.start` event: The response has already started."
            )

        # Retrieve the HTTP status code and any headers sent from the application.
        status = message["status"]
        headers = message.get("headers", [])

        # Start building the response with the base server headers and include any
        # headers from the application in the response.
        self.content = get_server_headers(status)
        for header_name, header_value in headers:

            if header_name == b"content-length":
                # If we see the content-length header, then set the value on our
                # instance to be used when sending the response.
                self.content_length = int(header_value.decode())

            # Default to keep-alive unless the connection header has a close value.
            elif header_name == b"connection" and header_value == b"close":
                self.protocol.keep_alive = False

            self.content.append(b"".join([header_name, b": ", header_value, b"\r\n"]))

        self.update_connection_state(ASGIConnectionState.RESPONSE)

    async def on_http_response_body(self, message: Message) -> None:
        """
        Handler for the HTTP response body events.
        https://asgi.readthedocs.io/en/latest/specs/www.html#response-body
        """
        if self.state not in (
            ASGIConnectionState.RESPONSE,
            ASGIConnectionState.STREAMING,
        ):
            raise Exception(
                "Invalid `http.response.body` event: The response has not started."
            )

        body = message.get("body", b"")
        more_body = message.get("more_body", False)

        if self.state is ASGIConnectionState.RESPONSE:
            # If we aren't currently streaming, then we will either update the state
            # to begin streaming or complete the response.

            if not more_body:
                # There is no more body to be received from the application, so we
                # complete the response and close the connection.

                if self.content_length is None:
                    # If we didn't see a content-length in the headers during the start
                    # event, then we create it here based on the body size.
                    self.content.append(b"content-length: %x\r\n\r\n" % len(body))
                else:
                    self.content.append(b"\r\n")

                self.content.extend([body, b"\r\n"])
                await self.protocol.feed_data(b"".join(self.content))
                self.update_connection_state(ASGIConnectionState.CLOSED)

            else:

                # There is additional body to be received from the application, so
                # we need to use the chunked transfer encoding header to stream any
                # additional body data being sent from the application.
                self.content.extend(
                    [
                        b"transfer-encoding: chunked\r\n\r\n",
                        b"%x\r\n" % len(body),
                        body,
                        b"\r\n",
                    ]
                )
                await self.protocol.feed_data(b"".join(self.content))
                self.update_connection_state(ASGIConnectionState.STREAMING)

        elif self.state is ASGIConnectionState.STREAMING:
            await self.protocol.feed_data(
                b"".join([b"%x\r\n" % len(body), body, b"\r\n"])
            )

            if not more_body:
                await self.protocol.feed_data(b"0\r\n\r\n")
                self.update_connection_state(ASGIConnectionState.CLOSED)

        if self.protocol.write_paused:
            await self.protocol.drain()

    def on_connection_complete(self) -> None:
        self.put_message({"type": "http.disconnect"})
        self.protocol.on_response_complete()


class ASGIWebSocketConnection(ASGIConnection):
    def run_asgi(self, app: ASGIApp, scope: Scope) -> None:
        super().run_asgi(app, scope)
        # Place an initial `websocket.connect` message type in the application queue to
        # indicate an incoming request.
        self.put_message({"type": "websocket.connect", "order": 0})

    async def send(self, message: Message) -> None:
        message_type = message["type"]
        opcode = None

        payload_data = message.get("text")
        if payload_data:
            opcode = WebSocketOpcode.TEXT
        else:
            payload_data = message.get("bytes")
            if payload_data:
                opcode = WebSocketOpcode.BYTES

        if self.state is ASGIConnectionState.CLOSED:
            raise Exception(f"Unexpected message, ASGIConnection is {self.state}")

        if self.state == ASGIConnectionState.REQUEST:
            subprotocol = message.get("subprotocol", None)
            if subprotocol is not None:
                self.protocol.handshake_headers.append(
                    (b"Sec-WebSocket-Protocol", subprotocol.encode("utf-8"))
                )

            if not self.protocol.accepted:
                accept = message_type == "websocket.accept"
                close = message_type == "websocket.close"

                if accept or close:
                    self.protocol.accept()
                    self.update_connection_state(ASGIConnectionState.RESPONSE)
                else:
                    self.protocol.reject()
                    self.update_connection_state(ASGIConnectionState.CLOSED)

        if self.state is ASGIConnectionState.RESPONSE:

            if opcode is not None:

                try:

                    content = await self.protocol.parser.get_frame_content(
                        payload_data, opcode=opcode
                    )

                except WebSocketError as exc:
                    print(exc)
                    self.protocol.close()

                await self.protocol.feed_data(content)

            if message_type == "websocket.close":
                code = message.get("code", 1000)
                self.update_connection_state(ASGIConnectionState.CLOSED)
                # todo
