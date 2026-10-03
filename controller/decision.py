"""Rollback decision. Pure functions of MetricSample + DecisionConfig.

No I/O, no clock, no globals. tests/test_decision.py feeds synthetic samples
and asserts exact actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from config import DecisionConfig, DecisionMode
from stats import difference_interval, sprt_boundaries, sprt_log_likelihood_ratio, z_two_tailed

Action = Literal["hold", "advance", "rollback", "promote"]

# If the Wilson interval overlaps 0 but the point estimate is at least this
# much worse, we refuse to advance. That is the "borderline → hold" case.
AMBIGUOUS_ERROR_DELTA = 0.01


@dataclass(frozen=True)
class MetricSample:
    """One service's behaviour over one observation window."""

    service: str  # "stable" | "canary"
    window_start: float
    window_end: float
    request_count: int
    error_count: int
    p95_latency_ms: float

    @property
    def error_rate(self) -> float:
        if self.request_count <= 0:
            return 0.0
        return self.error_count / self.request_count


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    evidence: dict


def evaluate(
    canary: MetricSample, stable: MetricSample, cfg: DecisionConfig, mode: DecisionMode
) -> Decision:
    if mode == "naive_threshold":
        return evaluate_naive_threshold(canary, stable, cfg)
    if mode == "tier1":
        return evaluate_tier1(canary, stable, cfg)
    if mode == "tier2_sprt":
        return evaluate_tier2_sprt(canary, stable, cfg)
    raise ValueError(f"unknown decision mode: {mode!r}")


def evaluate_naive_threshold(
    canary: MetricSample, stable: MetricSample, cfg: DecisionConfig
) -> Decision:
    """Point-estimate rule with no sample-size floor.

    Rollback if canary error rate exceeds stable's by more than
    naive_error_rate_delta, or canary p95 exceeds stable p95 times
    naive_latency_multiplier. Otherwise advance. This is the baseline the
    statistical detector is compared against: it will fire on a handful of
    unlucky requests that tier1 correctly holds on.
    """
    error_delta = canary.error_rate - stable.error_rate
    evidence = {
        "mode": "naive_threshold",
        "canary_error_rate": canary.error_rate,
        "stable_error_rate": stable.error_rate,
        "error_delta": error_delta,
        "canary_p95_ms": canary.p95_latency_ms,
        "stable_p95_ms": stable.p95_latency_ms,
        "canary_n": canary.request_count,
        "stable_n": stable.request_count,
    }
    if error_delta > cfg.naive_error_rate_delta:
        return Decision(
            "rollback",
            (
                f"naive: canary error {canary.error_rate:.3f} exceeds stable "
                f"{stable.error_rate:.3f} by {error_delta:.3f} "
                f"(threshold {cfg.naive_error_rate_delta})"
            ),
            evidence,
        )
    if stable.p95_latency_ms > 0 and canary.p95_latency_ms > (
        stable.p95_latency_ms * cfg.naive_latency_multiplier
    ):
        return Decision(
            "rollback",
            (
                f"naive: canary p95 {canary.p95_latency_ms:.1f}ms > "
                f"{cfg.naive_latency_multiplier}× stable "
                f"{stable.p95_latency_ms:.1f}ms"
            ),
            evidence,
        )
    return Decision("advance", "naive: canary within error and latency thresholds", evidence)


def evaluate_tier1(canary: MetricSample, stable: MetricSample, cfg: DecisionConfig) -> Decision:
    """Wilson-interval two-proportion test on errors + p95 ratio on latency.

    Hold below min_samples_per_window. Rollback only when the interval for
    (canary_error_rate − stable_error_rate) sits entirely above 0, or canary
    p95 exceeds stable p95 × latency_tolerance. Borderline overlap with a
    worse point estimate holds rather than guessing.
    """
    z = z_two_tailed(cfg.alpha)
    evidence: dict = {
        "mode": "tier1",
        "alpha": cfg.alpha,
        "z": z,
        "canary_n": canary.request_count,
        "stable_n": stable.request_count,
        "canary_error_rate": canary.error_rate,
        "stable_error_rate": stable.error_rate,
        "canary_p95_ms": canary.p95_latency_ms,
        "stable_p95_ms": stable.p95_latency_ms,
        "min_samples": cfg.min_samples_per_window,
    }

    if canary.request_count < cfg.min_samples_per_window:
        evidence["signal"] = "insufficient_samples"
        return Decision(
            "hold",
            (
                f"only {canary.request_count} canary requests "
                f"(need {cfg.min_samples_per_window}); holding"
            ),
            evidence,
        )

    # At 100% canary, stable may have no traffic in the window. Compare
    # against an absolute tripwire so we still have a rollback path.
    if stable.request_count < cfg.min_samples_per_window:
        return _absolute_canary_check(canary, cfg, evidence)

    delta_lo, delta_hi = difference_interval(
        canary.error_count,
        canary.request_count,
        stable.error_count,
        stable.request_count,
        z,
    )
    evidence["error_delta_ci"] = [delta_lo, delta_hi]
    evidence["error_delta"] = canary.error_rate - stable.error_rate

    if delta_lo > 0:
        evidence["signal"] = "error_rate"
        return Decision(
            "rollback",
            (
                f"canary error {canary.error_rate:.3f} significantly worse than "
                f"stable {stable.error_rate:.3f} "
                f"(Δ CI [{delta_lo:.3f}, {delta_hi:.3f}] excludes 0)"
            ),
            evidence,
        )

    if (
        stable.p95_latency_ms > 0
        and canary.p95_latency_ms > stable.p95_latency_ms * cfg.latency_tolerance
    ):
        evidence["signal"] = "latency"
        return Decision(
            "rollback",
            (
                f"canary p95 {canary.p95_latency_ms:.1f}ms exceeds "
                f"{cfg.latency_tolerance}× stable {stable.p95_latency_ms:.1f}ms"
            ),
            evidence,
        )

    observed_delta = canary.error_rate - stable.error_rate
    if observed_delta >= AMBIGUOUS_ERROR_DELTA and delta_lo <= 0 <= delta_hi:
        evidence["signal"] = "ambiguous_error_rate"
        return Decision(
            "hold",
            (
                f"canary looks slightly worse (Δ={observed_delta:.3f}) but "
                f"CI [{delta_lo:.3f}, {delta_hi:.3f}] still includes 0; holding"
            ),
            evidence,
        )

    evidence["signal"] = "healthy"
    return Decision(
        "advance",
        "canary error rate and p95 are not worse than stable",
        evidence,
    )


def _absolute_canary_check(
    canary: MetricSample, cfg: DecisionConfig, evidence: dict
) -> Decision:
    """Used when stable has too few samples (typically the 100% stage)."""
    evidence["signal"] = "absolute_canary"
    if canary.error_rate > cfg.naive_error_rate_delta:
        return Decision(
            "rollback",
            (
                f"no stable baseline; canary error {canary.error_rate:.3f} "
                f"exceeds absolute {cfg.naive_error_rate_delta}"
            ),
            evidence,
        )
    return Decision(
        "advance",
        "no stable baseline this window; canary error rate is acceptable",
        evidence,
    )


def evaluate_tier2_sprt(
    canary: MetricSample, stable: MetricSample, cfg: DecisionConfig
) -> Decision:
    """SPRT on the current window's error counts, plus the same latency ratio.

    H0: canary error rate = stable error rate (floored at 0.1%).
    H1: canary error rate = stable + 10 percentage points.
    The window is treated as one binomial batch. Latency still uses the
    percentile ratio so a slow-but-correct canary is caught.
    """
    evidence: dict = {
        "mode": "tier2_sprt",
        "canary_n": canary.request_count,
        "stable_n": stable.request_count,
        "canary_error_rate": canary.error_rate,
        "stable_error_rate": stable.error_rate,
        "canary_p95_ms": canary.p95_latency_ms,
        "stable_p95_ms": stable.p95_latency_ms,
    }
    if canary.request_count < cfg.min_samples_per_window:
        return Decision(
            "hold",
            f"only {canary.request_count} canary requests; holding",
            evidence,
        )

    if (
        stable.request_count >= cfg.min_samples_per_window
        and stable.p95_latency_ms > 0
        and canary.p95_latency_ms > stable.p95_latency_ms * cfg.latency_tolerance
    ):
        evidence["signal"] = "latency"
        return Decision(
            "rollback",
            (
                f"canary p95 {canary.p95_latency_ms:.1f}ms exceeds "
                f"{cfg.latency_tolerance}× stable {stable.p95_latency_ms:.1f}ms"
            ),
            evidence,
        )

    p0 = min(0.5, max(stable.error_rate, 0.001))
    p1 = min(0.99, p0 + 0.10)
    llr = sprt_log_likelihood_ratio(canary.error_count, canary.request_count, p0, p1)
    bound_a, bound_b = sprt_boundaries(cfg.alpha, cfg.beta)
    evidence.update({"p0": p0, "p1": p1, "llr": llr, "A": bound_a, "B": bound_b})

    if llr >= bound_a:
        evidence["signal"] = "sprt_h1"
        return Decision(
            "rollback",
            f"SPRT LLR {llr:.2f} ≥ A {bound_a:.2f}: canary errors match H1 (p={p1:.3f})",
            evidence,
        )
    if llr <= bound_b:
        evidence["signal"] = "sprt_h0"
        return Decision(
            "advance",
            f"SPRT LLR {llr:.2f} ≤ B {bound_b:.2f}: canary errors match H0 (p={p0:.3f})",
            evidence,
        )
    evidence["signal"] = "sprt_continue"
    return Decision(
        "hold",
        f"SPRT LLR {llr:.2f} still between B {bound_b:.2f} and A {bound_a:.2f}",
        evidence,
    )
