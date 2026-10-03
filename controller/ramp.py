"""Progressive rollout state machine.

The decision module says hold / advance / rollback. This module turns that
into a canary weight, and refuses to re-advance after a rollback.
"""

from __future__ import annotations

from dataclasses import dataclass

from config import RampConfig
from decision import Action, Decision


@dataclass
class RampState:
    canary_weight: int
    stage_index: int
    stage_entered_at: float
    rolled_back: bool = False
    promoted: bool = False

    @property
    def stable_weight(self) -> int:
        return 100 - self.canary_weight


def initial_state(cfg: RampConfig, now: float) -> RampState:
    """Start the run at the first ramp stage (typically 5% canary)."""
    return RampState(
        canary_weight=cfg.stages[0],
        stage_index=0,
        stage_entered_at=now,
    )


def apply_decision(
    state: RampState, decision: Decision, cfg: RampConfig, now: float
) -> tuple[RampState, Action]:
    """Return (new_state, action_that_was_taken).

    `advance` from the evaluator is a *permission* to move, not an order:
    we only increment the stage after bake_seconds at the current weight
    with no rollback in between.
    """
    if state.rolled_back:
        return state, "hold"
    if state.promoted:
        # Stay at 100%, but keep watching: a late fault can still roll back.
        if decision.action == "rollback":
            return _rollback(state), "rollback"
        return state, "hold"

    if decision.action == "rollback":
        return _rollback(state), "rollback"

    if decision.action in ("advance", "promote"):
        baked = (now - state.stage_entered_at) >= cfg.bake_seconds
        if not baked:
            return state, "hold"
        if state.stage_index >= len(cfg.stages) - 1:
            # Already at the last stage and it baked cleanly.
            promoted = RampState(
                canary_weight=100,
                stage_index=state.stage_index,
                stage_entered_at=state.stage_entered_at,
                promoted=True,
            )
            return promoted, "promote"
        next_index = state.stage_index + 1
        nxt = RampState(
            canary_weight=cfg.stages[next_index],
            stage_index=next_index,
            stage_entered_at=now,
        )
        return nxt, "advance"

    return state, "hold"


def _rollback(state: RampState) -> RampState:
    return RampState(
        canary_weight=0,
        stage_index=state.stage_index,
        stage_entered_at=state.stage_entered_at,
        rolled_back=True,
        promoted=False,
    )
