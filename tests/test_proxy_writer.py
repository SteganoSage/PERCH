"""nginx template rendering and write-debounce tests. No Docker required.

These tests verify that the Jinja2 template correctly renders nginx
configuration with weighted routing, particularly the zero-weight handling,
and that config writes (which trigger the proxy's reload) are debounced.
"""

from __future__ import annotations

from pathlib import Path

import proxy_writer
import pytest
from proxy_writer import ProxyWriter

TEMPLATE = Path(__file__).resolve().parents[1] / "proxy" / "nginx.conf.j2"


def _writer(tmp_path: Path, debounce_seconds: float = 0) -> ProxyWriter:
    """Helper to create a ProxyWriter for testing.

    Args:
        tmp_path: Temporary directory path for output
        debounce_seconds: Minimum seconds between config writes

    Returns:
        ProxyWriter instance configured for testing
    """
    return ProxyWriter(
        template_path=str(TEMPLATE),
        output_path=str(tmp_path / "default.conf"),
        debounce_seconds=debounce_seconds,
    )


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record debounce waits instead of actually sleeping."""
    calls: list[float] = []
    monkeypatch.setattr(proxy_writer.time, "sleep", calls.append)
    return calls


def test_zero_canary_weight_emits_down(tmp_path: Path) -> None:
    """Zero canary weight should be rendered as 'down', not 'weight=0'.

    nginx rejects weight=0 as invalid, so we emit 'down' instead to
    ensure no traffic is sent to the canary.
    """
    text = _writer(tmp_path).render(stable_weight=100, canary_weight=0)
    server_lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("server ")]
    assert any("canary:8000 down" in ln for ln in server_lines)
    assert not any("weight=0" in ln for ln in server_lines)
    assert any("stable:8000 weight=100" in ln for ln in server_lines)


def test_split_weights(tmp_path: Path) -> None:
    """Non-zero weights should be rendered correctly in the upstream config.

    This tests the normal case where both services receive traffic.
    """
    text = _writer(tmp_path).render(stable_weight=95, canary_weight=5)
    assert "stable:8000 weight=95" in text
    assert "canary:8000 weight=5" in text


def test_rendered_config_reports_its_weights(tmp_path: Path) -> None:
    """The runner and e2e tests read this header to confirm nginx loaded a config."""
    text = _writer(tmp_path).render(stable_weight=95, canary_weight=5)
    assert 'add_header X-Perch-Weights "95/5" always;' in text


def test_unchanged_weights_are_not_rewritten(tmp_path: Path, sleeps: list[float]) -> None:
    """Rewriting the file is what reloads nginx, so same weights must not touch it."""
    writer = _writer(tmp_path)
    assert writer.apply(95, 5) is True
    assert writer.apply(95, 5) is False
    assert writer.apply(95, 5, force=True) is True


def test_back_to_back_writes_are_debounced(tmp_path: Path, sleeps: list[float]) -> None:
    """A second write inside the debounce window waits out the remainder first."""
    writer = _writer(tmp_path, debounce_seconds=2)
    writer.apply(95, 5)
    assert sleeps == []
    writer.apply(90, 10)
    assert len(sleeps) == 1
    assert 1.5 < sleeps[0] <= 2
    assert "canary:8000 weight=10" in (tmp_path / "default.conf").read_text(encoding="utf-8")
