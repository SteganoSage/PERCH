"""controller/stats.py against published or closed-form values. No Docker required."""

from __future__ import annotations

import math

import pytest
from stats import sprt_boundaries, sprt_log_likelihood_ratio, wilson_interval, z_two_tailed


def test_z_for_95_percent() -> None:
    assert z_two_tailed(0.05) == pytest.approx(1.959964, abs=1e-6)


def test_wilson_interval_zero_of_ten() -> None:
    """The textbook case where the plain normal interval collapses to [0, 0]."""
    lo, hi = wilson_interval(0, 10, z_two_tailed(0.05))
    assert lo == pytest.approx(0.0, abs=1e-12)
    assert hi == pytest.approx(0.2775, abs=1e-4)


def test_wilson_interval_matches_newcombe_1998() -> None:
    """Newcombe (1998), Statistics in Medicine 17:857, Table I: 81/263 -> 0.2553 to 0.3662."""
    lo, hi = wilson_interval(81, 263, z_two_tailed(0.05))
    assert lo == pytest.approx(0.2553, abs=1e-4)
    assert hi == pytest.approx(0.3662, abs=1e-4)


def test_wald_boundaries() -> None:
    upper, lower = sprt_boundaries(0.05, 0.10)
    assert upper == pytest.approx(math.log(0.90 / 0.05))
    assert lower == pytest.approx(math.log(0.10 / 0.95))


def test_llr_is_additive_over_batches() -> None:
    """Feeding outcomes poll by poll must give the same evidence as all at once."""
    whole = sprt_log_likelihood_ratio(7, 100, 0.01, 0.06)
    parts = sprt_log_likelihood_ratio(3, 40, 0.01, 0.06) + sprt_log_likelihood_ratio(
        4, 60, 0.01, 0.06
    )
    assert whole == pytest.approx(parts)
