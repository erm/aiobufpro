# aiobufpro

This is forked from another project https://github.com/erm/gutsy. Work in progress, experiment, etc.

I wanted to continue experimenting with my own implementation of the WebSocket/HTTP protocols using asyncio's BufferedProtocol, but also wanted to finish a complete ASGI server implementation using some more stable libraries.

## TODO

- Strip out all the ASGI connection classes to focus purely on the protocol implementations
- Separate the HTTP and WS protocol class