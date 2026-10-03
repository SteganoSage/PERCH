"""Small statistical helpers. Stdlib only — the decision module stays pure and
easy to test without scipy.
"""

from __future__ import annotations

import math
from statistics import NormalDist

_UNIT = NormalDist()


def z_two_tailed(alpha: float) -> float:
    """z such that P(|Z| > z) = alpha under a standard normal."""
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    return _UNIT.inv_cdf(1 - alpha / 2)


def wilson_interval(successes: int, n: int, z: float) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Better than the naive p ± z√(p(1-p)/n) when p is near 0 — which is exactly
    where a healthy service lives (almost no errors).
    """
    if n <= 0:
        return (0.0, 1.0)
    successes = max(0, min(successes, n))
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def difference_interval(
    a_successes: int, a_n: int, b_successes: int, b_n: int, z: float
) -> tuple[float, float]:
    """Conservative interval for P_a − P_b from the two Wilson intervals.

    Lower = a_lo − b_hi, upper = a_hi − b_lo. If the lower bound is still > 0,
    a is significantly larger than b even after giving b every benefit of the
    doubt. That is the rollback trigger for error rate.
    """
    a_lo, a_hi = wilson_interval(a_successes, a_n, z)
    b_lo, b_hi = wilson_interval(b_successes, b_n, z)
    return (a_lo - b_hi, a_hi - b_lo)


def sprt_log_likelihood_ratio(
    errors: int, n: int, p0: float, p1: float
) -> float:
    """Batch log-likelihood ratio for a binomial: H0: p=p0 vs H1: p=p1."""
    if n <= 0 or p0 <= 0 or p1 <= 0 or p0 >= 1 or p1 >= 1:
        return 0.0
    successes = n - errors
    return errors * math.log(p1 / p0) + successes * math.log((1.0 - p1) / (1.0 - p0))


def sprt_boundaries(alpha: float, beta: float) -> tuple[float, float]:
    """Wald SPRT thresholds A (reject H0 / rollback) and B (accept H0 / advance)."""
    a = math.log((1.0 - beta) / alpha)
    b = math.log(beta / (1.0 - alpha))
    return a, b
