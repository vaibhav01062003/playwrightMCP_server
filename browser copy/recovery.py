"""
browser/recovery.py — Browser crash recovery helpers.

Called by BrowserManager and PlaywrightWrapper when a browser-level failure
is detected. Provides an async recovery pipeline with telemetry hooks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logger import logger
from core.telemetry import telemetry

if TYPE_CHECKING:
    from browser.browser_manager import BrowserManager


async def recover_browser(browser_manager: "BrowserManager") -> bool:
    """
    Attempt to recover the browser after a crash or unexpected close.

    Returns True on success, False if recovery failed.
    """
    logger.warning("browser_recovery_start")
    telemetry.record_browser_crash()

    async with telemetry.span("browser.recovery"):
        try:
            await browser_manager.ensure_ready()
            logger.info("browser_recovery_success")
            return True
        except Exception as exc:
            logger.error("browser_recovery_failed", error=str(exc))
            return False


async def safe_navigate(browser_manager: "BrowserManager", url: str, *, timeout_ms: int = 30_000) -> bool:
    """
    Navigate to *url*, recovering the browser if necessary.

    Returns True if navigation succeeded, False otherwise.
    """
    try:
        page = await browser_manager.get_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return True
    except Exception as exc:
        logger.warning("safe_navigate_failed_attempting_recovery", url=url, error=str(exc))
        recovered = await recover_browser(browser_manager)
        if not recovered:
            return False
        try:
            page = await browser_manager.get_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return True
        except Exception as exc2:
            logger.error("safe_navigate_failed_after_recovery", url=url, error=str(exc2))
            return False
