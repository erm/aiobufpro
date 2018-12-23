import enum

import logging
from typing import List
from urllib.parse import urlparse


logger = logging.getLogger()


class HTTPParserState(enum.Enum):
    """Current state of the HTTP parser."""

    PARSING_REQUEST = 0
    PARSING_HEADERS = 1
    PARSING_COMPLETE = 2


class HTTPParser:
    """
    Parse the incoming request headers until they are completely received, and store the
    parsed headers as name/value pair in the `headers`.

    * `http_method` -
        (*str*): The decoded HTTP method bytes string that begins the request line.

    * `http_version` -
        (*str*): The decoded HTTP version bytes string from the request line.

    * `path` -
        (*str*): The decoded path bytes string from the request line.

    * `query_string` -
        (*str*): The decoded query string bytes string from the request line.

    * `raw_headers` -
        (*bytes*): Store the raw headers a complete bytes string.

    * `headers` -
        (*List[Tuple[bytes, bytes]]*): A list of all the parsed header name/value pairs.

    * `parsing_data` -
        (*bytes*): The incoming data being parsed to form a header.

    * `parsing_header` -
        (*bytes*): The incomplete header that is built by the `parsing_data`.

    * `parsing_sep_pos` -
        (*int*): Index of the colon that separates the parsing header's name and value.

    * `next_header` -
        (*bytes*): The latest completed header bytes string that will be parsed and
        appended to the `headers` list.

    * `next_sep_pos` -
        (*int*): The `parsing_sep_pos` of a completed `parsing_header` used to parse the
        the name and value of the `next_header`.

    * `should_upgrade` -
        (*bool*):
        Set if the connection header indicates an upgrade request, default is `None`.

    * `upgrade_header` -
        (*Tuple[bytes, bytes]*):
        Set if the request contains an upgrade header and `should_upgrade` is `True`,
        default is `None`.
    """

    def __init__(self):
        self.state: HTTPParserState = HTTPParserState.PARSING_REQUEST
        self.http_method: str = None
        self.http_version: str = None
        self.path: str = None
        self.query_string: str = None
        self.headers: List = []
        self.parsing_data: bytes = None
        self.parsing_header: bytes = b""
        self.parsing_sep_pos: int = None
        self.next_sep_pos: int = None
        self.next_header: bytes = b""
        self.raw_headers = b""
        self.upgrade_header = None
        self.should_upgrade = None

    @property
    def is_complete(self):
        return self.state is HTTPParserState.PARSING_COMPLETE

    def parse_headers(self, data: bytes) -> None:
        """
        Parse the incoming bytes data sent by the `HTTPBufferedProtocol` to build
        the HTTP request headers.

        * `data` -
            (*bytes*): A byte string received by the protocol buffer that contains
            information related to HTTP request headers.
        """
        self.raw_headers += data
        _parsing_data = self.parsing_data

        # If we are currently parsing an incomplete header, append the incoming data
        # to the end of the parsing data.
        if _parsing_data is not None:
            data = _parsing_data + data

        data_len = len(data)

        # Iterate the byte string to determine the state of the incoming header request.
        for i in range(data_len):

            # If there is not already a header being parsed during the first cycle of
            # the loop, then the parsing header will initially be an empty byte string.
            _parsing_header = self.parsing_header

            # Slice the byte value for the current iteration.
            b = data[i : i + 1]

            # Append the byte value to the header currently be read by the protocol.
            _parsing_header += b

            # Decrement the initial data size that was set when this method was first
            # called to accomodate additional incoming data being appended to the
            # `parsing_data` byte string for the current header.
            data_len -= 1

            if b == b":":

                # This byte value may indicate the header name has been completely read,
                # or it could be contained with a header value. This is determined by
                # the current parser state and `parsing_sep_pos`.
                _parsing_sep_pos = self.parsing_sep_pos

                if (
                    _parsing_sep_pos is None
                    and self.state is not HTTPParserState.PARSING_REQUEST
                ):
                    # If this is the first occurrence of a colon for the header that is
                    # currently being parsed (i.e.: `parsing_sep_pos` is set), and the
                    # parser has already parsed the http request method and version string,
                    # then this byte value is the correct separator index.
                    self.parsing_sep_pos = len(_parsing_header)

            if b == b"\n":
                if self.state is not HTTPParserState.PARSING_HEADERS:

                    # The first occurence of the newline character indicates that the
                    # http request method and version string has been completely read.
                    http_method, path, http_version = (
                        _parsing_header.decode("ascii").strip().split(" ")
                    )

                    # Set the http method, version, and any potential query string data
                    # on the parser instance, then update the parser state to continue
                    # reading the headers.
                    self.http_method = http_method
                    self.http_version = http_version
                    _parsed_path = urlparse(path)
                    self.path = _parsed_path.path
                    self.query_string = _parsed_path.query
                    self.state = HTTPParserState.PARSING_HEADERS

                else:

                    # A header has been fully read. If there is no `next_header` value
                    # defined, then it will be finalised and set during the current
                    # iteration.
                    _next_header = self.next_header

                    if _next_header is not None and _next_header != b"":
                        _next_sep_pos = self.next_sep_pos

                        # Slice the latest parsed header name and value and append to
                        # the headers list.
                        current_header = _next_header[: _next_sep_pos - 1]
                        current_value = _next_header[
                            _next_sep_pos + 1 : len(_next_header) - 2
                        ]
                        header = (current_header, current_value)

                        if self.should_upgrade is None:

                            # The Connection header has not yet been parsed. If it's the
                            # current header, then use it to determine if this is an
                            # upgrade request.
                            if current_header.lower() == b"connection":

                                if b"upgrade" not in current_value.lower():
                                    self.should_upgrade = False
                                else:
                                    logger.debug("Upgrade request identified.")
                                    self.should_upgrade = True

                        elif self.should_upgrade and self.upgrade_header is None:

                            # The Connection header indicated an upgrade request, check
                            # if the current header is the upgrade header.
                            if current_header.lower() == b"upgrade":
                                logger.debug(f"Upgrade header identified: {header}")
                                self.upgrade_header = header

                        self.headers.append(header)

                    # The headers have been completely parsed. The current iteration
                    # will update the state and the next iteration will cleanup the
                    # remaining parsing data and include the final header in the
                    # `headers` list.
                    if _parsing_header == b"\r\n":
                        _next_header = self.next_header
                        _next_header += _parsing_header
                        self.next_header = _next_header
                        self.state = HTTPParserState.PARSING_COMPLETE

                    # A header has been completed parsed, set the `next_header` bytes
                    # and the `next_sep_pos` index for the current parsing header. The
                    # next iteration that completely parses a header will append the
                    # `next_header` value to the headers list.
                    _parsing_sep_pos = self.parsing_sep_pos
                    self.next_sep_pos = _parsing_sep_pos
                    self.next_header = _parsing_header

                    # Reset the separator index for the parsing header.
                    self.parsing_sep_pos = None

                # Reset the parsing header, this will occur in both the request line and
                # general header parsing cases.
                self.parsing_header = b""
            else:
                # An incomplete header is still being parsed, set the `parsing_header`
                # to the current value of the loop and continue iteration.
                self.parsing_header = _parsing_header

        if data_len > 0:
            # If there is any data remaining after the loop completes then it is set as
            # the `parsing_data` to be processed first during the next loop.
            self.parsing_data = data
