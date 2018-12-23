import asyncio
import enum
from typing import Union, Tuple


def xor(bytes_one: int, bytes_two: int) -> int:
    """
    Implements an XOR (exclusive or) operation used when unmasking a WebSocket frame
    payload. Converts the bytes values two bitstrings, then compares each bit value
    according to the "one or the other, but not both" criteria.
    """
    bitstr_one = f"{bytes_one:08b}"
    bitstr_two = f"{bytes_two:08b}"

    # Iterate and bool the sum of each bit comparison to generate the unmasked
    # bitstring, casting the result to an integer then finally to a string.
    result_bitstr = "".join(
        [f"{int(int(bitstr_one[x]) + int(bitstr_two[x]) == 1)}" for x in range(8)]
    )

    # Cast the bitstring to an integer for use in the payload bytearray generator.
    return int(result_bitstr, 2)


# Frame indexes used for parsing the bits from the incoming bytes data.
HEAD_FRAME_INDEXES = ((0, 1), (1, 2), (2, 3), (3, 4), (4, 8))
MASK_AND_PAYLOAD_LEN_FRAME_INDEXES = ((0, 1), (1, 8))


class WebSocketError(Exception):
    """Raised when an error occurs in the WebSocket protocol."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return self.message


class WebSocketParserState(enum.Enum):
    """Manage the control flow when decoding the incoming WebSocket frames."""

    INITIAL_BYTES = enum.auto()
    PAYLOAD_LENGTH = enum.auto()
    MASKING_KEY = enum.auto()


class WebSocketCloseCode(enum.Enum):
    """
    Endpoints MAY use the following pre-defined status codes when sending
    a Close frame:

    * 1000 indicates a normal closure, meaning that the purpose for
      which the connection was established has been fulfilled.

    * 1001 indicates that an endpoint is "going away", such as a server
      going down or a browser having navigated away from a page.

    * 1002 indicates that an endpoint is terminating the connection due
      to a protocol error.

    * 1003 indicates that an endpoint is terminating the connection
      because it has received a type of data it cannot accept (e.g., an
      endpoint that understands only text data MAY send this if it
      receives a binary message).

    * 1007 indicates that an endpoint is terminating the connection
      because it has received data within a message that was not
      consistent with the type of the message (e.g., non-UTF-8 [RFC3629]
      data within a text message).

    * 1008 indicates that an endpoint is terminating the connection
      because it has received a message that violates its policy.  This
      is a generic status code that can be returned when there is no
      other more suitable status code (e.g., 1003 or 1009) or if there
      is a need to hide specific details about the policy.

    * 1009 indicates that an endpoint is terminating the connection
      because it has received a message that is too big for it to
      process.

    * 1010 indicates that an endpoint (client) is terminating the
      connection because it has expected the server to negotiate one or
      more extension, but the server didn't return them in the response
      message of the WebSocket handshake.  The list of extensions that
      are needed SHOULD appear in the /reason/ part of the Close frame.
      Note that this status code is not used by the server, because it
      can fail the WebSocket handshake instead.

    * 1011 indicates that a server is terminating the connection because
      it encountered an unexpected condition that prevented it from
      fulfilling the request.
    """

    NORMAL = 1000
    GOING_AWAY = 1001
    PROTOCOL_ERROR = 1002
    UNSUPPORTED_DATA = 1003
    INVALID_DATA = 1007
    POLICY_VIOLATION = 1008
    TOO_BIG = 1009
    MISSING_EXTENSION = 1010
    INTERNAL_SERVER_ERROR = 1011


class WebSocketOpcode(enum.Enum):
    """

    Non-control frame codes:

    * 1 (0x1), text frame.

    * 2 (0x2), binary frame.

    Control frames are identified by opcodes where the most significant
    bit of the opcode is 1.

    Control frames are used to communicate state about the WebSocket.
    Control frames can be interjected in the middle of a fragmented
    message.

    All control frames MUST have a payload length of 125 bytes or less
    and MUST NOT be fragmented.

    Control frames:

    * 8 (0x8), close frame.

    * 9 (0x9), ping frame.

    * 10 (0xA), pong frame.
    """

    TEXT = 1
    BINARY = 2
    CLOSE = 8
    PING = 9
    PONG = 10


class WebSocketParser:
    """
      0                   1                   2                   3
      0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
     +-+-+-+-+-------+-+-------------+-------------------------------+
     |F|R|R|R| opcode|M| Payload len |    Extended payload length    |
     |I|S|S|S|  (4)  |A|     (7)     |             (16/64)           |
     |N|V|V|V|       |S|             |   (if payload len==126/127)   |
     | |1|2|3|       |K|             |                               |
     +-+-+-+-+-------+-+-------------+ - - - - - - - - - - - - - - - +
     |     Extended payload length continued, if payload len == 127  |
     + - - - - - - - - - - - - - - - +-------------------------------+
     |                               |Masking-key, if MASK set to 1  |
     +-------------------------------+-------------------------------+
     | Masking-key (continued)       |          Payload Data         |
     +-------------------------------- - - - - - - - - - - - - - - - +
     :                     Payload Data continued ...                :
     + - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - +
     |                     Payload Data continued ...                |
     +---------------------------------------------------------------+
     """

    def __init__(self, protocol: asyncio.BufferedProtocol) -> None:
        # self.current_frame: WebSocketFrame = None
        # self.frames = []
        self.protocol: asyncio.BufferedProtocol = protocol
        self.state: WebSocketParserState = WebSocketParserState.INITIAL_BYTES

    def get_frame_values(self, data: bytes, indexes: Tuple[int, int]) -> int:
        """
        Format the `data` bytes into its bitstring representation, then iterate the
        specified indexes to slice the relevant header values.
        """
        bitstr = f"{data:08b}"
        return (int(bitstr[i[0] : i[1]], 2) for i in indexes)

    async def parse_frame(self, data: bytes) -> None:
        if self.state is WebSocketParserState.INITIAL_BYTES and len(data) >= 2:

            # Read the first two bytes of the websocket frame into a tuple of two
            # unsigned integers.
            bytes_one, bytes_two = data[:2]
            del data[:2]

            # The first value, `bytes_one` contains the first eight frame bits.
            #
            #  0 1 2 3 4 5 6 7
            # +-+-+-+-+-------+
            # |F|R|R|R| opcode|
            # |I|S|S|S|  (4)  |
            # |N|V|V|V|       |
            # | |1|2|3|       |
            # +---------------+

            fin, rsv1, rsv2, rsv3, opcode = self.get_frame_values(
                bytes_one, HEAD_FRAME_INDEXES
            )

            is_control_frame = opcode > 7
            opcode = WebSocketOpcode(opcode)

            # The second value, `bytes_two`, contains the second eight frame bits.
            #
            #  8 9 0 1 2 3 4 5
            # +--+------------+
            # |M| Payload len |
            # |A|     (7)     |
            # |S|             |
            # |K|             |
            # +---------------+

            mask, payload_len = self.get_frame_values(
                bytes_two, MASK_AND_PAYLOAD_LEN_FRAME_INDEXES
            )

            # All control frames MUST have a payload length of 125 bytes or less
            # and MUST NOT be fragmented.
            if is_control_frame:

                if not fin:

                    # If the value for `fin` is unset, then it is not the final frame.
                    raise WebSocketError(
                        WebSocketCloseCode.PROTOCOL_ERROR,
                        "Control frame is fragmented.",
                    )

                elif payload_len > 125:
                    raise WebSocketError(
                        WebSocketCloseCode.PROTOCOL_ERROR,
                        "Control frame payload greater than 125 bytes.",
                    )

            if mask != 1:

                # A client MUST mask all frames sent to the server.
                raise WebSocketError(
                    WebSocketCloseCode.PROTOCOL_ERROR, "Frame mask missing."
                )

            if payload_len <= 125:

                # If the payload length is 0-125, then that is the payload length,
                # and we can immediately continue to unmasking the payload.
                self.state = WebSocketParserState.MASKING_KEY
            else:
                self.state = WebSocketParserState.PAYLOAD_LENGTH

        #  0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
        # +-+-+-+-+-------+-+-------------+-------------------------------+
        # |                               |    Extended payload length    |
        # |                               |             (16/64)           |
        # |                               |   (if payload len==126/127)   |
        # |                               |                               |
        # +-+-+-+-+-------+-+-------------+ - - - - - - - - - - - - - - - +
        # |     Extended payload length continued, if payload len == 127  |
        # +-------------------------------- - - - - - - - - - - - - - - - +

        if self.state is WebSocketParserState.PAYLOAD_LENGTH:

            if payload_len < 127 and len(data) >= 2:

                # If 126, the following 2 bytes interpreted as a 16-bit unsigned integer
                # are the payload length.
                payload_len_bytes = 2

            elif len(data) >= 8:

                # If 127, the following 8 bytes interpreted as a 64-bit unsigned integer
                # (the most significant bit MUST be 0) are the payload length.
                payload_len_bytes = 8

            payload_len_data = data[:payload_len_bytes]
            del data[:payload_len_bytes]

            bitstr = f"{payload_len_data:08b}"
            payload_len = int(bitstr, 2)

            self.state = WebSocketParserState.MASKING_KEY

        if self.state is WebSocketParserState.MASKING_KEY and len(data) >= 4:

            #  0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
            # + - - - - - - - - - - - - - - - +-------------------------------+
            # |                               |Masking-key, if MASK set to 1  |
            # +-------------------------------+-------------------------------+
            # | Masking-key (continued)       |          Payload Data         |
            # +-------------------------------- - - - - - - - - - - - - - - - +
            # :                     Payload Data continued ...                :
            # + - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - +
            # |                     Payload Data continued ...                |
            # +---------------------------------------------------------------+

            # Masking-key is the next 32-bits of the frame, 4 bytes in `data`.
            masking_key = data[:4]
            del data[:4]

            # The value of `data` is now the encoded payload. To decode the payload,
            # the XOR (exclusive OR) operation is applied to each byte or
            # character with the octet at index modulo 4 of the masking key.
            payload_data = bytes(
                [xor(data[i], masking_key[i % 4]) for i in range(payload_len)]
            )
            del data[:payload_len]

            if not is_control_frame:

                message = {"type": "websocket.receive"}

                if opcode is WebSocketOpcode.TEXT:
                    message["text"] = payload_data.decode("latin-1")
                elif opcode is WebSocketOpcode.BYTES:
                    message["bytes"] = payload_data

                self.protocol.asgi_connection.put_message(message)

            elif opcode is WebSocketOpcode.PING:

                content = self.get_frame_content(payload_data, opcode=opcode)
                await self.protocol.feed_data(content)

            self.state = WebSocketParserState.INITIAL_BYTES

    async def get_frame_content(
        self,
        payload_data: Union[str, bytes],
        *,
        opcode: WebSocketOpcode,
        fin=1,
        rsv1=0,
        rsv2=0,
        rsv3=0,
    ) -> None:

        if isinstance(payload_data, str):
            payload_data = payload_data.encode()

        payload_len = len(payload_data)
        opcode = f"{opcode.value:04}"
        header = int(f"{fin}{rsv1}{rsv2}{rsv3}{opcode}", 2)
        content = bytes([header, payload_len]) + payload_data

        return content
