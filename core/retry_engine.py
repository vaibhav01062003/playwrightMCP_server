"""
core/retry_engine.py — Async retry system with exponential backoff.

Supports:
  - Configurable attempt count and base delay (env-driven via core/config.py)
  - Exponential backoff with optional jitter
  - Timeout protection per-attempt and globally
  - Transient-error classification helpers
  - Named retry policies (browser, mcp, network, auth)
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Type, TypeVar

from core.config import settings
from core.logger import logger

T = TypeVar("T")

# ── Transient error categories ────────────────────────────────────────────────

_TRANSIENT_EXCEPTIONS: tuple[Type[Exception], ...] = (
    ConnectionResetError,
    ConnectionRefusedError,
    TimeoutError,
    asyncio.TimeoutError,
    OSError,
)

_TRANSIENT_SUBSTRINGS: tuple[str, ...] = (
    "timeout",
    "connection reset",
    "connection refused",
    "broken pipe",
    "eof",
    "browser closed",
    "target closed",
    "session closed",
    "page crashed",
    "net::err",
    "protocol error",
)


def is_transient(exc: Exception) -> bool:
    """Return True if the exception looks like a recoverable transient failure."""
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return True
    msg = str(exc).lower()
    return any(sub in msg for sub in _TRANSIENT_SUBSTRINGS)


# ── Policy dataclass ──────────────────────────────────────────────────────────

@dataclass
class RetryPolicy:
    """Encapsulates retry behaviour for a specific operation class."""
    name: str
    max_attempts: int = field(default_factory=lambda: settings.retry_count)
    base_delay: float = field(default_factory=lambda: settings.retry_delay)
    max_delay: float = 30.0
    backoff_factor: float = 2.0
    jitter: bool = True
    # Per-attempt timeout (seconds). None → use global HARNESS_TIMEOUT.
    attempt_timeout: Optional[float] = field(default_factory=lambda: settings.timeout)
    # Only retry when these exception types occur. Empty = retry on all transient.
    retry_on: tuple[Type[Exception], ...] = ()

    def delay_for(self, attempt: int) -> float:
        """Compute sleep seconds for attempt N (0-indexed)."""
        delay = min(self.base_delay * (self.backoff_factor ** attempt), self.max_delay)
        if self.jitter:
            delay *= (0.5 + random.random() * 0.5)
        return delay

    def should_retry(self, exc: Exception) -> bool:
        if self.retry_on:
            return isinstance(exc, self.retry_on) or is_transient(exc)
        return is_transient(exc)


# ── Built-in policies ─────────────────────────────────────────────────────────

BROWSER_POLICY = RetryPolicy(name="browser", max_attempts=4, base_delay=2.0)
MCP_POLICY = RetryPolicy(name="mcp", max_attempts=3, base_delay=1.0)
NETWORK_POLICY = RetryPolicy(name="network", max_attempts=5, base_delay=0.5)
AUTH_POLICY = RetryPolicy(name="auth", max_attempts=3, base_delay=3.0, jitter=False)
DEFAULT_POLICY = RetryPolicy(name="default")


# ── Engine ────────────────────────────────────────────────────────────────────

class RetryEngine:
    """
    Async retry engine.

    Usage::

        engine = RetryEngine()
        result = await engine.execute(my_async_fn, MCP_POLICY, arg1, kwarg=val)
    """

    async def execute(
        self,
        fn: Callable[..., Awaitable[T]],
        policy: RetryPolicy = DEFAULT_POLICY,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """
        Execute *fn* with *args*/*kwargs* under the given *policy*.
        Retries on transient errors; raises the last exception after exhaustion.
        """
        last_exc: Optional[Exception] = None

        for attempt in range(policy.max_attempts):
            try:
                log = logger.bind(
                    retry_policy=policy.name,
                    attempt=attempt + 1,
                    max_attempts=policy.max_attempts,
                )
                if attempt > 0:
                    log.info("retry_attempt")

                if policy.attempt_timeout:
                    result: T = await asyncio.wait_for(
                        fn(*args, **kwargs),
                        timeout=policy.attempt_timeout,
                    )
                else:
                    result = await fn(*args, **kwargs)

                if attempt > 0:
                    log.info("retry_succeeded")
                return result

            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                log = logger.bind(
                    retry_policy=policy.name,
                    attempt=attempt + 1,
                    max_attempts=policy.max_attempts,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

                if not policy.should_retry(exc):
                    log.warning("non_transient_error_no_retry")
                    raise

                if attempt < policy.max_attempts - 1:
                    delay = policy.delay_for(attempt)
                    log.warning("transient_error_retrying", delay=round(delay, 2))
                    await asyncio.sleep(delay)
                else:
                    log.error("retry_exhausted")

        raise last_exc  # type: ignore[misc]


# Singleton
retry_engine = RetryEngine()
