"""
mcp/playwright_wrapper.py — Wrapped Playwright MCP client.

Architecture
------------
Tool Call
    → Telemetry Start (span open)
    → AuthGuard.ensure_login()
    → RetryEngine.execute()
        → PlaywrightMCPProcess.call_tool()
    → Capture Result
    → Telemetry End (span close, metrics recorded)

The wrapper proxies ALL MCP tool calls dynamically — no hardcoded tool list.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from core.auth_guard import auth_guard
from core.config import settings
from core.logger import logger
from core.retry_engine import MCP_POLICY, retry_engine
from core.telemetry import telemetry
from browser.browser_manager import browser_manager
from mcp.playwright_process import PlaywrightMCPProcess


class PlaywrightWrapper:
    """
    The central execution hub for all MCP tool calls.

    Usage::

        wrapper = PlaywrightWrapper()
        await wrapper.start()
        result = await wrapper.call_tool("browser_navigate", {"url": "https://example.com"})
        await wrapper.stop()
    """

    def __init__(self) -> None:
        self._process = PlaywrightMCPProcess()
        self._available_tools: Optional[list[dict]] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the Playwright MCP subprocess and warm up the browser."""
        logger.info("playwright_wrapper_starting")
        await self._process.start()
        await browser_manager.ensure_ready()
        self._available_tools = await self._process.list_tools()
        logger.info(
            "playwright_wrapper_ready",
            tool_count=len(self._available_tools or []),
        )

    async def stop(self) -> None:
        """Gracefully stop the MCP subprocess and browser."""
        await self._process.stop()
        await browser_manager.shutdown()
        logger.info("playwright_wrapper_stopped")

    # ── Tool execution ─────────────────────────────────────────────────────────

    async def call_tool(
        self,
        tool_name: str,
        arguments: Optional[dict[str, Any]] = None,
    ) -> Any:
        """
        Execute a single MCP tool with full middleware stack.

        Parameters
        ----------
        tool_name:
            Name of the Playwright MCP tool (e.g. 'browser_navigate').
        arguments:
            Tool argument dict.

        Returns
        -------
        Any
            Parsed JSON response from the Playwright MCP process.
        """
        arguments = arguments or {}
        t0 = time.monotonic()
        success = False

        async with telemetry.span(
            "mcp.tool_call",
            attributes={"tool": tool_name, "args_keys": ",".join(arguments.keys())},
        ):
            try:
                # 1. Auth guard
                await auth_guard.ensure_login(browser_manager)

                # 2. Normalise screenshot args before execution
                if "screenshot" in tool_name.lower():
                    arguments = _normalise_screenshot_args(arguments)

                # 3. Execute with retry
                result = await retry_engine.execute(
                    self._process.call_tool,
                    MCP_POLICY,
                    tool_name,
                    arguments,
                )

                success = True
                return result

            except Exception as exc:
                logger.error(
                    "tool_call_failed",
                    tool=tool_name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                raise

            finally:
                elapsed_ms = (time.monotonic() - t0) * 1000
                telemetry.record_tool_call(tool_name, success, elapsed_ms)
                logger.info(
                    "tool_call_complete",
                    tool=tool_name,
                    success=success,
                    latency_ms=round(elapsed_ms, 2),
                )

    async def list_tools(self) -> list[dict]:
        """Return dynamically discovered tool list from the Playwright MCP server."""
        if self._available_tools is None:
            self._available_tools = await self._process.list_tools()
        return self._available_tools or []

    async def health_check(self) -> dict[str, Any]:
        """Return a health snapshot for readiness / liveness probes."""
        return {
            "process_alive": self._process.is_alive(),
            "browser_alive": browser_manager.is_alive(),
            "tools_discovered": len(await self.list_tools()),
        }


# ── Screenshot normalisation ───────────────────────────────────────────────────

def _normalise_screenshot_args(args: dict[str, Any]) -> dict[str, Any]:
    """
    Playwright MCP rejects fullPage=true when a selector is also specified.
    Auto-remove fullPage when a selector is present to prevent crashes.
    """
    if args.get("selector") and args.get("fullPage"):
        logger.warning(
            "screenshot_args_normalised",
            msg="Removed fullPage=True because selector was provided.",
        )
        args = {**args}
        del args["fullPage"]
    return args


# Singleton
playwright_wrapper = PlaywrightWrapper()
