"""Evaluation tests: the detectors' claims, checked by simulation. No Docker.

Each test runs the controller's real decision and ramp code through analysis/simulate_rollouts.py
with a fixed seed, so it is deterministic: it passes or fails the same way every time, and a change
to the decision logic that breaks one of these claims fails it. `make eval`, ~15s.

Rates are judged with 95% Wilson intervals rather than raw point estimates where a claim is "at
most X": a rate whose interval reaches below X is not evidence of breaking it.
"""

from __future__ import annotations

from statistics import mean, stdev

import numpy as np
import pytest
from config import RampConfig
from simulate_rollouts import SCENARIOS, ControllerConfig, RunResult, Scenario, simulate
from stats import wilson_interval, z_two_tailed

CFG = ControllerConfig()
ALPHA = CFG.decision.alpha
SCENARIO = {s.name: s for s in SCENARIOS}
Z = z_two_tailed(0.05)


def rollouts(
    mode: str,
    scenario: Scenario,
    runs: int,
    seed: int,
    background: float = 0.01,
    cfg: ControllerConfig = CFG,
) -> list[RunResult]:
    rng = np.random.default_rng(seed)
    return [simulate(mode, scenario, background, cfg, 50.0, rng, 600.0) for _ in range(runs)]  # type: ignore[arg-type]


def share(results: list[RunResult], outcome: str) -> tuple[float, float, float]:
    """(rate, Wilson low, Wilson high) of runs ending in `outcome`."""
    k = sum(1 for r in results if r.outcome == outcome)
    lo, hi = wilson_interval(k, len(results), Z)
    return k / len(results), lo, hi


@pytest.mark.parametrize("background", [0.0, 0.01, 0.02, 0.05])
def test_tier2_keeps_false_rollbacks_within_budget(background: float) -> None:
    """tier2's design guarantee: a good canary is wrongly rolled back at most alpha of the time,
    whatever error rate both versions share. (E2: 0.0% at 0%, 1.4% at 1%, 5.0% at 5%.)"""
    results = rollouts("tier2_sprt", SCENARIO["good"], 300, seed=101, background=background)
    rate, lo, _ = share(results, "rollback")
    assert lo <= ALPHA, f"{rate:.1%} wrongly rolled back"
    if background <= 0.02:
        assert rate <= ALPHA


def test_naive_threshold_breaks_the_budget_under_realistic_noise() -> None:
    """Why the statistical modes exist: at 1% background errors the naive rule wrongly rolls back
    significantly more than 5% of good canaries (E1/E2: ~12%)."""
    rate, lo, _ = share(rollouts("naive_threshold", SCENARIO["good"], 300, seed=102), "rollback")
    assert lo > ALPHA, f"only {rate:.1%}"


def test_tier1_peeking_costs_it_its_nominal_alpha() -> None:
    """tier1 re-runs a 5% test every poll on overlapping windows, so its rollout-level false
    rollbacks exceed what tier2's budgeted tests allow (E1: ~8% vs ~1%)."""
    tier1, _, _ = share(rollouts("tier1", SCENARIO["good"], 400, seed=103), "rollback")
    tier2, _, _ = share(rollouts("tier2_sprt", SCENARIO["good"], 400, seed=103), "rollback")
    assert tier1 > 2 * tier2


@pytest.mark.parametrize("mode", ["naive_threshold", "tier1", "tier2_sprt"])
@pytest.mark.parametrize("name", ["bad-error", "bad-latency", "bad-partial", "bad-slow-onset"])
def test_every_mode_catches_every_live_fault(mode: str, name: str) -> None:
    """No bad canary in the live scenario set ever reaches 100%."""
    results = rollouts(mode, SCENARIO[name], 150, seed=104)
    assert all(r.outcome == "rollback" for r in results)


@pytest.mark.parametrize("name", ["bad-error", "bad-latency", "bad-partial"])
def test_tier2_reaches_fewer_users_than_tier1_on_loud_faults(name: str) -> None:
    """No sample-size floor: strong evidence ends the test early (E1: ~18 vs 50 on bad-error)."""
    tier1 = mean(r.canary_requests for r in rollouts("tier1", SCENARIO[name], 200, seed=105))
    tier2 = mean(r.canary_requests for r in rollouts("tier2_sprt", SCENARIO[name], 200, seed=105))
    assert tier2 < tier1 * 0.7


def test_tier1_blast_radius_is_set_by_its_sample_floor() -> None:
    """tier1 cannot decide before 40 canary requests: on a loud fault its blast radius sits at
    that floor plus one poll, with almost no spread."""
    results = rollouts("tier1", SCENARIO["bad-error"], 200, seed=106)
    requests = [r.canary_requests for r in results]
    assert 40 <= mean(requests) <= 55
    assert stdev(requests) < 3


def test_tier2_detects_its_designed_effect_size() -> None:
    """Each tier2 test is built to catch a canary 5 points worse than stable (sequential_min_effect)
    with miss probability beta per stage; over five stages that is near-certain detection."""
    subtle = Scenario("subtle", "rollback", CFG.decision.sequential_min_effect)
    rate, _, _ = share(rollouts("tier2_sprt", subtle, 200, seed=107), "rollback")
    assert rate >= 0.95


def test_tier1_stalls_when_the_bake_is_too_short_for_its_floor() -> None:
    """A 15s bake gives the 5% stage ~37 canary requests per window, below tier1's 40: it never
    decides. tier2 has no floor and still ships the good canary (E4)."""
    short = ControllerConfig(ramp=RampConfig(bake_seconds=15))
    tier1 = rollouts("tier1", SCENARIO["good"], 20, seed=108, cfg=short)
    tier2 = rollouts("tier2_sprt", SCENARIO["good"], 100, seed=108, cfg=short)
    assert all(r.outcome == "timeout" for r in tier1)
    assert share(tier2, "promote")[0] >= 0.95


def test_simulation_is_reproducible() -> None:
    """Same seed, same numbers: every figure and table can be regenerated exactly."""
    first = rollouts("tier2_sprt", SCENARIO["bad-partial"], 30, seed=109)
    again = rollouts("tier2_sprt", SCENARIO["bad-partial"], 30, seed=109)
    assert first == again
