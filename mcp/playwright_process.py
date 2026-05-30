"""
mcp/playwright_process.py — Manages the npx @playwright/mcp subprocess.

Responsibilities:
  - Launch `npx @playwright/mcp@latest` as a child process
  - Communicate over stdio using JSON-RPC (MCP protocol)
  - Health checks and automatic restart
  - Graceful shutdown
  - Dynamic tool discovery via tools/list
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from core.config import settings
from core.logger import logger


_JSONRPC_VERSION = "2.0"
_MCP_PROTOCOL_VERSION = "2025-06-18"


def _resolve_command(command: str) -> str:
    resolved = shutil.which(command)
    if resolved:
        return resolved

    if sys.platform == "win32" and not command.lower().endswith((".cmd", ".exe", ".bat")):
        for extension in (".cmd", ".exe", ".bat"):
            resolved = shutil.which(command + extension)
            if resolved:
                return resolved

    raise FileNotFoundError(
        f"Could not find Playwright MCP command '{command}' on PATH. "
        "Install Node.js or set PLAYWRIGHT_MCP_COMMAND to the full path of npx.cmd."
    )


def _find_cached_playwright_mcp_cli() -> Optional[Path]:
    cache_root = Path(os.getenv("LOCALAPPDATA", "")) / "npm-cache" / "_npx"
    if not cache_root.exists():
        return None

    candidates = [
        package_json.parent / "cli.js"
        for package_json in cache_root.glob("*/node_modules/@playwright/mcp/package.json")
        if (package_json.parent / "cli.js").exists()
    ]
    if not candidates:
        return None

    return max(candidates, key=lambda path: path.stat().st_mtime)


def _resolve_windows_npx_command(args: list[str]) -> Optional[list[str]]:
    if sys.platform != "win32" or not args or not args[0].startswith("@playwright/mcp"):
        return None

    cli_path = _find_cached_playwright_mcp_cli()
    if cli_path is None:
        npx = _resolve_command(settings.playwright_mcp_command)
        subprocess.run(
            [npx, args[0], "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=120,
        )
        cli_path = _find_cached_playwright_mcp_cli()

    node = _resolve_command("node")
    if cli_path is None:
        raise FileNotFoundError(
            "Could not find @playwright/mcp in the npm npx cache. "
            "Run `npx @playwright/mcp@latest --version`, then retry."
        )

    return [node, str(cli_path), *args[1:]]


def _rpc(method: str, params: Any, msg_id: int) -> bytes:
    payload = {
        "jsonrpc": _JSONRPC_VERSION,
        "id": msg_id,
        "method": method,
        "params": params,
    }
    raw = json.dumps(payload) + "\n"
    return raw.encode()


class PlaywrightMCPProcess:
    """
    Manages the lifecycle of a `npx @playwright/mcp@latest` subprocess and
    exposes a clean async API for MCP method calls.
    """

    def __init__(self) -> None:
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._msg_id = 0
        self._lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task] = None
        self._pending: dict[int, asyncio.Future] = {}

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Launch the Playwright MCP subprocess."""
        cmd = None
        if settings.playwright_mcp_command.lower() in {"npx", "npx.cmd"}:
            cmd = _resolve_windows_npx_command(settings.playwright_mcp_args)
        if cmd is None:
            command = _resolve_command(settings.playwright_mcp_command)
            cmd = [command] + settings.playwright_mcp_args
        if settings.playwright_headless:
            cmd += ["--headless"]

        logger.info("playwright_mcp_process_starting", cmd=" ".join(cmd))

        env = {**os.environ}

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # Start background reader
        self._reader_task = asyncio.create_task(self._read_loop(), name="mcp-reader")

        # Initialise MCP session
        await self._initialize()

        logger.info("playwright_mcp_process_started", pid=self._proc.pid)

    async def stop(self) -> None:
        """Gracefully terminate the subprocess."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except Exception:  # noqa: BLE001
                self._proc.kill()
        logger.info("playwright_mcp_process_stopped")

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def restart(self) -> None:
        logger.warning("playwright_mcp_process_restarting")
        await self.stop()
        await self.start()

    # ── MCP calls ──────────────────────────────────────────────────────────────

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a Playwright MCP tool via JSON-RPC."""
        if not self.is_alive():
            logger.warning("playwright_mcp_dead_restarting")
            await self.restart()

        return await self._rpc_call(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )

    async def list_tools(self) -> list[dict]:
        """Discover all tools exposed by the Playwright MCP server."""
        try:
            result = await self._rpc_call("tools/list", {})
            tools = result.get("tools", [])
            logger.info("playwright_mcp_tools_discovered", count=len(tools))
            return tools
        except Exception as exc:
            logger.error("playwright_mcp_list_tools_failed", error=str(exc))
            return []

    # ── JSON-RPC internals ─────────────────────────────────────────────────────

    async def _initialize(self) -> None:
        """Send MCP initialize handshake."""
        await self._rpc_call(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "playwright-python-wrapper", "version": "1.0.0"},
            },
        )
        # Send initialized notification (no response expected)
        await self._send({"jsonrpc": _JSONRPC_VERSION, "method": "notifications/initialized", "params": {}})

    async def _rpc_call(self, method: str, params: Any) -> Any:
        async with self._lock:
            self._msg_id += 1
            msg_id = self._msg_id

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut

        await self._send(_rpc(method, params, msg_id).decode())

        try:
            response = await asyncio.wait_for(fut, timeout=settings.timeout)
        finally:
            self._pending.pop(msg_id, None)

        if "error" in response:
            raise RuntimeError(f"MCP error [{method}]: {response['error']}")
        return response.get("result", {})

    async def _send(self, payload: dict[str, Any] | str | bytes) -> None:
        assert self._proc and self._proc.stdin
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        if isinstance(payload, str):
            payload = payload.encode()
        self._proc.stdin.write(payload if payload.endswith(b"\n") else payload + b"\n")
        await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        """Background task: read stdout and resolve pending futures."""
        assert self._proc and self._proc.stdout
        try:
            async for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("mcp_non_json_stdout", raw=line.decode(errors="replace"))
                    continue

                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._pending:
                    self._pending[msg_id].set_result(msg)
                elif "method" in msg:
                    # Notification — log and ignore
                    logger.debug("mcp_notification", method=msg.get("method"))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("mcp_read_loop_error", error=str(exc))
