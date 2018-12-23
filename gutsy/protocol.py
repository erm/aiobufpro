import enum
import asyncio
import logging
from typing import List, Tuple, Union

from starlette.types import ASGIApp, ASGIInstance, Scope

from aiobufpro.connections import ASGIHTTPConnection, ASGIWebSocketConnection
from aiobufpro.utils import get_server_headers, get_websocket_accept_key
from aiobufpro.parsers.http import HTTPParser
from aiobufpro.parsers.websocket import WebSocketParser

logger = logging.getLogger()


class HTTPWSProtocolState(enum.Enum):
    """
    Current state of HTTP request/response cycle.
    """

    REQUEST = enum.auto()
    RESPONSE = enum.auto()
    STREAMING = enum.auto()
    FRAMING = enum.auto()
    CLOSED = enum.auto()


class HTTPWSProtocol(asyncio.BufferedProtocol):
    """
    HTTP and WebSocket protocol class with manual control of the receive buffer.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app: ASGIApp = app
        self.asgi_connection: Union[ASGIWebSocketConnection, ASGIHTTPConnection] = None
        self.asgi_instance: ASGIInstance = None
        self.state: HTTPWSProtocolState = HTTPWSProtocolState.REQUEST
        self.parser: Union[WebSocketParser, HTTPParser] = HTTPParser()
        self.handshake_headers: List[Tuple[bytes, bytes]] = None
        self.subprotocols: List[bytes] = None
        self.http_version: str = "1.1"
        self.scheme: str = "http"
        self.server: str = None
        self.client: str = None
        self.buffer_data: bytearray = bytearray(100)
        self.low_water_limit: int = 16384
        self.high_water_limit: int = 65536
        self.write_paused: bool = False
        self.drain_waiter: asyncio.Event = None
        self.app_queue: asyncio.Queue = None
        self.scope: Scope = None
        self.content: List[Tuple] = None
        self.keep_alive: bool = True
        self.accepted: bool = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport
        self.client = self.transport.get_extra_info("peername")
        self.server = self.transport.get_extra_info("sockname")
        self.drain_waiter = asyncio.Event()
        self.drain_waiter.set()

    def eof_received(self) -> None:
        pass

    def get_buffer(self, sizehint: int) -> bytearray:
        """
        Called to allocate a new receive buffer.
        """
        if len(self.buffer_data) < sizehint:
            self.buffer_data.extend(0 for _ in range(sizehint - len(self.buffer_data)))
        return self.buffer_data

    def buffer_updated(self, nbytes: int, is_writable: bool = False) -> None:
        """
        Called when the buffer was updated with the received data.
        """
        if self.state is HTTPWSProtocolState.REQUEST:
            self.on_header(self.buffer_data[:nbytes])
        elif is_writable:
            self.transport.write(self.buffer_data[:nbytes])
        else:
            self.on_frame(self.buffer_data[:nbytes])

    def on_header(self, data: bytes) -> None:
        """
        Called when request data is received and the parser has not completed.
        """

        # The request headers  are currently being parsed, so the incoming request data
        # will be fed to the parser instance until it is complete.
        self.parser.parse_headers(data)

        if self.parser.is_complete:

            # Once the parser has completed reading the headers, update the state and
            # finalise the headers.
            self.state = HTTPWSProtocolState.RESPONSE
            self.on_headers_complete()

    def on_headers_complete(self) -> None:
        """
        Called when the request headers have been completely parsed to build the ASGI
        connection scope and handling any upgrade requests.
        """
        headers = [
            (header.lower(), header_value)
            for header, header_value in self.parser.headers
        ]

        # Build the ASGI connection scope using the protocol and parser details.
        self.scope = {
            "type": "http",
            "http_version": self.parser.http_version,
            "server": self.server,
            "client": self.client,
            "scheme": self.scheme,
            "method": self.parser.http_method,
            "path": self.parser.path,
            "query_string": self.parser.query_string,
            "headers": headers,
        }

        if self.parser.upgrade_header is not None:

            # An unsupported upgrade header was received, return a 500 response.
            if self.parser.upgrade_header[1] != b"websocket":
                logger.debug(
                    f"Unsupported upgrade header: {self.parser.upgrade_header}"
                )
                content = b"".join(get_server_headers(500))
                self.transport.write(
                    b"".join([content, b"Unsupported upgrade request.\r\n"])
                )
                self.transport.close()
                return

            self.on_upgrade()

        else:
            # This is an HTTP request, create an HTTP connection.
            asgi_connection = ASGIHTTPConnection(protocol=self)
            asgi_connection.run_asgi(app=self.app, scope=self.scope)
            self.asgi_connection = asgi_connection

    def on_upgrade(self) -> None:

        # Retrieve the header key and generate the accept key.
        accept_key = None
        subprotocols = None
        for header, header_value in self.parser.headers:
            if header == b"Sec-WebSocket-Key":
                accept_key = get_websocket_accept_key(header_value)
            if header == b"Sec-WebSocket-Protocol":
                subprotocols = header_value

        # The websocket key is missing, return a 403 response.
        if accept_key is None:
            content = b"".join(get_server_headers(403))
            self.transport.write(b"".join([content, b"\r\n"]))
            self.transport.close()
            return

        accept_header = b"".join([b"Sec-WebSocket-Accept: ", accept_key, b"\r\n"])
        self.handshake_headers = b"".join(
            [b"Upgrade: WebSocket\r\nConnection: Upgrade\r\n", accept_header]
        )

        if subprotocols:
            subprotocols = subprotocols.split(b",")

        self.scheme = "wss" if self.scope["scheme"] == "http" else "ws"

        self.scope.update(
            {"type": "websocket", "subprotocols": subprotocols, "scheme": self.scheme}
        )

        asgi_connection = ASGIWebSocketConnection(protocol=self)
        asgi_connection.run_asgi(app=self.app, scope=self.scope)
        self.asgi_connection = asgi_connection

        self.parser = WebSocketParser(protocol=self)
        self.state = HTTPWSProtocolState.FRAMING

    def on_frame(self, data: bytes) -> None:
        assert (
            self.state is HTTPWSProtocolState.FRAMING
        ), "Invalid protocol state for framing."
        asyncio.create_task(self.parser.parse_frame(data))

    async def feed_data(
        self, content: Union[bytes, str], is_writable: bool = True
    ) -> None:
        if isinstance(content, str):
            content = content.encode()
        buf_size = len(content)
        buf = self.get_buffer(buf_size)
        buf[:buf_size] = content
        self.buffer_updated(buf_size, is_writable=is_writable)

    async def drain(self) -> None:
        """
        Await the event object to allow the transport's write buffer a chance
        to be flushed.
        """
        await self.drain_waiter.wait()

    def pause_writing(self) -> None:
        """
        Called when the transport’s buffer goes over the high watermark.
        """
        assert not self.write_paused, "Invalid"
        self.write_paused = True
        self.drain_waiter.clear()

    def resume_writing(self) -> None:
        """
        Called when the transport’s buffer drains below the low watermark.
        """
        assert self.write_paused, "Invalid write state"
        self.write_paused = False
        self.drain_waiter.set()

    def on_response_complete(self) -> None:
        """
        Called when the ASGI connection and response has completed.
        """
        if not self.keep_alive:
            self.transport.close()
        else:
            self.state = HTTPWSProtocolState.REQUEST
            self.parser = HTTPParser()

    def accept(self) -> None:
        """
        Called when accepting the websocket connection.
        """
        self.accepted = True
        server_headers = b"".join(get_server_headers(101))
        content = b"".join([server_headers, self.handshake_headers, b"\r\n"])
        self.transport.write(content)

    def reject(self) -> None:
        """
        Called when rejecting the websocket connection.
        """
        server_headers = b"".join(get_server_headers(403))
        content = b"".join([server_headers, b"\r\n"])
        self.transport.write(content)
        self.transport.close()

    def close(self) -> None:
        self.transport.close()
