"""
core/telemetry.py — OpenTelemetry instrumentation for the Playwright MCP Wrapper Server.

Provides:
  - Trace provider setup (Azure Application Insights compatible)
  - Structured span helpers for: Foundry requests, MCP tool execution,
    Playwright execution, login flow, retry attempts
  - Metric counters for tool calls, errors, auth events, retries
  - Context-manager and decorator wrappers
"""

from __future__ import annotations

import contextlib
import functools
import time
from collections.abc import AsyncGenerator
from typing import Any, Callable, Optional, TypeVar

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource

from core.config import settings
from core.logger import logger

F = TypeVar("F", bound=Callable[..., Any])

# ── Resource ──────────────────────────────────────────────────────────────────

_RESOURCE = Resource.create(
    {
        "service.name": settings.otel_service_name,
        "service.version": "1.0.0",
        "deployment.environment": "production" if not settings.debug else "development",
    }
)

# ── Trace provider ────────────────────────────────────────────────────────────


def _build_trace_provider() -> TracerProvider:
    provider = TracerProvider(resource=_RESOURCE)

    if settings.otel_enabled and settings.appinsights_conn_str:
        try:
            from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

            exporter = AzureMonitorTraceExporter(
                connection_string=settings.appinsights_conn_str
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("otel_azure_exporter_configured")
        except ImportError:
            logger.warning(
                "azure_monitor_exporter_not_installed",
                hint="pip install azure-monitor-opentelemetry-exporter",
            )
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        if settings.debug:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    return provider


def _build_meter_provider() -> MeterProvider:
    reader = PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=60_000)
    return MeterProvider(resource=_RESOURCE, metric_readers=[reader])


# ── Global setup ──────────────────────────────────────────────────────────────

class Telemetry:
    """Centralised telemetry facade."""

    def __init__(self) -> None:
        self._tracer_provider = _build_trace_provider()
        trace.set_tracer_provider(self._tracer_provider)

        self._meter_provider = _build_meter_provider()
        metrics.set_meter_provider(self._meter_provider)

        self._tracer = trace.get_tracer(settings.otel_service_name)
        self._meter = metrics.get_meter(settings.otel_service_name)

        # Metrics
        self.tool_calls = self._meter.create_counter(
            "mcp.tool_calls", description="Total MCP tool invocations"
        )
        self.tool_errors = self._meter.create_counter(
            "mcp.tool_errors", description="Total MCP tool failures"
        )
        self.auth_events = self._meter.create_counter(
            "auth.events", description="Auth guard events (login, relogin, failure)"
        )
        self.retry_attempts = self._meter.create_counter(
            "retry.attempts", description="Retry attempts across all policies"
        )
        self.browser_crashes = self._meter.create_counter(
            "browser.crashes", description="Browser crash / recovery events"
        )
        self.latency = self._meter.create_histogram(
            "mcp.tool_latency_ms", description="Tool execution latency in ms"
        )

    # ── Span helpers ──────────────────────────────────────────────────────────

    @contextlib.asynccontextmanager
    async def span(
        self,
        name: str,
        attributes: Optional[dict[str, Any]] = None,
    ) -> AsyncGenerator[trace.Span, None]:
        """Async context manager that wraps a block in an OTEL span."""
        with self._tracer.start_as_current_span(name) as sp:
            if attributes:
                for k, v in attributes.items():
                    sp.set_attribute(k, str(v))
            t0 = time.monotonic()
            try:
                yield sp
            except Exception as exc:
                sp.record_exception(exc)
                sp.set_status(trace.StatusCode.ERROR, str(exc))
                raise
            finally:
                elapsed_ms = (time.monotonic() - t0) * 1000
                sp.set_attribute("duration_ms", round(elapsed_ms, 2))

    def record_tool_call(
        self,
        tool_name: str,
        success: bool,
        latency_ms: float,
        extra: Optional[dict[str, str]] = None,
    ) -> None:
        attrs = {"tool": tool_name, **(extra or {})}
        self.tool_calls.add(1, attrs)
        self.latency.record(latency_ms, attrs)
        if not success:
            self.tool_errors.add(1, attrs)

    def record_auth_event(self, event: str) -> None:
        """event: 'login', 'relogin', 'failure', 'session_valid'"""
        self.auth_events.add(1, {"event": event})

    def record_retry(self, policy: str, attempt: int) -> None:
        self.retry_attempts.add(1, {"policy": policy, "attempt": str(attempt)})

    def record_browser_crash(self) -> None:
        self.browser_crashes.add(1)

    def shutdown(self) -> None:
        self._tracer_provider.shutdown()
        self._meter_provider.shutdown()
        logger.info("telemetry_shutdown")


# Singleton
telemetry = Telemetry()
