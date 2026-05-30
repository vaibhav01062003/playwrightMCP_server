"""
tests/test_retry.py — Tests for core/retry_engine.py
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from core.retry_engine import (
    RetryEngine,
    RetryPolicy,
    is_transient,
    BROWSER_POLICY,
    MCP_POLICY,
    AUTH_POLICY,
)


# ── is_transient ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("exc,expected", [
    (ConnectionResetError("reset"), True),
    (ConnectionRefusedError("refused"), True),
    (TimeoutError("timeout"), True),
    (asyncio.TimeoutError(), True),
    (RuntimeError("browser closed"), True),
    (RuntimeError("target closed"), True),
    (RuntimeError("page crashed"), True),
    (ValueError("user error"), False),
    (RuntimeError("some random error"), False),
])
def test_is_transient(exc, expected):
    assert is_transient(exc) == expected


# ── RetryPolicy.delay_for ─────────────────────────────────────────────────────

def test_delay_increases_with_attempts():
    policy = RetryPolicy(name="test", base_delay=1.0, jitter=False, backoff_factor=2.0)
    delays = [policy.delay_for(i) for i in range(4)]
    # Each delay should be >= previous (no jitter)
    for i in range(1, len(delays)):
        assert delays[i] >= delays[i - 1]


def test_delay_capped_at_max():
    policy = RetryPolicy(name="test", base_delay=1.0, max_delay=5.0, jitter=False, backoff_factor=10.0)
    assert policy.delay_for(10) <= 5.0


# ── RetryEngine ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_success_on_first_attempt():
    engine = RetryEngine()
    mock_fn = AsyncMock(return_value="ok")
    result = await engine.execute(mock_fn, MCP_POLICY)
    assert result == "ok"
    mock_fn.assert_awaited_once()


@pytest.mark.asyncio
async def test_retries_on_transient_error():
    engine = RetryEngine()
    policy = RetryPolicy(name="test", max_attempts=3, base_delay=0.01, attempt_timeout=None)
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionResetError("transient")
        return "recovered"

    result = await engine.execute(flaky, policy)
    assert result == "recovered"
    assert call_count == 3


@pytest.mark.asyncio
async def test_raises_after_exhaustion():
    engine = RetryEngine()
    policy = RetryPolicy(name="test", max_attempts=2, base_delay=0.01, attempt_timeout=None)

    async def always_fails():
        raise ConnectionResetError("always transient")

    with pytest.raises(ConnectionResetError):
        await engine.execute(always_fails, policy)


@pytest.mark.asyncio
async def test_no_retry_on_non_transient_error():
    engine = RetryEngine()
    policy = RetryPolicy(name="test", max_attempts=5, base_delay=0.01, attempt_timeout=None)
    call_count = 0

    async def non_transient():
        nonlocal call_count
        call_count += 1
        raise ValueError("not transient")

    with pytest.raises(ValueError):
        await engine.execute(non_transient, policy)

    assert call_count == 1  # Should NOT have retried


@pytest.mark.asyncio
async def test_timeout_protection():
    engine = RetryEngine()
    policy = RetryPolicy(name="test", max_attempts=1, attempt_timeout=0.1)

    async def slow():
        await asyncio.sleep(10)
        return "too late"

    with pytest.raises(asyncio.TimeoutError):
        await engine.execute(slow, policy)
