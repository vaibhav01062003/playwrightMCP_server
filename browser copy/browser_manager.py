"""
browser/browser_manager.py — Playwright browser lifecycle manager.

Responsibilities:
  - Launch / reuse a single Playwright browser instance
  - Manage browser context and page
  - Crash detection and auto-recovery
  - Screenshot normalization (prevent fullPage + element conflicts)
  - Graceful shutdown
"""

from __future__ import annotations

import asyncio
from typing import Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from core.config import settings
from core.logger import logger
from core.telemetry import telemetry


class BrowserManager:
    """Single-instance Playwright browser lifecycle owner."""

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._lock = asyncio.Lock()

    # ── Public API ─────────────────────────────────────────────────────────────

    def is_alive(self) -> bool:
        """Return True if the browser and page are in a usable state."""
        if self._browser is None or not self._browser.is_connected():
            return False
        if self._page is None or self._page.is_closed():
            return False
        return True

    async def ensure_ready(self) -> None:
        """Ensure browser is running; launch or recover if needed."""
        async with self._lock:
            if not self.is_alive():
                await self._launch_or_recover()

    async def get_page(self) -> Page:
        """Return the active page, recovering the browser if necessary."""
        await self.ensure_ready()
        assert self._page is not None
        return self._page

    async def get_cookies(self) -> list[dict]:
        """Return current browser context cookies."""
        if self._context is None:
            return []
        return await self._context.cookies()

    async def new_page(self) -> Page:
        """Open a new tab in the current context."""
        await self.ensure_ready()
        assert self._context is not None
        page = await self._context.new_page()
        self._page = page
        self._attach_crash_listener(page)
        return page

    async def take_screenshot(
        self,
        *,
        full_page: bool = False,
        selector: Optional[str] = None,
        path: Optional[str] = None,
    ) -> bytes:
        """
        Safe screenshot helper.

        Playwright does NOT allow fullPage=True AND element screenshots together.
        This method normalises invalid combos automatically.
        """
        page = await self.get_page()
        if selector and full_page:
            logger.warning(
                "screenshot_conflict_normalised",
                msg="fullPage=True is incompatible with element selector; using element screenshot only.",
            )
            full_page = False

        if selector:
            element = page.locator(selector).first
            return await element.screenshot(path=path, timeout=settings.timeout * 1000)

        return await page.screenshot(full_page=full_page, path=path, timeout=settings.timeout * 1000)

    async def shutdown(self) -> None:
        """Gracefully close the browser and Playwright driver."""
        async with self._lock:
            await self._close_all()
        logger.info("browser_shutdown_complete")

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _launch_or_recover(self) -> None:
        """(Re)launch the browser, closing stale resources first."""
        logger.info("browser_launching")
        await self._close_all()
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=settings.playwright_headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 900},
                java_script_enabled=True,
                ignore_https_errors=False,
            )
            self._page = await self._context.new_page()
            self._attach_crash_listener(self._page)
            logger.info("browser_launched")
        except Exception as exc:
            logger.error("browser_launch_failed", error=str(exc))
            telemetry.record_browser_crash()
            raise

    def _attach_crash_listener(self, page: Page) -> None:
        page.on("crash", self._on_crash)
        page.on("close", self._on_page_close)

    async def _on_crash(self, page: Page) -> None:  # type: ignore[override]
        logger.error("browser_page_crashed")
        telemetry.record_browser_crash()
        # Trigger recovery on next call
        self._page = None

    async def _on_page_close(self, page: Page) -> None:  # type: ignore[override]
        logger.warning("browser_page_closed")
        if self._page is page:
            self._page = None

    async def _close_all(self) -> None:
        for obj, label in [
            (self._page, "page"),
            (self._context, "context"),
            (self._browser, "browser"),
        ]:
            if obj is not None:
                try:
                    await obj.close()
                    logger.debug("browser_closed_resource", resource=label)
                except Exception:  # noqa: BLE001
                    pass

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass

        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None


# Singleton
browser_manager = BrowserManager()
