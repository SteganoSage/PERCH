"""nginx template rendering tests. No Docker required.

These tests verify that the Jinja2 template correctly renders nginx
configuration with weighted routing, particularly the zero-weight handling.
"""

from __future__ import annotations

from pathlib import Path

from proxy_writer import ProxyWriter

TEMPLATE = Path(__file__).resolve().parents[1] / "proxy" / "nginx.conf.j2"


def _writer(tmp_path: Path) -> ProxyWriter:
    """Helper to create a ProxyWriter for testing.

    Args:
        tmp_path: Temporary directory path for output

    Returns:
        ProxyWriter instance configured for testing
    """
    return ProxyWriter(
        template_path=str(TEMPLATE),
        output_path=str(tmp_path / "default.conf"),
        proxy_container="unused",
        debounce_seconds=0,
    )


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
