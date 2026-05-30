"""
core/config.py — Environment-driven configuration for the Playwright MCP Wrapper Server.
All secrets and tunables are loaded from environment variables. No hardcoded values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Config:
    # ── Retry / Timeout ──────────────────────────────────────────────────────
    retry_count: int = field(default_factory=lambda: int(os.getenv("HARNESS_RETRY_COUNT", "3")))
    retry_delay: float = field(default_factory=lambda: float(os.getenv("HARNESS_RETRY_DELAY", "1.5")))
    timeout: float = field(default_factory=lambda: float(os.getenv("HARNESS_TIMEOUT", "30.0")))

    # ── OpenTelemetry / Azure App Insights ───────────────────────────────────
    otel_enabled: bool = field(default_factory=lambda: os.getenv("OTEL_ENABLED", "false").lower() == "true")
    appinsights_conn_str: Optional[str] = field(default_factory=lambda: os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"))
    otel_service_name: str = field(default_factory=lambda: os.getenv("OTEL_SERVICE_NAME", "playwright-mcp-wrapper"))

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    # ── Playwright MCP subprocess ─────────────────────────────────────────────
    playwright_mcp_command: str = field(
        default_factory=lambda: os.getenv("PLAYWRIGHT_MCP_COMMAND", "npx")
    )
    playwright_mcp_args: list[str] = field(
        default_factory=lambda: os.getenv(
            "PLAYWRIGHT_MCP_ARGS", "@playwright/mcp@latest"
        ).split()
    )
    playwright_mcp_port: int = field(
        default_factory=lambda: int(os.getenv("PLAYWRIGHT_MCP_PORT", "3001"))
    )
    playwright_headless: bool = field(
        default_factory=lambda: os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
    )

    # ── Authentication (e.g. Schoology) ───────────────────────────────────────
    schoology_email: Optional[str] = field(default_factory=lambda: os.getenv("SCHOOLOGY_EMAIL"))
    schoology_password: Optional[str] = field(default_factory=lambda: os.getenv("SCHOOLOGY_PASSWORD"))
    login_url: Optional[str] = field(default_factory=lambda: os.getenv("LOGIN_URL"))
    session_ttl: int = field(
        default_factory=lambda: int(os.getenv("SESSION_TTL_SECONDS", "3600"))
    )

    # ── Server ────────────────────────────────────────────────────────────────
    server_host: str = field(default_factory=lambda: os.getenv("SERVER_HOST", "0.0.0.0"))
    server_port: int = field(default_factory=lambda: int(os.getenv("SERVER_PORT", "8000")))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")


# Singleton — import and use `settings` everywhere
settings = Config()
