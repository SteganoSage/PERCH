"""analysis/run_campaign.py planning and tabulation. No Docker required."""

from __future__ import annotations

import pytest
from run_campaign import plan, stat, table


def test_mode_order_rotates_each_repeat() -> None:
    cells = plan(["good", "bad-error"], ["a", "b", "c"], repeats=2)
    assert [m for r, s, m in cells if r == 0 and s == "good"] == ["a", "b", "c"]
    assert [m for r, s, m in cells if r == 1 and s == "good"] == ["b", "c", "a"]
    assert len(cells) == 2 * 2 * 3


def test_stat_uses_sample_variance() -> None:
    s = stat([10, 20, 30])
    assert (s.n, s.mean, s.var, s.lo, s.hi) == (3, 20, 100, 10, 30)
    assert s.sd == pytest.approx(10)


def test_table_counts_wrong_outcomes_and_only_averages_hits() -> None:
    base = {"scenario": "good", "mode": "naive_threshold", "canary_errors": 0}
    rows = [
        {**base, "ok": True, "promoted": True, "seconds": 120.0, "canary_requests": 2400},
        {**base, "ok": True, "promoted": True, "seconds": 130.0, "canary_requests": 2410},
        {**base, "ok": False, "rolled_back": True, "seconds": 40.0, "canary_requests": 90},
        {**base, "ok": False, "error": "no SUMMARY line"},
    ]
    line = table(rows).splitlines()[-1]
    assert "| 2/4, 1 wrong, 1 timed out or failed |" in line
    assert "125.0 ± 7.1" in line
    assert "2405.0" in line
