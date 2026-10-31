"""Pull a service's numbers out of Prometheus for the decision logic.

Every query in one poll should pass the same `at` timestamp: Prometheus then evaluates them all
against the same scrapes, instead of a new scrape landing between two queries and skewing them.

Two views of the same counters:
- sample(): increases over the last window (what Tier 1 and the naive rule judge);
- counters(): cumulative totals right now, which Tier 2 differences between polls so that each
  request is fed to its sequential tests exactly once.
"""

from __future__ import annotations

import math
import time

import httpx
from decision import CounterSnapshot, MetricSample


class PrometheusClient:
    """Minimal HTTP client for the Prometheus instant-query API."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def sample(self, service: str, window_seconds: int, at: float | None = None) -> MetricSample:
        """Requests, errors (HTTP 500), p95 latency and the latency histogram over the window."""
        window = f"{int(window_seconds)}s"
        end = time.time() if at is None else at
        start = end - window_seconds
        requests = self._scalar(
            f'sum(increase(http_requests_total{{service="{service}"}}[{window}]))', end
        )
        errors = self._scalar(
            f'sum(increase(http_requests_total{{service="{service}",status="500"}}[{window}]))',
            end,
        )
        p95_s = self._scalar(
            "histogram_quantile(0.95, "
            "sum by (le) ("
            f'rate(http_request_duration_seconds_bucket{{service="{service}"}}[{window}])'
            "))",
            end,
        )
        buckets = self._by_le(
            "sum by (le) ("
            f'increase(http_request_duration_seconds_bucket{{service="{service}"}}[{window}])'
            ")",
            end,
        )
        n = max(0, int(round(requests)))
        e = max(0, min(n, int(round(errors))))
        return MetricSample(
            service=service,
            window_start=start,
            window_end=end,
            request_count=n,
            error_count=e,
            p95_latency_ms=p95_s * 1000.0,
            latency_buckets=buckets,
        )

    def counters(self, service: str, at: float | None = None) -> CounterSnapshot:
        """The service's cumulative request, error and latency-bucket counts, as last scraped."""
        at = time.time() if at is None else at
        requests = self._scalar(f'sum(http_requests_total{{service="{service}"}})', at)
        errors = self._scalar(f'sum(http_requests_total{{service="{service}",status="500"}})', at)
        buckets = self._by_le(
            f'sum by (le) (http_request_duration_seconds_bucket{{service="{service}"}})', at
        )
        return CounterSnapshot(int(round(requests)), int(round(errors)), buckets)

    def _vector(self, expr: str, at: float) -> list[dict]:
        response = httpx.get(
            f"{self.base_url}/api/v1/query",
            params={"query": expr, "time": f"{at:.3f}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            return []
        return payload.get("data", {}).get("result") or []

    def _scalar(self, expr: str, at: float) -> float:
        """First series' value, or 0.0 when there is none or it is NaN/Inf (e.g. no traffic)."""
        result = self._vector(expr, at)
        if not result:
            return 0.0
        return _finite(result[0]["value"][1])

    def _by_le(self, expr: str, at: float) -> dict[float, int]:
        """A `sum by (le)` histogram as {bucket upper bound in seconds: count}; +Inf is math.inf."""
        buckets = {}
        for series in self._vector(expr, at):
            le = series.get("metric", {}).get("le")
            if le is not None:
                buckets[float(le)] = int(round(_finite(series["value"][1])))
        return buckets


def _finite(raw: object) -> float:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(value) or math.isinf(value) else value
