"""Rollback decision. Pure functions of MetricSample + DecisionConfig.

No I/O, no clock, no globals. tests/test_decision.py feeds synthetic samples
and asserts exact actions.

Three modes:

- naive_threshold: point estimates against fixed margins. The strawman baseline.
- tier1: fixed-sample tests on the current window, re-run every poll: Wilson
  intervals on the error rates, a p95 ratio on latency.
- tier2_sprt: Wald sequential probability ratio tests on per-request outcomes,
  accumulated across polls, one per signal per ramp stage.

tier1 and tier2 judge each signal separately and combine the verdicts with one
explicit rule (FUSION_RULE), so the decision log shows every signal's verdict
and evidence, not just whichever fired first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Literal

from config import DecisionConfig, DecisionMode
from stats import (
    difference_interval,
    sprt_boundaries,
    sprt_log_likelihood_ratio,
    wilson_interval,
    z_two_tailed,
)

Action = Literal["hold", "advance", "rollback", "promote"]
Verdict = Literal["worse", "inconclusive", "ok", "no_baseline"]

FUSION_RULE = (
    "rollback if any signal is worse; otherwise hold if any is inconclusive; otherwise advance "
    "(no_baseline never blocks)"
)


@dataclass(frozen=True)
class MetricSample:
    """One service's behaviour over one observation window."""

    service: str  # "stable" | "canary"
    window_start: float
    window_end: float
    request_count: int
    error_count: int
    p95_latency_ms: float
    latency_buckets: dict[float, int] = field(default_factory=dict)
    """Cumulative latency histogram over the window: bucket upper bound in seconds -> requests at
    or under it, with math.inf holding the total. Only Tier 2 reads it."""

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


@dataclass(frozen=True)
class Signal:
    """One signal's verdict for this poll, with the numbers behind it."""

    verdict: Verdict
    detail: str
    evidence: dict


def fuse(mode: str, signals: dict[str, Signal], common: dict) -> Decision:
    """Combine per-signal verdicts with FUSION_RULE, keeping every signal's evidence."""
    evidence = {
        "mode": mode,
        **common,
        "fusion": FUSION_RULE,
        "signals": {
            name: {"verdict": s.verdict, "detail": s.detail, **s.evidence}
            for name, s in signals.items()
        },
    }
    worse = [s.detail for s in signals.values() if s.verdict == "worse"]
    if worse:
        return Decision("rollback", "; ".join(worse), evidence)
    pending = [s.detail for s in signals.values() if s.verdict == "inconclusive"]
    if pending:
        return Decision("hold", "holding: " + "; ".join(pending), evidence)
    return Decision("advance", "; ".join(s.detail for s in signals.values()), evidence)


def evaluate(
    canary: MetricSample, stable: MetricSample, cfg: DecisionConfig, mode: DecisionMode
) -> Decision:
    """The stateless modes. tier2_sprt carries state between polls: use evaluate_tier2_sprt."""
    if mode == "naive_threshold":
        return evaluate_naive_threshold(canary, stable, cfg)
    if mode == "tier1":
        return evaluate_tier1(canary, stable, cfg)
    raise ValueError(f"{mode!r} is not a stateless decision mode")


def evaluate_naive_threshold(
    canary: MetricSample, stable: MetricSample, cfg: DecisionConfig
) -> Decision:
    """Point-estimate rule with no sample-size floor.

    Rollback if canary error rate exceeds stable's by more than
    naive_error_rate_delta, or canary p95 exceeds stable p95 times
    naive_latency_multiplier. Otherwise advance. This is the baseline the
    statistical detector is compared against: it will fire on a handful of
    unlucky requests that tier1 correctly holds on.

    Zero canary requests still holds: that is no observation at all, and
    "advancing" on it would ramp the baseline through stages on no data and
    inflate its blast radius for reasons unrelated to the threshold.
    """
    error_delta = canary.error_rate - stable.error_rate
    evidence = {
        "mode": "naive_threshold",
        **_counts(canary, stable),
        "error_delta": error_delta,
    }
    if canary.request_count == 0:
        evidence["signal"] = "no_canary_traffic"
        return Decision("hold", "naive: no canary requests in window; holding", evidence)
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


# --- Tier 1 ---------------------------------------------------------------------------------


def evaluate_tier1(canary: MetricSample, stable: MetricSample, cfg: DecisionConfig) -> Decision:
    """Fixed-sample tests on this window, re-run every poll.

    Errors: a Wilson interval for each version's error rate. The canary is worse only when the
    difference's lower bound (canary's lower bound minus stable's upper bound) is above 0, i.e.
    the two intervals don't overlap. That is stricter than a pooled two-proportion test at the
    same alpha, deliberately: the test is repeated on overlapping windows every poll, and every
    repeat is another chance of a false alarm.
    Latency: canary p95 against stable p95 x latency_tolerance.
    Neither signal is judged on fewer than min_samples_per_window canary requests.
    """
    z = z_two_tailed(cfg.alpha)
    common = {
        **_counts(canary, stable),
        "alpha": cfg.alpha,
        "z": z,
        "min_samples": cfg.min_samples_per_window,
    }
    signals = {
        "error_rate": _tier1_errors(canary, stable, cfg, z),
        "latency": _p95_ratio(canary, stable, cfg),
    }
    return fuse("tier1", signals, common)


def _tier1_errors(
    canary: MetricSample, stable: MetricSample, cfg: DecisionConfig, z: float
) -> Signal:
    n = canary.request_count
    if n < cfg.min_samples_per_window:
        return Signal(
            "inconclusive",
            f"error_rate: only {n} canary requests (need {cfg.min_samples_per_window})",
            {},
        )
    c_lo, c_hi = wilson_interval(canary.error_count, n, z)
    if stable.request_count < cfg.min_samples_per_window:
        # 100% canary: stable gets no traffic to compare against.
        evidence = {
            "canary_error_ci": [c_lo, c_hi],
            "absolute_ceiling": cfg.absolute_error_ceiling,
        }
        if c_lo > cfg.absolute_error_ceiling:
            return Signal(
                "worse",
                (
                    f"error_rate: no stable baseline; canary error {canary.error_rate:.3f} is "
                    f"significantly above the {cfg.absolute_error_ceiling} ceiling "
                    f"(CI [{c_lo:.3f}, {c_hi:.3f}])"
                ),
                evidence,
            )
        return Signal(
            "ok",
            (
                f"error_rate: no stable baseline; canary error {canary.error_rate:.3f} is not "
                f"significantly above the {cfg.absolute_error_ceiling} ceiling"
            ),
            evidence,
        )

    lo, hi = difference_interval(canary.error_count, n, stable.error_count, stable.request_count, z)
    delta = canary.error_rate - stable.error_rate
    evidence = {
        "canary_error_ci": [c_lo, c_hi],
        "stable_error_ci": list(wilson_interval(stable.error_count, stable.request_count, z)),
        "error_delta": delta,
        "error_delta_ci": [lo, hi],
    }
    rates = f"canary error {canary.error_rate:.3f} vs stable {stable.error_rate:.3f}"
    if lo > 0:
        return Signal(
            "worse",
            f"error_rate: {rates} significantly worse (Δ CI [{lo:.3f}, {hi:.3f}] excludes 0)",
            evidence,
        )
    if delta >= cfg.ambiguous_error_delta:
        return Signal(
            "inconclusive",
            f"error_rate: {rates} looks worse but Δ CI [{lo:.3f}, {hi:.3f}] includes 0",
            evidence,
        )
    return Signal("ok", f"error_rate: {rates} not worse (Δ CI [{lo:.3f}, {hi:.3f}])", evidence)


def _p95_ratio(canary: MetricSample, stable: MetricSample, cfg: DecisionConfig) -> Signal:
    if canary.request_count < cfg.min_samples_per_window:
        return Signal(
            "inconclusive",
            (
                f"latency: only {canary.request_count} canary requests "
                f"(need {cfg.min_samples_per_window})"
            ),
            {},
        )
    if stable.request_count < cfg.min_samples_per_window or stable.p95_latency_ms <= 0:
        return Signal("no_baseline", "latency: no stable traffic to compare against", {})
    ratio = canary.p95_latency_ms / stable.p95_latency_ms
    evidence = {"p95_ratio": ratio, "tolerance": cfg.latency_tolerance}
    p95s = f"canary p95 {canary.p95_latency_ms:.1f}ms"
    stable_p95 = f"{cfg.latency_tolerance}× stable {stable.p95_latency_ms:.1f}ms"
    if ratio > cfg.latency_tolerance:
        return Signal("worse", f"latency: {p95s} exceeds {stable_p95}", evidence)
    return Signal("ok", f"latency: {p95s} within {stable_p95}", evidence)


# --- Tier 2 ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class CounterSnapshot:
    """The canary's cumulative counters as Prometheus last scraped them.

    They only grow, until the canary restarts and they drop back to 0.
    """

    requests: int
    errors: int
    latency_buckets: dict[float, int]
    """Bucket upper bound in seconds -> cumulative requests at or under it (math.inf: total)."""

    def since(self, earlier: CounterSnapshot) -> CounterSnapshot:
        """What was counted after `earlier`; after a restart, everything counted so far."""
        if self.requests < earlier.requests:
            return self
        return CounterSnapshot(
            self.requests - earlier.requests,
            max(0, self.errors - earlier.errors),
            {
                le: max(0, count - earlier.latency_buckets.get(le, 0))
                for le, count in self.latency_buckets.items()
            },
        )


@dataclass(frozen=True)
class SequentialTest:
    """Wald's SPRT on a stream of yes/no outcomes: H0 rate p0 against H1 rate p1."""

    p0: float
    p1: float
    llr: float = 0.0
    n: int = 0
    events: int = 0
    verdict: Literal["continue", "h0", "h1"] = "continue"

    def update(self, events: int, n: int, upper: float, lower: float) -> SequentialTest:
        """Add a poll's worth of outcomes. A test that has crossed a boundary is over."""
        if self.verdict != "continue" or n <= 0:
            return self
        llr = self.llr + sprt_log_likelihood_ratio(events, n, self.p0, self.p1)
        verdict: Literal["continue", "h0", "h1"] = "continue"
        if llr >= upper:
            verdict = "h1"
        elif llr <= lower:
            verdict = "h0"
        return replace(self, llr=llr, n=self.n + n, events=self.events + events, verdict=verdict)


@dataclass(frozen=True)
class SprtState:
    """Tier 2's memory between polls."""

    stage_index: int
    last: CounterSnapshot
    """Canary counters at the previous poll; the next poll's outcomes are counted from here."""
    latency_bound: float
    """Seconds. For the latency test, "slow" means slower than this histogram bucket boundary."""
    errors: SequentialTest
    slow: SequentialTest


def evaluate_tier2_sprt(
    canary: MetricSample,
    stable: MetricSample,
    counters: CounterSnapshot,
    state: SprtState | None,
    stage_index: int,
    stages: int,
    cfg: DecisionConfig,
) -> tuple[Decision, SprtState | None]:
    """Sequential tests on per-request outcomes, carried across polls.

    Each ramp stage starts two fresh Wald SPRTs, fed the canary's requests since the previous
    poll:
    - errors: is the canary's error rate stable's (H0), or sequential_min_effect higher (H1)?
    - latency: is the share of canary requests slower than L stable's share (H0), or
      sequential_min_effect higher (H1)? L is latency_tolerance x stable p95, rounded up to the
      next histogram bucket boundary.
    H0's rates are stable's over the window at the stage's first poll (add-one smoothed, see
    _add_one_rate). At 100% canary, where stable gets no traffic, the previous stage's carry over.
    Boundaries come from beta and alpha split over every test in the rollout
    (DecisionConfig.sequential_alpha). A test that crosses a boundary stays decided until the
    next stage starts new ones, so a fault that begins after a stage accepted H0 is caught by the
    next stage's tests, at most one bake later. Keeping a test running past H0 instead was
    simulated and doubled false rollbacks (1.0% -> 2.2%) without catching faults sooner.

    Returns the decision and the state to pass back in on the next poll.
    """
    alpha = cfg.sequential_alpha(stages)
    upper, lower = sprt_boundaries(alpha, cfg.beta)
    common = {
        **_counts(canary, stable),
        "alpha_per_test": alpha,
        "beta": cfg.beta,
        "boundary_rollback": upper,
        "boundary_accept": lower,
    }

    if state is None or state.stage_index != stage_index:
        started = _start_stage(stable, counters, state, stage_index, cfg)
        if started is None:
            waiting = Signal(
                "inconclusive",
                (
                    f"waiting for {cfg.min_samples_per_window} stable requests to set the "
                    f"reference rates (have {stable.request_count})"
                ),
                {},
            )
            return fuse("tier2_sprt", {"error_rate": waiting, "latency": waiting}, common), None
        state = started

    new = counters.since(state.last)
    state = replace(
        state,
        last=counters,
        errors=state.errors.update(new.errors, new.requests, upper, lower),
        slow=state.slow.update(
            _slower_than(new.latency_buckets, state.latency_bound),
            new.latency_buckets.get(math.inf, new.requests),
            upper,
            lower,
        ),
    )
    if math.isfinite(state.latency_bound):
        common["latency_bound_ms"] = state.latency_bound * 1000.0
    signals = {
        "error_rate": _sequential_signal("error_rate", "errors", state.errors, upper, lower),
        "latency": _sequential_signal(
            "latency",
            f"slower than {state.latency_bound * 1000:.0f}ms",
            state.slow,
            upper,
            lower,
        ),
    }
    return fuse("tier2_sprt", signals, common), state


def _start_stage(
    stable: MetricSample,
    counters: CounterSnapshot,
    previous: SprtState | None,
    stage_index: int,
    cfg: DecisionConfig,
) -> SprtState | None:
    if stable.request_count >= cfg.min_samples_per_window:
        p0 = _add_one_rate(stable.error_count, stable.request_count)
        bound = _latency_bound(stable, cfg)
        timed = stable.latency_buckets.get(math.inf, stable.request_count)
        q0 = _add_one_rate(_slower_than(stable.latency_buckets, bound), timed)
    elif previous is not None:
        p0, q0, bound = previous.errors.p0, previous.slow.p0, previous.latency_bound
    else:
        return None
    effect = cfg.sequential_min_effect
    return SprtState(
        stage_index=stage_index,
        # Traffic since the previous poll already ran at this stage's weight.
        last=previous.last if previous is not None else counters,
        latency_bound=bound,
        errors=SequentialTest(p0, min(p0 + effect, 0.999)),
        slow=SequentialTest(q0, min(q0 + effect, 0.999)),
    )


def _add_one_rate(events: int, n: int) -> float:
    """Laplace's add-one estimate (events + 1) / (n + 2).

    With the ~1000 stable requests a window holds it is within a hair of the plain rate, but it is
    never exactly 0, so one canary error against an error-free stable can't produce an infinite
    likelihood ratio.
    """
    return (events + 1) / (n + 2)


def _latency_bound(stable: MetricSample, cfg: DecisionConfig) -> float:
    """latency_tolerance x stable p95, rounded up to a histogram bucket boundary, in seconds."""
    target = stable.p95_latency_ms / 1000.0 * cfg.latency_tolerance
    finite = sorted(b for b in stable.latency_buckets if math.isfinite(b))
    for bound in finite:
        if bound >= target:
            return bound
    return finite[-1] if finite else math.inf


def _sequential_signal(
    name: str, outcome: str, test: SequentialTest, upper: float, lower: float
) -> Signal:
    evidence = {
        "p0": test.p0,
        "p1": test.p1,
        "llr": test.llr,
        "n": test.n,
        "events": test.events,
        "test": test.verdict,
    }
    seen = f"{test.events}/{test.n} canary requests {outcome}"
    if test.verdict == "h1":
        return Signal(
            "worse",
            (
                f"{name}: {seen} fits H1 (rate {test.p1:.3f}) over H0 ({test.p0:.3f}); "
                f"LLR {test.llr:.2f} ≥ {upper:.2f}"
            ),
            evidence,
        )
    if test.verdict == "h0":
        return Signal(
            "ok",
            f"{name}: {seen} fits H0 (rate {test.p0:.3f}); LLR {test.llr:.2f} ≤ {lower:.2f}",
            evidence,
        )
    return Signal(
        "inconclusive",
        f"{name}: {seen} so far, LLR {test.llr:.2f} between {lower:.2f} and {upper:.2f}",
        evidence,
    )


# --- shared ---------------------------------------------------------------------------------


def _counts(canary: MetricSample, stable: MetricSample) -> dict:
    return {
        "canary_n": canary.request_count,
        "stable_n": stable.request_count,
        "canary_error_rate": canary.error_rate,
        "stable_error_rate": stable.error_rate,
        "canary_p95_ms": canary.p95_latency_ms,
        "stable_p95_ms": stable.p95_latency_ms,
    }


def _slower_than(buckets: dict[float, int], bound: float) -> int:
    """Requests in a cumulative histogram slower than one of its bucket boundaries."""
    total = buckets.get(math.inf, 0)
    return max(0, total - buckets.get(bound, total))
