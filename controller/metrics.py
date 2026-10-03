"""Pull a MetricSample for one service out of Prometheus.

This module provides a simple HTTP client for querying Prometheus metrics
and converting them into MetricSample objects used by the decision logic.
"""

from __future__ import annotations

import math
import time

import httpx

from decision import MetricSample


class PrometheusClient:
    """HTTP client for querying Prometheus metrics.

    This client queries Prometheus for the metrics needed to make rollback
    decisions: total requests, error count, and p95 latency for a given
    service over a time window.

    Attributes:
        base_url: Base URL of the Prometheus server (e.g., "http://prometheus:9090")
        timeout: HTTP request timeout in seconds
    """

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        """Initialize the Prometheus client.

        Args:
            base_url: Base URL of the Prometheus server
            timeout: HTTP request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def sample(self, service: str, window_seconds: int) -> MetricSample:
        """Pull metrics for a service over a time window.

        Queries Prometheus for:
        - Total request count over the window
        - Error count (HTTP 500) over the window
        - p95 latency over the window

        Args:
            service: Service name ("stable" or "canary")
            window_seconds: Size of the observation window in seconds

        Returns:
            MetricSample containing request count, error count, and p95 latency
        """
        window = f"{int(window_seconds)}s"
        end = time.time()
        start = end - window_seconds
        requests = self._scalar(
            f'sum(increase(http_requests_total{{service="{service}"}}[{window}]))'
        )
        errors = self._scalar(
            f'sum(increase(http_requests_total{{service="{service}",status="500"}}[{window}]))'
        )
        p95_s = self._scalar(
            "histogram_quantile(0.95, "
            "sum by (le) ("
            f'rate(http_request_duration_seconds_bucket{{service="{service}"}}[{window}])'
            "))"
        )
        n = max(0, int(round(requests)))
        e = max(0, min(n, int(round(errors))))
        p95_ms = 0.0 if math.isnan(p95_s) else p95_s * 1000.0
        return MetricSample(
            service=service,
            window_start=start,
            window_end=end,
            request_count=n,
            error_count=e,
            p95_latency_ms=p95_ms,
        )

    def _scalar(self, expr: str) -> float:
        """Execute a Prometheus query and return a scalar value.

        Sends a query to Prometheus and extracts the scalar value from the
        response. Handles various error cases gracefully by returning 0.0.

        Args:
            expr: Prometheus query expression

        Returns:
            Scalar value from the query, or 0.0 on any error
        """
        url = f"{self.base_url}/api/v1/query"
        response = httpx.get(url, params={"query": expr}, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            return 0.0
        result = payload.get("data", {}).get("result") or []
        if not result:
            return 0.0
        raw = result[0]["value"][1]
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 0.0
        if math.isnan(value) or math.isinf(value):
            return 0.0
        return value
