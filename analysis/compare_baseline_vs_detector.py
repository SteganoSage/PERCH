"""The central comparison: the naive threshold against the statistical detectors, both ways.

A detector is only better if it is better in both directions, so for every decision mode this
measures:
- the bad-error canary: how many requests reach it and how fast it is rolled back (live,
  --repeats runs per mode);
- the good canary: whether it is promoted (live), and how often it is wrongly rolled back
  (simulated: that is a rate of a few percent, which a handful of live runs cannot measure, so the
  simulator runs the same decision code --sim-runs times).
Live and simulated numbers sit side by side, which checks the simulator against reality too.

Usage:
    python analysis/compare_baseline_vs_detector.py --repeats 5          # ~1 hour
    python analysis/compare_baseline_vs_detector.py --resume results/campaigns/<dir>
    python analysis/compare_baseline_vs_detector.py --summarize results/campaigns/<dir>

--summarize also works on a full run_campaign.py folder: it only reads the good and bad-error rows.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from run_campaign import CAMPAIGNS, MODES, plan, read_rows, run_campaign, stat
from simulate_rollouts import SCENARIOS as SIM_SCENARIOS
from simulate_rollouts import ControllerConfig, rate_ci, simulate

SCENARIOS = ("good", "bad-error")


def _simulated(mode: str, name: str, runs: int, seed: int) -> list:
    scenario = next(s for s in SIM_SCENARIOS if s.name == name)
    rng = np.random.default_rng(seed)
    cfg = ControllerConfig()
    return [simulate(mode, scenario, 0.01, cfg, 50.0, rng, 600.0) for _ in range(runs)]


def comparison(rows: list[dict], sim_runs: int, seed: int) -> str:
    lines = [
        "| mode | bad-error: canary requests (live) | (sim) "
        "| bad-error: seconds to rollback (live) | (sim) "
        "| good: promoted (live) | good: wrongly rolled back (sim) |",
        "|---|---|---|---|---|---|---|",
    ]
    headline = {}
    for mode in MODES:
        bad = [r for r in rows if r["scenario"] == "bad-error" and r["mode"] == mode and r["ok"]]
        good = [r for r in rows if r["scenario"] == "good" and r["mode"] == mode]
        sim_bad = [
            r for r in _simulated(mode, "bad-error", sim_runs, seed) if r.outcome == "rollback"
        ]
        sim_good = _simulated(mode, "good", sim_runs, seed)
        wrong = sum(1 for r in sim_good if r.outcome == "rollback")
        live_requests = stat([r["canary_requests"] for r in bad])
        sim_requests = stat([r.canary_requests for r in sim_bad])
        lines.append(
            f"| {mode} | {_short(live_requests)} | {_short(sim_requests)} "
            f"| {_short(stat([r['seconds'] for r in bad]))} "
            f"| {_short(stat([r.seconds for r in sim_bad]))} "
            f"| {sum(1 for r in good if r['ok'])}/{len(good)} "
            f"| {rate_ci(wrong, len(sim_good)).strip()} |"
        )
        headline[mode] = (live_requests, sim_requests, 100.0 * wrong / len(sim_good))

    naive = headline["naive_threshold"]
    notes = [""]
    for mode in MODES[1:]:
        live, sim, fp = headline[mode]
        notes.append(
            f"- **{mode} vs naive**: bad-error blast radius {_mean(live)} vs "
            f"{_mean(naive[0])} canary requests live ({_mean(sim)} vs {_mean(naive[1])} "
            f"simulated); good canaries wrongly rolled back {fp:.1f}% vs {naive[2]:.1f}% "
            "(simulated)."
        )
    return "\n".join(lines + notes)


def _mean(s) -> str:
    return "-" if s.n == 0 else f"{s.mean:.1f}"


def _short(s) -> str:
    return "-" if s.n == 0 else f"{s.mean:.1f} ± {s.sd:.1f} (n={s.n})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--sim-runs", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20261031)
    parser.add_argument("--resume", type=Path, help="continue an interrupted comparison")
    parser.add_argument("--summarize", type=Path, help="only rebuild comparison.md")
    args = parser.parse_args()

    if args.summarize or args.resume:
        campaign = (args.summarize or args.resume).resolve()
    else:
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        campaign = CAMPAIGNS / f"comparison_{stamp}"
        campaign.mkdir(parents=True, exist_ok=True)
        spec = {
            "started": stamp,
            "scenarios": list(SCENARIOS),
            "modes": list(MODES),
            "repeats": args.repeats,
            "cells": plan(list(SCENARIOS), list(MODES), args.repeats),
        }
        (campaign / "campaign.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    if not args.summarize:
        spec = json.loads((campaign / "campaign.json").read_text(encoding="utf-8"))
        run_campaign(campaign, [tuple(c) for c in spec["cells"]])  # type: ignore[misc]

    text = comparison(read_rows(campaign), args.sim_runs, args.seed)
    note = (
        "Live: mean ± sd over runs that rolled back. Simulated: "
        f"{args.sim_runs} rollouts per cell (analysis/simulate_rollouts.py, seed {args.seed}), "
        "same decision code, 1% background errors.\n\n"
    )
    (campaign / "comparison.md").write_text(note + text + "\n", encoding="utf-8")
    print("\n" + text)


if __name__ == "__main__":
    main()
