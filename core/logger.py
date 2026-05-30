"""
core/logger.py — Structured JSON logging for the Playwright MCP Wrapper Server.
Outputs machine-readable logs compatible with Azure Monitor / Log Analytics.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from core.config import settings


def _configure_stdlib_logging() -> None:
    """Wire stdlib logging into structlog so third-party libs are captured too."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, settings.log_level, logging.INFO),
    )


def build_logger() -> structlog.BoundLogger:
    _configure_stdlib_logging()

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.debug:
        # Human-readable in dev / local
        renderer: Any = structlog.dev.ConsoleRenderer(colors=True)
    else:
        # JSON in production (Azure / Docker)
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level, logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    return structlog.get_logger("playwright-mcp-wrapper")


logger = build_logger()
