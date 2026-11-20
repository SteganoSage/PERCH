"""Monte Carlo rollouts of the real controller logic, no Docker.

Drives controller/decision.py and controller/ramp.py (the same code the controller container runs)
with synthetic traffic, thousands of times, to measure what a handful of live runs can't: how often
a healthy canary is wrongly rolled back, and how much traffic a broken one reaches first.

Traffic model, mirroring the live stack's defaults:
- open-loop load at --rps, split by the current weight the way nginx's weighted round-robin does
  (deterministic shares, not random draws);
- every request fails independently with its version's error probability;
- latency ~ Normal(mean, jitter) per version, as services/*/app.py injects it;
- the load generator is already running when the controller starts (scenarios/run.py order), and
  the controller polls every POLL_INTERVAL_SECONDS over the last RAMP_BAKE_SECONDS of traffic, like
  Prometheus increase()[25s].

Usage:
    python analysis/simulate_rollouts.py --runs 1000            # 1% background errors
    python analysis/simulate_rollouts.py --background-error 0   # both versions error-free
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))

from config import ControllerConfig, DecisionMode  # noqa: E402
from decision import (  # noqa: E402
    CounterSnapshot,
    MetricSample,
    SprtState,
    evaluate,
    evaluate_tier2_sprt,
)
from ramp import apply_decision, initial_state  # noqa: E402

MODES: tuple[DecisionMode, ...] = ("naive_threshold", "tier1", "tier2_sprt")
HEALTHY_LATENCY = (40.0, 10.0)  # ms mean, jitter — services/*/app.py defaults
# services/*/app.py LATENCY_BUCKETS (seconds), plus +Inf as Prometheus exposes it.
BUCKETS = np.array(
    [0.005, 0.01, 0.02, 0.03, 0.04, 0.05, 0.075, 0.1, 0.15, 0.2]
    + [0.3, 0.4, 0.5, 0.75, 1.0, 2.5, math.inf]
)
# The canary container starts a few seconds before the controller (scenarios/run.py), which is
# where a slow-onset fault's clock begins.
CANARY_HEAD_START_SECONDS = 5.0


@dataclass(frozen=True)
class Scenario:
    name: str
    expect: str  # "promote" | "rollback"
    extra_error: float = 0.0  # injected fault, failing independently of the background rate
    latency: tuple[float, float] = HEALTHY_LATENCY
    onset_seconds: float = 0.0  # >0: the fault ramps in linearly over this long (slow onset)


# Mirrors scenarios/run.py. bad-partial's 60% on /work/checkout, a third of the mix, is about the
# same per-request failure probability as 20% everywhere, which is all an aggregate test sees.
SCENARIOS = (
    Scenario("good", "promote"),
    Scenario("bad-error", "rollback", extra_error=0.30),
    Scenario("bad-latency", "rollback", latency=(400.0, 120.0)),
    Scenario("bad-slow-onset", "rollback", extra_error=0.30, onset_seconds=120.0),
    Scenario("bad-partial", "rollback", extra_error=0.20),
    Scenario("bad-subtle", "rollback", extra_error=0.05),
)


@dataclass(frozen=True)
class Tick:
    """One poll interval of traffic for one version."""

    n: int
    errors: int
    buckets: np.ndarray  # cumulative requests at or under each of BUCKETS


EMPTY_TICK = Tick(0, 0, np.zeros(len(BUCKETS), dtype=int))


@dataclass(frozen=True)
class RunResult:
    outcome: str  # "promote" | "rollback" | "timeout"
    seconds: float
    canary_requests: int
    canary_errors: int


def _traffic(
    rng: np.random.Generator, n: int, error_p: float, latency: tuple[float, float]
) -> Tick:
    if n == 0:
        return EMPTY_TICK
    errors = int(rng.binomial(n, min(1.0, max(0.0, error_p))))
    seconds = np.sort(np.maximum(0.0, rng.normal(latency[0], latency[1], n))) / 1000.0
    return Tick(n, errors, np.searchsorted(seconds, BUCKETS, side="right"))


def _histogram_quantile(q: float, cumulative: np.ndarray) -> float:
    """Prometheus's histogram_quantile: linear interpolation inside the bucket holding rank q."""
    total = cumulative[-1]
    if total == 0:
        return 0.0
    rank = q * total
    i = int(np.searchsorted(cumulative, rank, side="left"))
    if math.isinf(BUCKETS[i]):
        return float(BUCKETS[i - 1])
    lower, below = (BUCKETS[i - 1], cumulative[i - 1]) if i > 0 else (0.0, 0)
    return float(lower + (BUCKETS[i] - lower) * (rank - below) / (cumulative[i] - below))


def _window_sample(service: str, ticks: deque[Tick], start: float, end: float) -> MetricSample:
    cumulative = np.sum([t.buckets for t in ticks], axis=0)
    return MetricSample(
        service,
        start,
        end,
        sum(t.n for t in ticks),
        sum(t.errors for t in ticks),
        _histogram_quantile(0.95, cumulative) * 1000.0,
        dict(zip(BUCKETS.tolist(), cumulative.tolist(), strict=True)),
    )


def simulate(
    mode: DecisionMode,
    scenario: Scenario,
    background_error: float,
    cfg: ControllerConfig,
    rps: float,
    rng: np.random.Generator,
    max_seconds: float,
) -> RunResult:
    poll = cfg.poll_interval_seconds
    window_ticks = max(1, round(cfg.ramp.bake_seconds / poll))
    per_tick = round(rps * poll)
    stable_hist: deque[Tick] = deque(maxlen=window_ticks)
    canary_hist: deque[Tick] = deque(maxlen=window_ticks)
    for _ in range(window_ticks):  # load generator ran on 100% stable before the controller
        stable_hist.append(_traffic(rng, per_tick, background_error, HEALTHY_LATENCY))
        canary_hist.append(EMPTY_TICK)

    state = initial_state(cfg.ramp, 0.0)
    sprt: SprtState | None = None
    totals = EMPTY_TICK  # the canary's cumulative counters, as Tier 2 reads them
    carry = 0.0
    served = errors_served = 0
    now = 0.0
    while now <= max_seconds:
        canary = _window_sample("canary", canary_hist, now - cfg.ramp.bake_seconds, now)
        stable = _window_sample("stable", stable_hist, now - cfg.ramp.bake_seconds, now)
        if mode == "tier2_sprt":
            counters = CounterSnapshot(
                totals.n,
                totals.errors,
                dict(zip(BUCKETS.tolist(), totals.buckets.tolist(), strict=True)),
            )
            decision, sprt = evaluate_tier2_sprt(
                canary,
                stable,
                counters,
                sprt,
                state.stage_index,
                len(cfg.ramp.stages),
                cfg.decision,
            )
        else:
            decision = evaluate(canary, stable, cfg.decision, mode)
        state, _ = apply_decision(state, decision, cfg.ramp, now)
        if state.rolled_back:
            return RunResult("rollback", now, served, errors_served)
        if state.promoted:
            return RunResult("promote", now, served, errors_served)

        exact = per_tick * state.canary_weight / 100.0 + carry
        n_canary = int(exact)
        carry = exact - n_canary
        onset = 1.0
        if scenario.onset_seconds > 0:
            onset = min(1.0, (now + CANARY_HEAD_START_SECONDS) / scenario.onset_seconds)
        # Background and fault fail independently, as in services/*/app.py.
        canary_error = 1.0 - (1.0 - background_error) * (1.0 - scenario.extra_error * onset)
        mean_ms = HEALTHY_LATENCY[0] + (scenario.latency[0] - HEALTHY_LATENCY[0]) * onset
        jitter_ms = HEALTHY_LATENCY[1] + (scenario.latency[1] - HEALTHY_LATENCY[1]) * onset
        tick = _traffic(rng, n_canary, canary_error, (mean_ms, jitter_ms))
        canary_hist.append(tick)
        totals = Tick(totals.n + tick.n, totals.errors + tick.errors, totals.buckets + tick.buckets)
        stable_hist.append(_traffic(rng, per_tick - n_canary, background_error, HEALTHY_LATENCY))
        served += tick.n
        errors_served += tick.errors
        now += poll
    return RunResult("timeout", now, served, errors_served)


def rate_ci(k: int, n: int) -> str:
    """Rate with a 95% Wilson interval, e.g. '4.1% [2.9, 5.6]'."""
    if n == 0:
        return "-"
    z = 1.959964
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return f"{100 * p:5.1f}% [{100 * max(0, centre - half):.1f}, {100 * min(1, centre + half):.1f}]"


def _mean_sd(values: list[float]) -> str:
    if not values:
        return "-"
    sd = stdev(values) if len(values) > 1 else 0.0
    return f"{mean(values):7.1f} ± {sd:5.1f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=1000, help="rollouts per scenario × mode")
    parser.add_argument(
        "--background-error",
        type=float,
        default=0.01,
        help="error rate both versions share (BASE_ERROR_RATE; the stack's default is 1%%)",
    )
    parser.add_argument("--rps", type=float, default=50.0)
    parser.add_argument("--max-seconds", type=float, default=600.0)
    parser.add_argument("--seed", type=int, default=20261031)
    parser.add_argument("--scenarios", nargs="*", default=[s.name for s in SCENARIOS])
    parser.add_argument("--modes", nargs="*", default=list(MODES))
    args = parser.parse_args()

    cfg = ControllerConfig()
    rng = np.random.default_rng(args.seed)
    print(
        f"{args.runs} rollouts per cell · {args.rps:g} rps · background error "
        f"{100 * args.background_error:g}% · stages {list(cfg.ramp.stages)} · "
        f"bake {cfg.ramp.bake_seconds}s · poll {cfg.poll_interval_seconds}s · seed {args.seed}\n"
    )
    header = (
        f"{'scenario':<15}{'mode':<17}{'expected outcome (95% CI)':<28}"
        f"{'wrong outcome':<20}{'time to outcome s':<20}{'canary requests':<19}"
        f"{'canary errors served'}"
    )
    print(header)
    print("-" * len(header))
    for scenario in (s for s in SCENARIOS if s.name in args.scenarios):
        for mode in args.modes:
            results = [
                simulate(
                    mode, scenario, args.background_error, cfg, args.rps, rng, args.max_seconds
                )
                for _ in range(args.runs)
            ]
            hit = [r for r in results if r.outcome == scenario.expect]
            wrong = sum(1 for r in results if r.outcome not in (scenario.expect, "timeout"))
            print(
                f"{scenario.name:<15}{mode:<17}{rate_ci(len(hit), len(results)):<28}"
                f"{rate_ci(wrong, len(results)):<20}"
                f"{_mean_sd([r.seconds for r in hit]):<20}"
                f"{_mean_sd([r.canary_requests for r in hit]):<19}"
                f"{_mean_sd([r.canary_errors for r in hit])}"
            )
        print()


if __name__ == "__main__":
    main()
