"""
core/auth_guard.py — Session / authentication lifecycle management.

Responsibilities:
  - ensure_login(): validate current browser session; re-authenticate if stale
  - Cookie and page-state validation
  - Session expiry tracking (configurable TTL)
  - Browser-closed recovery path
  - Telemetry emission on every auth event
  - RetryEngine integration for login attempts
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from core.config import settings
from core.logger import logger
from core.retry_engine import AUTH_POLICY, retry_engine
from core.telemetry import telemetry

if TYPE_CHECKING:
    from browser.browser_manager import BrowserManager


class AuthGuard:
    """
    Guards all MCP tool executions behind a valid browser session.

    Call ``await auth_guard.ensure_login(browser_manager)`` before each tool
    invocation from the PlaywrightWrapper.
    """

    def __init__(self) -> None:
        self._last_login_ts: float = 0.0
        self._login_in_progress: bool = False

    # ── Public API ─────────────────────────────────────────────────────────────

    async def ensure_login(self, browser_manager: "BrowserManager") -> None:
        """
        Ensure the browser has an active, authenticated session.

        Flow:
          1. Check if browser / context / page are alive.
          2. Check session TTL.
          3. Validate session via cookie presence.
          4. If any check fails → perform login (with retries).
        """
        async with telemetry.span("auth_guard.ensure_login"):
            try:
                if not await self._is_session_valid(browser_manager):
                    logger.info("auth_session_invalid_triggering_login")
                    await retry_engine.execute(
                        self._perform_login,
                        AUTH_POLICY,
                        browser_manager,
                    )
                else:
                    telemetry.record_auth_event("session_valid")
                    logger.debug("auth_session_valid")
            except Exception as exc:
                telemetry.record_auth_event("failure")
                logger.error("auth_guard_failure", error=str(exc))
                raise

    # ── Session validation ─────────────────────────────────────────────────────

    async def _is_session_valid(self, browser_manager: "BrowserManager") -> bool:
        """Return True only if the browser is alive AND session hasn't expired."""
        # 1. Browser alive?
        if not browser_manager.is_alive():
            logger.warning("auth_browser_not_alive")
            return False

        # 2. Session TTL expired?
        if self._session_expired():
            logger.info("auth_session_ttl_expired")
            return False

        # 3. Cookie check (best-effort — only when login_url is configured)
        if settings.login_url:
            try:
                cookies = await browser_manager.get_cookies()
                if not self._has_auth_cookies(cookies):
                    logger.info("auth_cookies_absent_or_invalid")
                    return False
            except Exception as exc:  # noqa: BLE001
                logger.warning("auth_cookie_check_failed", error=str(exc))
                return False

        return True

    def _session_expired(self) -> bool:
        if self._last_login_ts == 0.0:
            return True
        return (time.monotonic() - self._last_login_ts) > settings.session_ttl

    @staticmethod
    def _has_auth_cookies(cookies: list[dict]) -> bool:
        """
        Heuristic: any cookie whose name contains common session identifiers.
        Override this method for application-specific logic.
        """
        AUTH_NAMES = {"session", "auth", "token", "sid", "logged_in", "access"}
        for c in cookies:
            name = c.get("name", "").lower()
            if any(a in name for a in AUTH_NAMES):
                return True
        return False

    # ── Login execution ────────────────────────────────────────────────────────

    async def _perform_login(self, browser_manager: "BrowserManager") -> None:
        """
        Execute the login flow.
        Requires LOGIN_URL, SCHOOLOGY_EMAIL, SCHOOLOGY_PASSWORD in env.
        """
        if self._login_in_progress:
            logger.warning("auth_login_already_in_progress")
            return

        self._login_in_progress = True
        try:
            async with telemetry.span("auth_guard.perform_login"):
                logger.info("auth_login_start", url=settings.login_url)

                # Ensure browser is healthy before attempting login
                await browser_manager.ensure_ready()

                page = await browser_manager.get_page()

                if not settings.login_url:
                    logger.warning("auth_no_login_url_configured")
                    telemetry.record_auth_event("login")
                    self._last_login_ts = time.monotonic()
                    return

                # Navigate to login page
                await page.goto(settings.login_url, wait_until="networkidle", timeout=30_000)

                # Fill credentials
                email = settings.schoology_email or ""
                password = settings.schoology_password or ""

                if not email or not password:
                    raise RuntimeError("SCHOOLOGY_EMAIL / SCHOOLOGY_PASSWORD not set in env")

                # Try common login form selectors (adapt as needed)
                await _fill_login_form(page, email, password)

                telemetry.record_auth_event("login")
                self._last_login_ts = time.monotonic()
                logger.info("auth_login_success")

        except Exception as exc:
            telemetry.record_auth_event("failure")
            logger.error("auth_login_failed", error=str(exc))
            raise
        finally:
            self._login_in_progress = False

    def invalidate(self) -> None:
        """Force next call to ensure_login() to re-authenticate."""
        self._last_login_ts = 0.0
        logger.info("auth_session_invalidated")


# ── Login form helper ──────────────────────────────────────────────────────────

async def _fill_login_form(page: "Page", email: str, password: str) -> None:  # type: ignore[name-defined]
    """
    Generic login form filler.  Tries common CSS selectors; extend as needed.
    """
    EMAIL_SELECTORS = [
        'input[type="email"]',
        'input[name="email"]',
        'input[name="username"]',
        'input[id="email"]',
        'input[id="username"]',
    ]
    PASSWORD_SELECTORS = [
        'input[type="password"]',
        'input[name="password"]',
        'input[id="password"]',
    ]
    SUBMIT_SELECTORS = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Log in")',
        'button:has-text("Sign in")',
        'button:has-text("Login")',
    ]

    async def try_fill(selectors: list[str], value: str, label: str) -> None:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=2_000):
                    await loc.fill(value)
                    logger.debug("auth_filled_field", label=label, selector=sel)
                    return
            except Exception:  # noqa: BLE001
                continue
        raise RuntimeError(f"Could not find {label} field. Tried: {selectors}")

    await try_fill(EMAIL_SELECTORS, email, "email")
    await try_fill(PASSWORD_SELECTORS, password, "password")

    for sel in SUBMIT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=2_000):
                await loc.click()
                logger.debug("auth_clicked_submit", selector=sel)
                await page.wait_for_load_state("networkidle", timeout=15_000)
                return
        except Exception:  # noqa: BLE001
            continue

    raise RuntimeError(f"Could not find submit button. Tried: {SUBMIT_SELECTORS}")


# Singleton
auth_guard = AuthGuard()
