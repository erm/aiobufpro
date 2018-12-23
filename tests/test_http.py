from starlette.responses import HTMLResponse
from starlette.testclient import TestClient

from aiobufpro.parsers.http import HTTPParser, HTTPParserState


REQUEST_HEADERS = bytearray(
    b"GET / HTTP/1.1\r\nHost: localhost:8000\r\nConnection: keep-alive\r\n\r\n"
)

UPGRADE_REQUEST_HEADERS = bytearray(
    b"".join(
        [
            b"GET /ws HTTP/1.1\r\nConnection: keep-alive, upgrade\r\n",
            b"Upgrade: websocket\r\nSec-WebSocket-Key: Y56tJpDd+hCW+vDb0qdekQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n\r\n",
        ]
    )
)

INVALID_UPGRADE_REQUEST_HEADERS = bytearray(
    b"GET / HTTP/1.1\r\nConnection: keep-alive\r\nUpgrade: websocket\r\n\r\n"
)


def test_parse_headers():
    """Ensure the parser completely parses the request version line and headers."""
    parser = HTTPParser()
    parser.parse_headers(REQUEST_HEADERS)
    assert parser.http_version == "HTTP/1.1"
    assert parser.http_method == "GET"
    assert parser.headers == [
        (b"Host", b"localhost:8000"),
        (b"Connection", b"keep-alive"),
    ]
    assert parser.is_complete


def test_parse_headers_chunked():
    """
    Ensure the parser completely parses the request version line and headers when
    the headers bytes are being streamed.
    """
    parser = HTTPParser()

    for i in range(len(REQUEST_HEADERS)):
        b = REQUEST_HEADERS[i : i + 1]
        assert parser.state is not HTTPParserState.PARSING_COMPLETE
        parser.parse_headers(b)

    assert parser.state is HTTPParserState.PARSING_COMPLETE
    assert parser.http_version == "HTTP/1.1"
    assert parser.http_method == "GET"
    assert parser.headers == [
        (b"Host", b"localhost:8000"),
        (b"Connection", b"keep-alive"),
    ]


def test_http_response():
    class App:
        def __init__(self, scope):
            self.scope = scope

        async def __call__(self, receive, send):
            response = HTMLResponse("<html><body>Hello, world!</body></html>")
            await response(receive, send)

    client = TestClient(App)
    response = client.get("/")
    assert response.status_code == 200
