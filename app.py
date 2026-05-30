"""
app.py - Entry point for the Playwright MCP Wrapper Server.

Modes
-----
1. stdio  (default) - reads JSON-RPC from stdin, writes responses to stdout.
2. http   - serves JSON-RPC over HTTP POST /mcp (set SERVER_MODE=http).
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

from core.config import settings
from core.logger import logger
from core.telemetry import telemetry
from mcp.playwright_wrapper import playwright_wrapper
from mcp.tool_router import tool_router


async def _read_stdin_line(loop: asyncio.AbstractEventLoop) -> bytes:
    return await loop.run_in_executor(None, sys.stdin.buffer.readline)


async def _write_stdout(loop: asyncio.AbstractEventLoop, data: bytes) -> None:
    await loop.run_in_executor(None, sys.stdout.buffer.write, data)
    await loop.run_in_executor(None, sys.stdout.buffer.flush)


async def run_stdio() -> None:
    """
    Run the MCP server in stdio mode.

    Avoid asyncio's pipe transports here. On Windows, especially from an
    interactive PowerShell console, Proactor pipe transports can fail with
    WinError 6 against console handles. Executor-backed blocking IO works for
    both interactive smoke tests and real MCP stdio pipes.
    """
    logger.info("server_mode_stdio")
    loop = asyncio.get_running_loop()

    try:
        while True:
            line = await _read_stdin_line(loop)
            if not line:
                logger.info("server_stdin_eof")
                break

            line = line.strip()
            if not line:
                continue

            response = await tool_router.handle(line)
            await _write_stdout(loop, response)
    except asyncio.CancelledError:
        raise
    except EOFError:
        logger.info("server_stdin_eof")


async def run_http() -> None:
    """
    Run the MCP server as a minimal HTTP server.

    POST /mcp    - MCP JSON-RPC endpoint
    GET /health  - liveness probe
    GET /ready   - readiness probe
    """
    try:
        from aiohttp import web
    except ImportError:
        logger.error("aiohttp_not_installed", hint="pip install aiohttp")
        sys.exit(1)

    async def mcp_handler(request: web.Request) -> web.Response:
        body = await request.read()
        response = await tool_router.handle(body)
        return web.Response(body=response, content_type="application/json")

    async def health_handler(request: web.Request) -> web.Response:
        health = await playwright_wrapper.health_check()
        status = 200 if health["browser_alive"] else 503
        return web.json_response(health, status=status)

    app = web.Application()
    app.router.add_post("/mcp", mcp_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/ready", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.server_host, settings.server_port)
    await site.start()

    logger.info(
        "server_mode_http",
        host=settings.server_host,
        port=settings.server_port,
    )
    await asyncio.Event().wait()


async def main() -> None:
    mode = os.getenv("SERVER_MODE", "stdio").lower()

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("shutdown_signal_received")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _signal_handler())

    logger.info("server_starting", mode=mode, version="1.0.0")

    await playwright_wrapper.start()

    server_task = asyncio.create_task(run_stdio() if mode == "stdio" else run_http())
    shutdown_task = asyncio.create_task(shutdown_event.wait())

    try:
        done, pending = await asyncio.wait(
            [server_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        for task in done:
            await task
    except asyncio.CancelledError:
        logger.info("server_cancelled")
        raise
    finally:
        logger.info("server_stopping")
        await playwright_wrapper.stop()
        telemetry.shutdown()
        logger.info("server_stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
