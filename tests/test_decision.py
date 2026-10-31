"""Unit tests for controller/decision.py. No Docker required.

These tests verify the decision logic by feeding synthetic MetricSamples
and asserting exact actions. The decision module is pure (no I/O), making it
easy to test comprehensively.
"""

from __future__ import annotations

import math

import pytest
from config import DecisionConfig
from decision import (
    CounterSnapshot,
    MetricSample,
    SprtState,
    evaluate,
    evaluate_naive_threshold,
    evaluate_tier1,
    evaluate_tier2_sprt,
)

CFG = DecisionConfig()
STAGES = 5


def sample(
    service: str,
    n: int,
    errors: int,
    p95: float = 40.0,
    buckets: dict[float, int] | None = None,
) -> MetricSample:
    """Helper to create a MetricSample for testing.

    Args:
        service: Service name ("stable" or "canary")
        n: Total request count
        errors: Error count (HTTP 500 responses)
        p95: p95 latency in milliseconds
        buckets: Cumulative latency histogram (seconds -> count); none by default

    Returns:
        MetricSample for use in decision evaluation
    """
    return MetricSample(
        service=service,
        window_start=0.0,
        window_end=1.0,
        request_count=n,
        error_count=errors,
        p95_latency_ms=p95,
        latency_buckets=buckets or {},
    )


def histogram(n: int, slow: int = 0) -> dict[float, int]:
    """Cumulative buckets with `slow` of `n` requests above 150ms and the rest under 50ms."""
    fast = n - slow
    return {0.05: fast, 0.1: fast, 0.15: fast, math.inf: n}


# --- Tier 1 ---------------------------------------------------------------------------------


def test_clearly_fine_canary_advances() -> None:
    """A healthy canary with identical metrics to stable should advance."""
    decision = evaluate(sample("canary", 200, 0), sample("stable", 200, 0), CFG, "tier1")
    assert decision.action == "advance"


def test_clearly_broken_error_rate_rolls_back() -> None:
    """A canary with significantly higher error rate should roll back.

    30% error rate (60/200) vs 0% is statistically significant.
    """
    decision = evaluate(sample("canary", 200, 60), sample("stable", 200, 0), CFG, "tier1")
    assert decision.action == "rollback"
    assert "error" in decision.reason.lower()


def test_clearly_broken_latency_rolls_back() -> None:
    """A canary with significantly higher latency should roll back.

    400ms p95 vs 40ms p95 is 10x, exceeding the 1.5x tolerance.
    """
    decision = evaluate(
        sample("canary", 200, 0, p95=400),
        sample("stable", 200, 0, p95=40),
        CFG,
        "tier1",
    )
    assert decision.action == "rollback"
    assert "p95" in decision.reason.lower()


def test_too_few_samples_holds_even_if_ratio_looks_bad() -> None:
    """Insufficient samples should always hold, regardless of metrics.

    Even 100% error rate on 10 samples is noise, not signal.
    """
    decision = evaluate_tier1(sample("canary", 10, 10), sample("stable", 200, 0), CFG)
    assert decision.action == "hold"
    assert "holding" in decision.reason.lower()


def test_borderline_error_rate_holds() -> None:
    """Worse-looking but not significantly worse should hold, not advance or roll back.

    5/200 = 2.5% vs 2/200 = 1%: the point estimate is 1.5 points worse, but even a pooled
    two-proportion test can't tell them apart (z = 1.14), so the honest answer is "not sure yet".
    """
    decision = evaluate_tier1(sample("canary", 200, 5), sample("stable", 200, 2), CFG)
    assert decision.action == "hold"
    assert decision.evidence["signals"]["error_rate"]["verdict"] == "inconclusive"


def test_tier1_is_stricter_than_a_pooled_test() -> None:
    """12/200 vs 2/200 is significant to a pooled z-test (z = 2.72) but tier1's non-overlap
    criterion still holds: deliberately conservative, since tier1 re-tests every poll."""
    decision = evaluate_tier1(sample("canary", 200, 12), sample("stable", 200, 2), CFG)
    assert decision.action == "hold"


def test_fusion_logs_every_signal_not_just_the_one_that_fired() -> None:
    """A latency-only fault rolls back, and the log still shows the error signal was fine."""
    decision = evaluate_tier1(
        sample("canary", 200, 2, p95=400), sample("stable", 1000, 10, p95=40), CFG
    )
    assert decision.action == "rollback"
    signals = decision.evidence["signals"]
    assert signals["error_rate"]["verdict"] == "ok"
    assert signals["latency"]["verdict"] == "worse"
    assert "error_delta_ci" in signals["error_rate"]
    assert decision.evidence["fusion"]


def test_tier1_full_canary_uses_absolute_ceiling() -> None:
    """At 100% canary stable has no traffic: errors are judged against the absolute ceiling,
    and the missing latency baseline does not block promotion."""
    no_stable = sample("stable", 0, 0, p95=0)
    bad = evaluate_tier1(sample("canary", 1000, 300), no_stable, CFG)
    assert bad.action == "rollback"
    fine = evaluate_tier1(sample("canary", 1000, 10), no_stable, CFG)
    assert fine.action == "advance"
    assert fine.evidence["signals"]["latency"]["verdict"] == "no_baseline"


# --- naive baseline -------------------------------------------------------------------------


def test_naive_fires_on_noisy_sample_that_tier1_holds() -> None:
    """Naive threshold fires on noise that Tier 1 correctly holds on.

    This demonstrates why statistical methods are better than point estimates.
    """
    noisy_canary = sample("canary", 10, 2)  # 20% errors on 10 requests
    stable = sample("stable", 10, 0)
    naive = evaluate_naive_threshold(noisy_canary, stable, CFG)
    tier1 = evaluate_tier1(noisy_canary, stable, CFG)
    assert naive.action == "rollback"
    assert tier1.action == "hold"


def test_naive_holds_with_no_canary_traffic() -> None:
    """Zero canary requests is no evidence either way; the baseline must not advance on it."""
    decision = evaluate_naive_threshold(sample("canary", 0, 0), sample("stable", 200, 0), CFG)
    assert decision.action == "hold"


def test_dispatch_naive_mode() -> None:
    """Verify the evaluate function dispatches to the correct mode."""
    decision = evaluate(sample("canary", 200, 0), sample("stable", 200, 0), CFG, "naive_threshold")
    assert decision.action == "advance"


def test_stateless_dispatch_refuses_tier2() -> None:
    with pytest.raises(ValueError):
        evaluate(sample("canary", 200, 0), sample("stable", 200, 0), CFG, "tier2_sprt")


# --- Tier 2 ---------------------------------------------------------------------------------

STABLE = sample("stable", 1100, 11, p95=45.0, buckets=histogram(1100))


def snap(requests: int, errors: int = 0, slow: int = 0) -> CounterSnapshot:
    return CounterSnapshot(requests, errors, histogram(requests, slow))


def tier2(
    counters: CounterSnapshot,
    state: SprtState | None,
    stage_index: int = 0,
    stable: MetricSample = STABLE,
):
    canary = sample("canary", counters.requests, counters.errors)
    return evaluate_tier2_sprt(canary, stable, counters, state, stage_index, STAGES, CFG)


def test_tier2_first_poll_only_sets_the_baseline() -> None:
    decision, state = tier2(snap(0), None)
    assert decision.action == "hold"
    assert state is not None and state.errors.n == 0


def test_tier2_loud_error_fault_rolls_back_after_one_poll() -> None:
    """6 errors in 12 canary requests against stable's 1% is decisive: no 40-request floor."""
    _, state = tier2(snap(0), None)
    decision, state = tier2(snap(12, errors=6), state)
    assert decision.action == "rollback"
    assert decision.evidence["signals"]["error_rate"]["verdict"] == "worse"
    assert state.errors.n == 12


def test_tier2_healthy_canary_accepts_h0_and_advances() -> None:
    """~45 clean requests carry the LLR past the accept boundary for both signals."""
    _, state = tier2(snap(0), None)
    decision, state = tier2(snap(50), state)
    assert decision.action == "advance"
    assert state.errors.verdict == "h0" and state.slow.verdict == "h0"


def test_tier2_too_little_evidence_holds() -> None:
    _, state = tier2(snap(0), None)
    decision, _ = tier2(snap(20), state)
    assert decision.action == "hold"
    assert "holding" in decision.reason


def test_tier2_evidence_accumulates_across_polls() -> None:
    """Three polls of ~17 clean requests reach the same verdict one poll of 50 would."""
    _, state = tier2(snap(0), None)
    for total in (17, 34):
        decision, state = tier2(snap(total), state)
        assert decision.action == "hold"
    decision, state = tier2(snap(51), state)
    assert decision.action == "advance"
    assert state.errors.n == 51


def test_tier2_new_stage_starts_fresh_tests() -> None:
    """A stage's accepted H0 does not carry into the next stage; the new tests count traffic
    from the previous poll, which already ran at the new weight."""
    _, state = tier2(snap(0), None)
    _, state = tier2(snap(50), state)
    assert state.errors.verdict == "h0"
    decision, state = tier2(snap(60), state, stage_index=1)
    assert decision.action == "hold"
    assert state.stage_index == 1
    assert state.errors.n == 10 and state.errors.verdict == "continue"


def test_tier2_latency_fault_rolls_back_on_slow_requests() -> None:
    """Every canary request slower than the bound (1.5x stable p95 -> 100ms bucket)."""
    _, state = tier2(snap(0), None)
    decision, state = tier2(snap(12, slow=12), state)
    assert decision.action == "rollback"
    assert state.latency_bound == 0.1
    signals = decision.evidence["signals"]
    assert signals["latency"]["verdict"] == "worse"
    assert signals["error_rate"]["verdict"] == "inconclusive"


def test_tier2_full_canary_keeps_previous_references() -> None:
    """At 100% stable gets no traffic; the last stage's reference rates carry over."""
    _, state = tier2(snap(0), None, stage_index=3)
    reference = state.errors.p0
    no_stable = sample("stable", 0, 0, p95=0)
    decision, state = tier2(snap(600), state, stage_index=4, stable=no_stable)
    assert decision.action == "advance"
    assert state.stage_index == 4 and state.errors.p0 == reference


def test_tier2_waits_for_a_stable_baseline() -> None:
    decision, state = tier2(snap(0), None, stable=sample("stable", 10, 0))
    assert decision.action == "hold"
    assert state is None


def test_counter_reset_counts_from_zero() -> None:
    """A restarted canary's counters drop back to 0; everything since then is new."""
    assert snap(5, errors=1).since(snap(500, errors=20)).requests == 5
    assert snap(530, errors=21).since(snap(500, errors=20)).errors == 1


def test_sequential_alpha_splits_budget_over_every_test() -> None:
    assert CFG.sequential_alpha(STAGES) == pytest.approx(0.05 / (2 * 5))
