"""
browser/login_handler.py — Reusable login flow helpers for common SSO patterns.

Provides higher-level helpers on top of core/auth_guard.py's generic form filler.
Extend this module with site-specific login strategies (OAuth, SAML, MFA, etc.).
"""

from __future__ import annotations

from typing import Optional

from playwright.async_api import Page

from core.logger import logger


async def schoology_login(page: Page, email: str, password: str) -> None:
    """
    Perform Schoology-specific login.
    Navigates to login page, fills credentials, handles potential MFA prompt.
    """
    logger.info("schoology_login_start")
    await page.goto("https://app.schoology.com/login", wait_until="networkidle", timeout=30_000)

    # Email field
    await page.fill('input[name="mail"]', email)
    # Password field
    await page.fill('input[name="pass"]', password)
    # Submit
    await page.click('input[id="edit-submit"]')
    await page.wait_for_load_state("networkidle", timeout=20_000)

    # Detect login failure
    error_loc = page.locator(".messages.error, .error-message")
    if await error_loc.count() > 0:
        err_text = await error_loc.first.text_content()
        raise RuntimeError(f"Schoology login failed: {err_text}")

    logger.info("schoology_login_success")


async def generic_oauth_login(
    page: Page,
    *,
    login_url: str,
    username: str,
    password: str,
    username_selector: str = 'input[type="email"]',
    password_selector: str = 'input[type="password"]',
    submit_selector: str = 'button[type="submit"]',
    success_url_fragment: Optional[str] = None,
) -> None:
    """
    Generic OAuth / form-based login.

    Parameters
    ----------
    success_url_fragment:
        Optional URL substring to assert after login (e.g. '/dashboard').
    """
    logger.info("oauth_login_start", url=login_url)
    await page.goto(login_url, wait_until="domcontentloaded", timeout=30_000)
    await page.fill(username_selector, username)
    await page.fill(password_selector, password)
    await page.click(submit_selector)
    await page.wait_for_load_state("networkidle", timeout=20_000)

    if success_url_fragment and success_url_fragment not in page.url:
        raise RuntimeError(
            f"Login redirect check failed. Expected URL to contain '{success_url_fragment}', "
            f"got: {page.url}"
        )

    logger.info("oauth_login_success", final_url=page.url)
