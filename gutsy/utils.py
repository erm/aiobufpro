import time
import http
import base64
import hashlib
from typing import List, Tuple
from email.utils import formatdate


HTTP_SERVER_NAME = b"server: aiobufpro\r\n"

WEBSOCKET_ACCEPT_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def get_server_headers(status: int) -> List[Tuple]:
    """Build the base server header list for HTTP responses."""
    try:
        phrase = http.HTTPStatus(status).phrase.encode()
    except ValueError:
        phrase = b""
    return [
        b"".join([b"HTTP/1.1 ", str(status).encode(), b" ", phrase, b"\r\n"]),
        HTTP_SERVER_NAME,
        b"".join([b"date: ", formatdate(time.time(), usegmt=True).encode(), b"\r\n"]),
    ]


def get_websocket_accept_key(sec_websocket_key: bytes) -> bytes:
    """
    Create the websocket accept key to return in server handshake. Refers to RFC6455.

    The `Sec-WebSocket-Key` minus any leading and trailing whitespace
    should be concatenated with the GUID string.

    A SHA-1 (160 bits) hash of this concatentated value should then be base64-encoded
    and returned in the server handshake.
    """
    header_key = sec_websocket_key.strip()
    accept_key = header_key + WEBSOCKET_ACCEPT_GUID.encode()
    accept_key_hash = hashlib.sha1(accept_key)
    accept_key_response = base64.b64encode(accept_key_hash.digest())
    return accept_key_response
