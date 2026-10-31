"""End-to-end checks against a running stack (`make up` / docker compose up).

If the stack is not up, these tests skip rather than fail, so `make unit`
stays runnable without Docker. The weight tests rewrite the proxy's live
config, so don't run them while a scenario is in progress.
"""

from __future__ import annotations

import os
import time
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from proxy_writer import ProxyWriter

ROOT = Path(__file__).resolve().parents[1]
PROXY_CONF = ROOT / "proxy-conf" / "default.conf"
TEMPLATE = ROOT / "proxy" / "nginx.conf.j2"


def _port(name: str, default: str) -> str:
    """Host port as docker compose resolves it: process env, then .env, then the default."""
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name and value.strip():
                return value.strip()
    return default


STABLE = f"http://127.0.0.1:{_port('STABLE_PORT', '8091')}"
CANARY = f"http://127.0.0.1:{_port('CANARY_PORT', '8092')}"
PROXY = f"http://127.0.0.1:{_port('PROXY_PORT', '8089')}"
PROM = f"http://127.0.0.1:{_port('PROMETHEUS_PORT', '9095')}"


def _reachable(url: str) -> bool:
    try:
        httpx.get(url, timeout=2.0)
        return True
    except (httpx.HTTPError, httpx.RequestError, OSError):
        return False


@pytest.fixture(scope="module")
def stack_up() -> None:
    if not _reachable(f"{STABLE}/health"):
        pytest.skip("stack is not running; start it with `docker compose up -d --build`")


def test_app_py_files_are_byte_identical() -> None:
    stable = (ROOT / "services" / "stable" / "app.py").read_bytes()
    canary = (ROOT / "services" / "canary" / "app.py").read_bytes()
    assert stable == canary


def _assert_work_response(response: httpx.Response, services: set[str]) -> None:
    """A /work answer from the right service. Both versions fail a background share of requests
    (BASE_ERROR_RATE), so a 500 is a valid answer as long as it is the app's own."""
    assert response.status_code in (200, 500)
    body = response.json()
    assert body["service"] in services
    assert body["ok"] is (response.status_code == 200)


def test_direct_health_and_work(stack_up: None) -> None:
    for base, name in ((STABLE, "stable"), (CANARY, "canary")):
        health = httpx.get(f"{base}/health", timeout=5.0)
        assert health.status_code == 200
        _assert_work_response(httpx.get(f"{base}/work", timeout=5.0), {name})


def test_proxy_serves_work(stack_up: None) -> None:
    _assert_work_response(httpx.get(f"{PROXY}/work", timeout=5.0), {"stable", "canary"})


def test_metrics_labelled_by_service(stack_up: None) -> None:
    for base, name in ((STABLE, "stable"), (CANARY, "canary")):
        text = httpx.get(f"{base}/metrics", timeout=5.0).text
        assert "http_requests_total" in text
        assert f'service="{name}"' in text
        assert "http_request_duration_seconds" in text


def test_prometheus_targets_up(stack_up: None) -> None:
    if not _reachable(f"{PROM}/-/ready"):
        pytest.skip("prometheus is not reachable")
    payload = httpx.get(f"{PROM}/api/v1/targets", timeout=5.0).json()
    health = {t["labels"]["job"]: t["health"] for t in payload["data"]["activeTargets"]}
    assert health.get("stable") == "up"
    assert health.get("canary") == "up"


def _wait_for_weights(stable: int, canary: int, timeout: float = 15.0) -> None:
    """Poll until nginx reports (via X-Perch-Weights) that it has loaded these weights."""
    want = f"{stable}/{canary}"
    deadline = time.monotonic() + timeout
    seen = None
    while time.monotonic() < deadline:
        seen = httpx.get(f"{PROXY}/health", timeout=5.0).headers.get("x-perch-weights")
        if seen == want:
            return
        time.sleep(0.5)
    pytest.fail(f"proxy never loaded weights {want}; last saw {seen}")


def _backends(n: int) -> Counter:
    # /health, not /work: unaffected by whatever fault the canary was last deployed with, and it
    # does not feed the request counters the controller decides on.
    return Counter(httpx.get(f"{PROXY}/health", timeout=5.0).json()["service"] for _ in range(n))


@pytest.fixture
def set_weights(stack_up: None) -> Iterator[Callable[[int, int], None]]:
    """Write weights into the proxy's live config exactly as the controller does, then restore.

    Do not run while a scenario is in progress: the controller would overwrite these weights.
    """
    original = PROXY_CONF.read_text(encoding="utf-8")
    writer = ProxyWriter(str(TEMPLATE), str(PROXY_CONF), debounce_seconds=0)

    def _set(stable: int, canary: int) -> None:
        writer.apply(stable, canary, force=True)
        _wait_for_weights(stable, canary)

    yield _set
    PROXY_CONF.write_text(original, encoding="utf-8")


def test_zero_weight_backend_gets_no_traffic(set_weights: Callable[[int, int], None]) -> None:
    set_weights(100, 0)
    assert set(_backends(40)) == {"stable"}
    set_weights(0, 100)
    assert set(_backends(40)) == {"canary"}


def test_changing_weights_shifts_traffic(set_weights: Callable[[int, int], None]) -> None:
    """80/20 should put about a fifth of requests on the canary.

    nginx's weighted round-robin is near-exact within one worker; the band only absorbs requests
    being spread over several workers, each with its own rotation.
    """
    set_weights(80, 20)
    counts = _backends(200)
    assert 0.10 <= counts["canary"] / 200 <= 0.30, counts
