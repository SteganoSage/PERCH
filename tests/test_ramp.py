"""Ramp state machine tests. No Docker required.

These tests verify the progressive rollout state machine that manages
traffic weights and ensures proper bake times before advancing.
"""

from __future__ import annotations

from config import RampConfig
from decision import Decision
from ramp import apply_decision, initial_state


CFG = RampConfig(stages=(5, 10, 25, 50, 100), bake_seconds=25)


def _decision(action: str) -> Decision:
    """Helper to create a Decision object for testing.

    Args:
        action: The action to test ("advance", "rollback", "hold", "promote")

    Returns:
        Decision object with minimal context
    """
    return Decision(action=action, reason="test", evidence={})  # type: ignore[arg-type]


def test_starts_at_first_stage() -> None:
    """Initial state should be at the first ramp stage (5% canary)."""
    state = initial_state(CFG, now=0.0)
    assert state.canary_weight == 5
    assert state.stable_weight == 95


def test_does_not_advance_before_bake() -> None:
    """Should not advance until the bake time has elapsed.

    Even if the decision is "advance", the state machine waits for
    bake_seconds before actually moving to the next stage.
    """
    state = initial_state(CFG, now=0.0)
    state, action = apply_decision(state, _decision("advance"), CFG, now=10.0)
    assert action == "hold"
    assert state.canary_weight == 5


def test_advances_after_bake() -> None:
    """Should advance to the next stage after bake time elapses.

    Once the current stage has been healthy for bake_seconds,
    an "advance" decision will move to the next weight.
    """
    state = initial_state(CFG, now=0.0)
    state, action = apply_decision(state, _decision("advance"), CFG, now=25.0)
    assert action == "advance"
    assert state.canary_weight == 10


def test_rollback_is_terminal() -> None:
    """Once rolled back, the state machine stays at 0% permanently.

    A rollback is terminal to prevent a bad canary from sneaking back
    into the rollout after a later clean window.
    """
    state = initial_state(CFG, now=0.0)
    state, action = apply_decision(state, _decision("rollback"), CFG, now=5.0)
    assert action == "rollback"
    assert state.canary_weight == 0
    state, action = apply_decision(state, _decision("advance"), CFG, now=100.0)
    assert action == "hold"
    assert state.canary_weight == 0
    assert state.rolled_back


def test_promote_after_final_stage_bakes() -> None:
    """After the final stage (100%) bakes cleanly, should promote.

    Promotion is a terminal state meaning the canary is fully rolled out.
    """
    state = initial_state(CFG, now=0.0)
    now = 0.0
    for expected in (10, 25, 50, 100):
        now += CFG.bake_seconds
        state, action = apply_decision(state, _decision("advance"), CFG, now)
        assert action == "advance"
        assert state.canary_weight == expected
    now += CFG.bake_seconds
    state, action = apply_decision(state, _decision("advance"), CFG, now)
    assert action == "promote"
    assert state.promoted
    assert state.canary_weight == 100
