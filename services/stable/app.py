"""The HTTP service behind the canary rollout.

Stable and canary run this exact file. The only differences between a healthy
release and a broken one are environment variables set at container start:

    SERVICE_NAME           Label on /metrics ("stable" or "canary").
    ERROR_RATE             Probability a /work request returns HTTP 500.
    LATENCY_MS_MEAN        Mean injected delay before the response.
    LATENCY_MS_JITTER      Std-dev of that delay (gaussian).
    DEGRADE_ENDPOINT       If set, only this path is faulty (partial failure).
    DEGRADE_RAMP_SECONDS   If set, the fault grows 0 → configured over this
                           many seconds from process start (slow onset).

GET /health is liveness only. The controller never uses it to decide a rollout.
GET /work is the "real" traffic the load generator and nginx send.
GET /metrics is scraped by Prometheus.
"""

from __future__ import annotations

import asyncio
import os
import random
import time

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

SERVICE_NAME = os.environ.get("SERVICE_NAME", "unknown")
ERROR_RATE = float(os.environ.get("ERROR_RATE", "0.0"))
LATENCY_MS_MEAN = float(os.environ.get("LATENCY_MS_MEAN", "40"))
LATENCY_MS_JITTER = float(os.environ.get("LATENCY_MS_JITTER", "10"))
DEGRADE_ENDPOINT = os.environ.get("DEGRADE_ENDPOINT", "").strip()
_RAMP_RAW = os.environ.get("DEGRADE_RAMP_SECONDS", "").strip()
DEGRADE_RAMP_SECONDS = float(_RAMP_RAW) if _RAMP_RAW else None

# Healthy baseline used when a request is *not* on the degraded path, and as
# the starting point of a slow-onset ramp.
HEALTHY_MEAN_MS = 40.0
HEALTHY_JITTER_MS = 10.0

STARTED_AT = time.monotonic()

# Buckets sit around the healthy 40ms mode, then stretch out to the 400ms
# latency-fault scenario so histogram_quantile(0.95, ...) has somewhere to land.
LATENCY_BUCKETS = (
    0.005,
    0.01,
    0.02,
    0.03,
    0.04,
    0.05,
    0.075,
    0.1,
    0.15,
    0.2,
    0.3,
    0.4,
    0.5,
    0.75,
    1.0,
    2.5,
)

REQUESTS = Counter(
    "http_requests_total",
    "Work requests by HTTP status",
    ["service", "status"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "Work request latency in seconds",
    ["service"],
    buckets=LATENCY_BUCKETS,
)

app = FastAPI(title=f"perch-{SERVICE_NAME}")


def fault_scale() -> float:
    """Calculate how fully 'on' the configured fault is, in [0, 1].

    When DEGRADE_RAMP_SECONDS is set, the fault ramps up gradually from 0 to 1
    over that time period. This simulates slow-onset faults.

    Returns:
        Float between 0.0 (fault not active) and 1.0 (fault fully active)
    """
    if not DEGRADE_RAMP_SECONDS or DEGRADE_RAMP_SECONDS <= 0:
        return 1.0
    elapsed = time.monotonic() - STARTED_AT
    return min(1.0, max(0.0, elapsed / DEGRADE_RAMP_SECONDS))


def path_is_faulted(path: str) -> bool:
    """Check if a specific path should have the fault applied.

    When DEGRADE_ENDPOINT is set, only that specific path experiences the fault.
    This enables partial failure scenarios where only some endpoints are broken.

    Args:
        path: The request path to check

    Returns:
        True if the path should have the fault applied, False otherwise
    """
    if not DEGRADE_ENDPOINT:
        return True
    want = DEGRADE_ENDPOINT.rstrip("/") or "/"
    got = path.rstrip("/") or "/"
    return got == want


def behaviour_for(path: str) -> tuple[float, float, float]:
    """Return (delay_mean_ms, delay_jitter_ms, error_probability) for this request."""
    if not path_is_faulted(path):
        return HEALTHY_MEAN_MS, HEALTHY_JITTER_MS, 0.0
    scale = fault_scale()
    delay_mean = HEALTHY_MEAN_MS + (LATENCY_MS_MEAN - HEALTHY_MEAN_MS) * scale
    delay_jitter = HEALTHY_JITTER_MS + (LATENCY_MS_JITTER - HEALTHY_JITTER_MS) * scale
    return delay_mean, delay_jitter, ERROR_RATE * scale


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/work")
@app.get("/work/{name}")
async def work(request: Request, name: str | None = None) -> Response:
    path = request.url.path
    delay_mean, delay_jitter, error_p = behaviour_for(path)

    delay_ms = max(0.0, random.gauss(delay_mean, delay_jitter))
    t0 = time.perf_counter()
    await asyncio.sleep(delay_ms / 1000.0)
    observed = time.perf_counter() - t0

    failed = random.random() < error_p
    status = 500 if failed else 200
    REQUESTS.labels(service=SERVICE_NAME, status=str(status)).inc()
    LATENCY.labels(service=SERVICE_NAME).observe(observed)

    body = {"ok": not failed, "service": SERVICE_NAME, "path": path}
    if failed:
        return JSONResponse(body, status_code=500)
    return JSONResponse(body)


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
