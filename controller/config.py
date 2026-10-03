"""Every magic number in PERCH, in one place, each with its justification (CLAUDE.md §6).

Values here are provisional defaults chosen for a laptop-scale demo; Phase 4/5 may retune them,
but the rule stands — nothing numeric may appear elsewhere in the controller without a line here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

DecisionMode = Literal["naive_threshold", "tier1", "tier2_sprt"]


@dataclass(frozen=True)
class DecisionConfig:
    """Parameters of the rollback decision itself."""

    # Statistical detector (Tier 1) --------------------------------------------------------
    alpha: float = 0.05
    """False-positive rate for the two-proportion test. 5% is the conventional choice; a rolled-back
    good release costs a deploy cycle, so we are not paying for anything stricter."""

    beta: float = 0.10
    """False-negative rate, used by the Tier 2 SPRT boundaries. Missing a genuinely bad canary is
    the more expensive error, hence beta < alpha in effect (10% miss vs. 5% false alarm)."""

    min_samples_per_window: int = 40
    """No decision other than `hold` below this many canary requests in the window. At 5% of 50 rps
    a 25s bake window sees ~62 canary requests, so the first stage can actually decide. 40 is enough
    to see a 30% vs 0% error spike clearly, and small enough that a 2-error blip on 20 requests
    (10%) is still refused as noise."""

    latency_tolerance: float = 1.5
    """Canary p95 may be up to 1.5x stable p95 before the latency signal fires. Below this, normal
    JIT/cache warm-up on a freshly started container is indistinguishable from real regression."""

    # Naive baseline (kept for the comparison in CLAUDE.md §5.7) ----------------------------
    naive_error_rate_delta: float = 0.05
    """Baseline rule: roll back when canary error rate exceeds stable's by 5 percentage points.
    Deliberately a round, undefensible number — that is the point of the comparison."""

    naive_latency_multiplier: float = 1.5
    """Baseline latency rule, matched to `latency_tolerance` so the comparison isolates the effect
    of the *statistics*, not of a differently-tuned threshold."""


@dataclass(frozen=True)
class RampConfig:
    """The progressive rollout schedule (CLAUDE.md §5.4)."""

    stages: tuple[int, ...] = (5, 10, 25, 50, 100)
    """Percent of traffic on the canary. Starts at 5% so that even a total canary failure caps the
    blast radius at ~5% of requests for one bake window; roughly doubles thereafter, which keeps the
    number of stages (and so total rollout time) small while never more than doubling exposure."""

    bake_seconds: int = 25
    """Seconds a stage must hold with no rollback before advancing. 25s at ~50 rps gives the 5%
    stage ~62 canary requests, above min_samples_per_window, so a good canary promotes in a bit
    over two minutes (5 stages) and a bad one is caught inside the first stage."""

    rollback_is_terminal: bool = True
    """Once rolled back, the state machine stays at 0% until manually reset for the next scenario
    run — a later clean window must never silently re-advance a canary that already tripped the
    alarm (CLAUDE.md §8)."""


@dataclass(frozen=True)
class ControllerConfig:
    """Loop and I/O settings."""

    poll_interval_seconds: int = 5
    """One observe->decide->act cycle per 5s. Faster than the bake time so a bad canary is caught
    mid-stage, slower than the 2s Prometheus scrape so every poll sees fresh samples."""

    proxy_reload_debounce_seconds: int = 2
    """Never reload nginx more than once per 2s even if the loop asks more often — reloads drop
    in-flight connections and would contaminate the latency we are measuring (CLAUDE.md §8)."""

    decision_log_path: str = "/results/decision_log.jsonl"
    """Append-only JSON lines; one object per decision, carrying the full evidence dict."""

    decision: DecisionConfig = field(default_factory=DecisionConfig)
    ramp: RampConfig = field(default_factory=RampConfig)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else float(raw)


def from_env() -> ControllerConfig:
    """Build config from process environment, falling back to the defaults above."""
    stages_raw = os.environ.get("RAMP_STAGES", "5,10,25,50,100")
    stages = tuple(int(x.strip()) for x in stages_raw.split(",") if x.strip())
    decision = DecisionConfig(
        alpha=_env_float("DECISION_ALPHA", 0.05),
        beta=_env_float("DECISION_BETA", 0.10),
        min_samples_per_window=_env_int("MIN_SAMPLES_PER_WINDOW", 40),
        latency_tolerance=_env_float("LATENCY_TOLERANCE", 1.5),
        naive_error_rate_delta=_env_float("NAIVE_ERROR_RATE_DELTA", 0.05),
        naive_latency_multiplier=_env_float("NAIVE_LATENCY_MULTIPLIER", 1.5),
    )
    ramp = RampConfig(
        stages=stages,
        bake_seconds=_env_int("RAMP_BAKE_SECONDS", 25),
        rollback_is_terminal=os.environ.get("ROLLBACK_IS_TERMINAL", "true").lower()
        != "false",
    )
    return ControllerConfig(
        poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 5),
        proxy_reload_debounce_seconds=_env_int("PROXY_RELOAD_DEBOUNCE_SECONDS", 2),
        decision_log_path=os.environ.get("DECISION_LOG_PATH", "/results/decision_log.jsonl"),
        decision=decision,
        ramp=ramp,
    )
