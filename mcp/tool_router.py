"""
mcp/tool_router.py — Routes incoming MCP JSON-RPC requests to the PlaywrightWrapper.

Handles:
  - tools/list  → return available Playwright tools
  - tools/call  → proxy to PlaywrightWrapper.call_tool()
  - Validation of incoming request shape
  - Structured error responses per MCP spec
"""

from __future__ import annotations

import json
from typing import Any

from core.logger import logger
from mcp.playwright_wrapper import playwright_wrapper

_JSONRPC = "2.0"
_MCP_PROTOCOL_VERSION = "2025-06-18"


def _ok(result: Any, msg_id: Any) -> dict:
    return {"jsonrpc": _JSONRPC, "id": msg_id, "result": result}


def _err(code: int, message: str, msg_id: Any, data: Any = None) -> dict:
    error: dict = {"code": code, "message": message}
    if data is not None:
        error["data"] = str(data)
    return {"jsonrpc": _JSONRPC, "id": msg_id, "error": error}


# Standard JSON-RPC error codes
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


class ToolRouter:
    """
    Routes JSON-RPC MCP messages to the correct handler.

    Usage::

        router = ToolRouter()
        response = await router.handle(raw_json_bytes)
    """

    async def handle(self, raw: bytes | str) -> bytes:
        """
        Parse a raw JSON-RPC message and return a JSON-encoded response.
        Always returns a valid JSON-RPC response (never raises).
        """
        msg_id: Any = None
        try:
            msg = json.loads(raw)
            msg_id = msg.get("id")
            method: str = msg.get("method", "")
            params: dict = msg.get("params", {})

            if not method:
                return _encode(_err(_INVALID_REQUEST, "Missing method", msg_id))

            result = await self._dispatch(method, params, msg_id)
            return _encode(result)

        except json.JSONDecodeError as exc:
            return _encode(_err(_PARSE_ERROR, "Parse error", msg_id, exc))
        except Exception as exc:
            logger.error("tool_router_unhandled", error=str(exc))
            return _encode(_err(_INTERNAL_ERROR, "Internal server error", msg_id, exc))

    async def _dispatch(self, method: str, params: dict, msg_id: Any) -> dict:
        logger.debug("tool_router_dispatch", method=method, id=msg_id)

        # ── Handshake ──────────────────────────────────────────────────────────
        if method == "initialize":
            return _ok(
                {
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "playwright-python-wrapper", "version": "1.0.0"},
                },
                msg_id,
            )

        if method == "notifications/initialized":
            return _ok({}, msg_id)

        # ── Tool list ──────────────────────────────────────────────────────────
        if method == "tools/list":
            tools = await playwright_wrapper.list_tools()
            return _ok({"tools": tools}, msg_id)

        # ── Tool call ──────────────────────────────────────────────────────────
        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})

            if not tool_name:
                return _err(_INVALID_PARAMS, "Missing 'name' in params", msg_id)

            result = await playwright_wrapper.call_tool(tool_name, arguments)
            return _ok(result, msg_id)

        # ── Health check ───────────────────────────────────────────────────────
        if method == "health":
            health = await playwright_wrapper.health_check()
            return _ok(health, msg_id)

        return _err(_METHOD_NOT_FOUND, f"Unknown method: {method}", msg_id)


def _encode(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode()


# Singleton
tool_router = ToolRouter()
