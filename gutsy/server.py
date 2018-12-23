import logging
import asyncio
import sys
import argparse
import importlib
from functools import partial

from starlette.types import ASGIApp

from aiobufpro.protocol import HTTPWSProtocol


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()


class Server:
    async def run_server(self, app: ASGIApp, host: str, port: int) -> None:
        """
        Run protocol server that will handle both HTTP and WebSocket requests.
        """
        loop = asyncio.get_running_loop()
        protocol = partial(HTTPWSProtocol, app=app)
        server = await loop.create_server(protocol, host=host, port=port)

        async with server:
            await server.serve_forever()

    def run(self, app: ASGIApp, *, host: str, port: int, debug: bool) -> None:
        if debug:

            # Wrap the ASGI application in debug middleware from the Starlette to have
            # error traceback printed in the browser.
            # https://www.starlette.io/debug/
            from starlette.middleware.errors import ServerErrorMiddleware

            app = ServerErrorMiddleware(app)

        logger.warning(f"Running protocol server on {host}:{port}")

        try:
            asyncio.run(self.run_server(app, host, port))
        except Exception as exc:
            logger.warning(f"Exception in event loop: {exc}")
        finally:
            logger.warning(f"Closing protocol server on {host}:{port}")


def main(args=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("app", help="ASGI application")
    parser.add_argument("--host", default="0.0.0.0", help="Host")
    parser.add_argument("--port", default="8000", help="Port")
    parser.add_argument("--debug", action="store_true", help="Debug")
    args = parser.parse_args()
    app_module, asgi_callable = args.app.split(":")
    sys.path.insert(0, ".")
    app = getattr(importlib.import_module(app_module), asgi_callable)
    Server().run(app, host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
