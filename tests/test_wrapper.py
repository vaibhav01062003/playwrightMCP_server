"""
tests/test_wrapper.py — Tests for PlaywrightWrapper and ToolRouter
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.tool_router import ToolRouter, _ok, _err


# ── ToolRouter ────────────────────────────────────────────────────────────────

@pytest.fixture
def router():
    return ToolRouter()


@pytest.mark.asyncio
async def test_router_initialize(router):
    msg = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1"},
        },
    })
    raw = await router.handle(msg)
    response = json.loads(raw)
    assert response["id"] == 1
    assert "result" in response
    assert response["result"]["protocolVersion"] == "2025-06-18"


@pytest.mark.asyncio
async def test_router_tools_list(router):
    mock_tools = [{"name": "browser_navigate", "description": "Navigate to URL"}]

    with patch("mcp.tool_router.playwright_wrapper") as mock_wrapper:
        mock_wrapper.list_tools = AsyncMock(return_value=mock_tools)
        raw = await router.handle(json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }))
        response = json.loads(raw)
        assert response["result"]["tools"] == mock_tools


@pytest.mark.asyncio
async def test_router_tool_call(router):
    with patch("mcp.tool_router.playwright_wrapper") as mock_wrapper:
        mock_wrapper.call_tool = AsyncMock(return_value={"content": [{"type": "text", "text": "done"}]})
        raw = await router.handle(json.dumps({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "browser_navigate",
                "arguments": {"url": "https://example.com"},
            },
        }))
        response = json.loads(raw)
        assert "result" in response
        mock_wrapper.call_tool.assert_awaited_once_with(
            "browser_navigate", {"url": "https://example.com"}
        )


@pytest.mark.asyncio
async def test_router_unknown_method(router):
    raw = await router.handle(json.dumps({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "unknown/method",
        "params": {},
    }))
    response = json.loads(raw)
    assert "error" in response
    assert response["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_router_parse_error(router):
    raw = await router.handle(b"this is not json {{")
    response = json.loads(raw)
    assert "error" in response
    assert response["error"]["code"] == -32700


@pytest.mark.asyncio
async def test_router_missing_tool_name(router):
    raw = await router.handle(json.dumps({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"arguments": {}},  # missing "name"
    }))
    response = json.loads(raw)
    assert "error" in response
    assert response["error"]["code"] == -32602


# ── Screenshot normalisation ──────────────────────────────────────────────────

def test_screenshot_args_normalised():
    from mcp.playwright_wrapper import _normalise_screenshot_args
    args = {"selector": "#main", "fullPage": True}
    result = _normalise_screenshot_args(args)
    assert "fullPage" not in result
    assert result["selector"] == "#main"


def test_screenshot_args_no_conflict():
    from mcp.playwright_wrapper import _normalise_screenshot_args
    args = {"fullPage": True}
    result = _normalise_screenshot_args(args)
    assert result["fullPage"] is True
