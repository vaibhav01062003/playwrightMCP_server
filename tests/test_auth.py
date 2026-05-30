"""
tests/test_auth.py — Tests for core/auth_guard.py
"""

from __future__ import annotations

import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.auth_guard import AuthGuard, _fill_login_form


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_browser(alive: bool = True, cookies: list = None) -> MagicMock:
    bm = MagicMock()
    bm.is_alive.return_value = alive
    bm.get_cookies = AsyncMock(return_value=cookies or [])
    bm.get_page = AsyncMock()
    bm.ensure_ready = AsyncMock()
    return bm


# ── Session validation ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_login_skips_when_valid():
    guard = AuthGuard()
    guard._last_login_ts = time.monotonic()  # Just logged in

    bm = _mock_browser(
        alive=True,
        cookies=[{"name": "session_token", "value": "abc"}],
    )

    with patch("core.config.settings.login_url", "https://example.com/login"):
        with patch("core.config.settings.session_ttl", 3600):
            # Should not perform login
            with patch.object(guard, "_perform_login", new_callable=AsyncMock) as mock_login:
                await guard.ensure_login(bm)
                mock_login.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_login_triggers_when_browser_dead():
    guard = AuthGuard()
    bm = _mock_browser(alive=False)

    with patch.object(guard, "_perform_login", new_callable=AsyncMock) as mock_login:
        await guard.ensure_login(bm)
        mock_login.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_login_triggers_when_ttl_expired():
    guard = AuthGuard()
    guard._last_login_ts = time.monotonic() - 9999  # Definitely expired

    bm = _mock_browser(alive=True)

    with patch.object(guard, "_perform_login", new_callable=AsyncMock) as mock_login:
        await guard.ensure_login(bm)
        mock_login.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_login_triggers_when_no_auth_cookies():
    guard = AuthGuard()
    guard._last_login_ts = time.monotonic()  # Fresh timestamp

    bm = _mock_browser(
        alive=True,
        cookies=[{"name": "analytics_id", "value": "xyz"}],  # No auth cookie
    )

    with patch("core.config.settings.login_url", "https://example.com/login"):
        with patch("core.config.settings.session_ttl", 3600):
            with patch.object(guard, "_perform_login", new_callable=AsyncMock) as mock_login:
                await guard.ensure_login(bm)
                mock_login.assert_awaited_once()


# ── Invalidation ──────────────────────────────────────────────────────────────

def test_invalidate_resets_timestamp():
    guard = AuthGuard()
    guard._last_login_ts = time.monotonic()
    guard.invalidate()
    assert guard._last_login_ts == 0.0


# ── has_auth_cookies ─────────────────────────────────────────────────────────

def test_has_auth_cookies_positive():
    assert AuthGuard._has_auth_cookies([{"name": "session_token"}])
    assert AuthGuard._has_auth_cookies([{"name": "auth_key"}])
    assert AuthGuard._has_auth_cookies([{"name": "access_token"}])


def test_has_auth_cookies_negative():
    assert not AuthGuard._has_auth_cookies([])
    assert not AuthGuard._has_auth_cookies([{"name": "analytics"}])
    assert not AuthGuard._has_auth_cookies([{"name": "theme"}])
