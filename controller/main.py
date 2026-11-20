"""Observe → decide → act → log.

The loop is the whole controller:

    every POLL_INTERVAL_SECONDS
        pull canary + stable MetricSamples from Prometheus
        evaluate()  (pure)
        apply_decision() to the ramp state machine
        if the weight changed: render nginx.conf and reload
        append one JSON line to /results/decision_log.jsonl
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from config import ControllerConfig, DecisionMode, from_env
from decision import Decision, SprtState, evaluate, evaluate_tier2_sprt
from metrics import PrometheusClient
from proxy_writer import ProxyWriter
from ramp import RampState, apply_decision, initial_state


def _log_line(
    state: RampState,
    canary,
    stable,
    evaluated: Decision,
    taken,
    now: float,
    elapsed: float,
) -> dict:
    """Build a complete decision log entry for observability.

    This function creates a comprehensive record of each controller decision,
    including the state before/after, metrics from both services, and the
    reasoning behind the action taken. This enables post-mortem analysis
    and debugging of rollout behavior.

    Args:
        state: Current ramp state (weights, stage, terminal flags)
        canary: MetricSample from canary service
        stable: MetricSample from stable service
        evaluated: Decision object from the evaluation logic
        taken: Action actually taken (may differ from evaluated due to bake time)
        now: Wall-clock timestamp (what Prometheus was queried at)
        elapsed: Monotonic seconds since the controller started

    Returns:
        Dictionary containing all decision context for logging
    """
    return {
        "ts": datetime.fromtimestamp(now, tz=UTC).isoformat(),
        "unix": now,
        "elapsed_s": elapsed,
        "canary_weight": state.canary_weight,
        "stable_weight": state.stable_weight,
        "rolled_back": state.rolled_back,
        "promoted": state.promoted,
        "evaluated_action": evaluated.action,
        "action": taken,
        "reason": evaluated.reason,
        "canary": {
            "n": canary.request_count,
            "errors": canary.error_count,
            "error_rate": canary.error_rate,
            "p95_ms": canary.p95_latency_ms,
        },
        "stable": {
            "n": stable.request_count,
            "errors": stable.error_count,
            "error_rate": stable.error_rate,
            "p95_ms": stable.p95_latency_ms,
        },
        "evidence": evaluated.evidence,
    }


def _write_status(path: Path, state: RampState, taken: str, reason: str) -> None:
    """Write the current controller state to a JSON file for monitoring.

    This creates a lightweight status file that external tools can poll to
    check the current state of the rollout without parsing the full decision log.

    Args:
        path: File path to write status.json
        state: Current ramp state
        taken: Action taken in this iteration
        reason: Reason for the action
    """
    path.write_text(
        json.dumps(
            {
                "canary_weight": state.canary_weight,
                "stable_weight": state.stable_weight,
                "rolled_back": state.rolled_back,
                "promoted": state.promoted,
                "last_action": taken,
                "reason": reason,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def run(cfg: ControllerConfig) -> None:
    """Main controller loop: observe metrics, decide on actions, update proxy.

    This is the heart of the PERCH system. It runs in an infinite loop,
    periodically polling Prometheus for metrics, evaluating whether to
    advance or rollback the canary, and updating nginx configuration when
    traffic weights need to change.

    The loop:
    1. Pulls metrics from Prometheus for both services
    2. Evaluates the decision logic (pure function)
    3. Applies the decision to the ramp state machine
    4. Updates nginx config if weights changed
    5. Logs the decision with full evidence
    6. Sleeps until the next poll interval

    Errors in a single tick are caught and logged but don't terminate the loop,
    ensuring the controller remains resilient to transient failures.

    Args:
        cfg: Controller configuration (poll interval, decision params, etc.)
    """
    mode: DecisionMode = os.environ.get("DECISION_MODE", "tier1")  # type: ignore[assignment]
    if mode not in ("naive_threshold", "tier1", "tier2_sprt"):
        raise SystemExit(f"invalid DECISION_MODE={mode!r}")

    # `docker stop` sends SIGTERM to PID 1, which Python ignores without a handler. Exit at once so
    # the scenario runner's stop doesn't wait out Docker's 10s kill timeout every run.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    log_path = Path(cfg.decision_log_path)
    status_path = log_path.with_name("status.json")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    prom = PrometheusClient(os.environ.get("PROMETHEUS_URL", "http://prometheus:9090"))
    writer = ProxyWriter(
        template_path=os.environ.get("TEMPLATE_PATH", "/app/nginx.conf.j2"),
        output_path=os.environ.get("NGINX_CONF_PATH", "/etc/nginx/conf.d/default.conf"),
        debounce_seconds=cfg.proxy_reload_debounce_seconds,
    )

    # Two clocks. Durations (the poll schedule, bake time, the log's elapsed_s) use the monotonic
    # clock, which never jumps. Wall-clock time is only for Prometheus query timestamps and the
    # log's ts/unix, because Prometheus stores wall-clock times. Docker Desktop's VM steps its wall
    # clock by ~2.3s whenever it resyncs with the host; timing the bake on it cost an extra poll
    # per stage and made durations read short.
    started = time.monotonic()
    state = initial_state(cfg.ramp, 0.0)
    print(
        f"controller starting mode={mode} first_weight={state.canary_weight}% "
        f"stages={list(cfg.ramp.stages)} bake={cfg.ramp.bake_seconds}s",
        flush=True,
    )
    writer.apply(state.stable_weight, state.canary_weight, force=True)

    window = cfg.ramp.bake_seconds
    sprt: SprtState | None = None
    while True:
        tick = time.monotonic()
        elapsed = tick - started
        loop_start = time.time()
        try:
            canary = prom.sample("canary", window, at=loop_start)
            stable = prom.sample("stable", window, at=loop_start)
            if mode == "tier2_sprt":
                evaluated, sprt = evaluate_tier2_sprt(
                    canary,
                    stable,
                    prom.counters("canary", at=loop_start),
                    sprt,
                    state.stage_index,
                    len(cfg.ramp.stages),
                    cfg.decision,
                )
            else:
                evaluated = evaluate(canary, stable, cfg.decision, mode)
            new_state, taken = apply_decision(state, evaluated, cfg.ramp, elapsed)
            if new_state.canary_weight != state.canary_weight:
                writer.apply(new_state.stable_weight, new_state.canary_weight)
                print(
                    f"weight {state.canary_weight}% → {new_state.canary_weight}% "
                    f"action={taken} reason={evaluated.reason}",
                    flush=True,
                )
            state = new_state
            record = _log_line(state, canary, stable, evaluated, taken, loop_start, elapsed)
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            _write_status(status_path, state, taken, evaluated.reason)
        except Exception as exc:
            print(f"controller tick failed: {exc!r}", flush=True)

        time.sleep(max(0.0, cfg.poll_interval_seconds - (time.monotonic() - tick)))


if __name__ == "__main__":
    run(from_env())
