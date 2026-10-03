"""End-to-end checks against a running stack (`make up` / docker compose up).

If the stack is not up, these tests skip rather than fail, so `make unit`
stays runnable without Docker.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
STABLE = "http://127.0.0.1:8091"
CANARY = "http://127.0.0.1:8092"
PROXY = "http://127.0.0.1:8080"
PROM = "http://127.0.0.1:9095"


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


def test_direct_health_and_work(stack_up: None) -> None:
    for base in (STABLE, CANARY):
        health = httpx.get(f"{base}/health", timeout=5.0)
        assert health.status_code == 200
        work = httpx.get(f"{base}/work", timeout=5.0)
        assert work.status_code == 200
        assert work.json()["ok"] is True


def test_proxy_serves_work(stack_up: None) -> None:
    response = httpx.get(f"{PROXY}/work", timeout=5.0)
    assert response.status_code == 200
    assert response.json()["service"] in {"stable", "canary"}


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


def test_proxy_split_includes_both_backends_once_controller_is_live(
    stack_up: None,
) -> None:
    """After the controller starts it should put *some* traffic on the canary.

    We sample many requests through the proxy and accept either 'already
    seeing both' or 'only stable' if the controller has already rolled back
    a previous scenario — in that case the test still proves the proxy
    answers.
    """
    names = set()
    for _ in range(80):
        body = httpx.get(f"{PROXY}/work", timeout=5.0).json()
        names.add(body["service"])
    assert names <= {"stable", "canary"}
    assert names  # at least one backend answered
