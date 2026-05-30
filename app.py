"""
app.py — Entry point for the Playwright MCP Wrapper Server.

Modes
-----
1. stdio  (default) — reads JSON-RPC from stdin, writes to stdout.
          Compatible with Azure Foundry MCP config (command/args style).
2. http   — serves JSON-RPC over HTTP POST /mcp (set SERVER_MODE=http).
          Suitable for Azure Container Apps with HTTP triggers.

Run
---
    python app.py            # stdio mode
    SERVER_MODE=http python app.py   # HTTP mode
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


# ── STDIO mode ─────────────────────────────────────────────────────────────────

async def run_stdio() -> None:
    """
    Run the MCP server in stdio mode.
    Reads newline-delimited JSON-RPC messages from stdin.
    Writes newline-delimited JSON-RPC responses to stdout.
    """
    logger.info("server_mode_stdio")
    loop = asyncio.get_event_loop()

    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)

    writer_transport, writer_protocol = await loop.connect_write_pipe(
        asyncio.BaseProtocol, sys.stdout.buffer
    )

    async def write(data: bytes) -> None:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    try:
        async for line in reader:
            line = line.strip()
            if not line:
                continue
            response = await tool_router.handle(line)
            await write(response)
    except asyncio.CancelledError:
        pass
    except EOFError:
        logger.info("server_stdin_eof")


# ── HTTP mode ──────────────────────────────────────────────────────────────────

async def run_http() -> None:
    """
    Run the MCP server as a minimal HTTP server (aiohttp).
    POST /mcp        → MCP JSON-RPC endpoint
    GET  /health     → Liveness probe
    GET  /ready      → Readiness probe
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
    # Keep running
    await asyncio.Event().wait()


# ── Startup / shutdown ─────────────────────────────────────────────────────────

async def main() -> None:
    mode = os.getenv("SERVER_MODE", "stdio").lower()

    # Graceful shutdown hook
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("shutdown_signal_received")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass  # Windows

    logger.info("server_starting", mode=mode, version="1.0.0")

    # Start the wrapper (launches Playwright MCP subprocess + browser)
    await playwright_wrapper.start()

    try:
        server_task = asyncio.create_task(
            run_stdio() if mode == "stdio" else run_http()
        )
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        done, pending = await asyncio.wait(
            [server_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

    finally:
        logger.info("server_stopping")
        await playwright_wrapper.stop()
        telemetry.shutdown()
        logger.info("server_stopped")


if __name__ == "__main__":
    asyncio.run(main())
