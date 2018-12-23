from aiobufpro.parsers.http import HTTPParser
from aiobufpro.utils import get_websocket_accept_key


UPGRADE_REQUEST_HEADERS = bytearray(
    b"".join(
        [
            b"GET /ws HTTP/1.1\r\nConnection: keep-alive, upgrade\r\n",
            b"Upgrade: websocket\r\nSec-WebSocket-Key: Y56tJpDd+hCW+vDb0qdekQ==\r\n",
            b"Sec-WebSocket-Version: 13\r\n\r\n",
        ]
    )
)

INVALID_UPGRADE_REQUEST_HEADERS = bytearray(
    b"GET / HTTP/1.1\r\nConnection: keep-alive\r\nUpgrade: websocket\r\n\r\n"
)


def test_parse_upgrade_header():
    """Ensure valid upgrade headers are stored on the parser instance."""
    parser = HTTPParser()
    parser.parse_headers(UPGRADE_REQUEST_HEADERS)
    assert parser.should_upgrade
    assert parser.upgrade_header == (b"Upgrade", b"websocket")


def test_parse_invalid_upgrade_header():
    """Ensure upgrade headers included in non-upgrade requests are not stored."""
    parser = HTTPParser()
    parser.parse_headers(INVALID_UPGRADE_REQUEST_HEADERS)
    assert not parser.should_upgrade
    assert parser.upgrade_header is None


def test_get_websocket_accept_key():
    """Ensure the generated websocket accept key is the correct value."""
    parser = HTTPParser()
    parser.parse_headers(UPGRADE_REQUEST_HEADERS)
    for header, header_value in parser.headers:
        if header == b"Sec-WebSocket-Key":
            accept_key = get_websocket_accept_key(header_value)
    assert accept_key == b"J9R6HjgRj5VpgXEFRYnNh9igw2o="
