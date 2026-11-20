"""scenarios/run.py helpers. No Docker required."""

from __future__ import annotations

from run import time_to_terminal


def test_time_to_terminal_uses_the_controllers_monotonic_clock() -> None:
    """Docker Desktop's VM steps its wall clock back ~2.3s when it resyncs with the host. A rollback
    at the controller's fifth poll is 20s on its own clock, though unix time says 17.7s."""
    rows = [
        {"unix": 1000.0, "elapsed_s": 0.0, "action": "hold"},
        {"unix": 1005.0, "elapsed_s": 5.0, "action": "hold"},
        {"unix": 1010.0, "elapsed_s": 10.0, "action": "hold"},
        {"unix": 1012.7, "elapsed_s": 15.0, "action": "hold"},  # wall clock stepped back 2.3s
        {"unix": 1017.7, "elapsed_s": 20.0, "action": "rollback"},
    ]
    assert time_to_terminal(rows) == 20.0


def test_time_to_terminal_reads_logs_from_before_elapsed_s() -> None:
    rows = [{"unix": 1000.0, "action": "hold"}, {"unix": 1020.0, "action": "promote"}]
    assert time_to_terminal(rows) == 20.0


def test_time_to_terminal_without_an_outcome_is_none() -> None:
    assert time_to_terminal([{"unix": 1.0, "elapsed_s": 0.0, "action": "hold"}]) is None
