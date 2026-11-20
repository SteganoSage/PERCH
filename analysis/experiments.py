"""Simulation experiments: four questions, a thousand rollouts per cell, results saved as CSV.

E1 modes        Every scenario under every decision mode: the headline comparison.
E2 noise        How the background error rate both versions share drives false rollbacks.
E3 fault size   How small a fault each mode reliably catches, and at what blast radius.
E4 bake time    The ramp's bake time: how fast good canaries ship vs how far bad ones get.

Every run goes through analysis/simulate_rollouts.py, which drives the controller's real decision
and ramp code. Each cell gets its own seed derived from (base seed, experiment, cell index), so a
rerun reproduces every number exactly, however the cells are spread over processes.

Output, in results/experiments/simulation/:
- <experiment>.csv: one row per cell (parameters, outcome counts and rates with 95% Wilson
  intervals, and mean / sd / median / p90 of time to outcome, canary requests and canary errors
  served, over the runs that reached the expected outcome);
- summary.md: the same as compact tables; meta.json: what produced them.
Figures come from analysis/plot_experiments.py.

Usage:
    python analysis/experiments.py                       # all four, ~1 minute on 16 cores
    python analysis/experiments.py --only e2 e3 --runs 300
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))

from config import ControllerConfig, RampConfig  # noqa: E402
from simulate_rollouts import MODES, SCENARIOS, Scenario, simulate  # noqa: E402
from stats import wilson_interval, z_two_tailed  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "experiments" / "simulation"
BASE_SEED = 20261120
RPS = 50.0
MAX_SECONDS = 600.0
DEFAULT_BACKGROUND = 0.01
DEFAULT_BAKE = 25

SCENARIO = {s.name: s for s in SCENARIOS}


@dataclass(frozen=True)
class Cell:
    experiment: str
    param: str  # the swept parameter's name
    value: float  # and its value in this cell
    mode: str
    scenario: Scenario
    background: float = DEFAULT_BACKGROUND
    bake: int = DEFAULT_BAKE


def e1_modes() -> list[Cell]:
    return [Cell("e1_modes", "scenario", i, m, s) for i, s in enumerate(SCENARIOS) for m in MODES]


def e2_noise() -> list[Cell]:
    levels = (0.0, 0.0025, 0.005, 0.01, 0.02, 0.03, 0.05)
    return [
        Cell("e2_noise", "background_error", b, m, SCENARIO[name], background=b)
        for b in levels
        for name in ("good", "bad-error")
        for m in MODES
    ]


def e3_fault_size() -> list[Cell]:
    sizes = (0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3)
    return [
        Cell("e3_fault_size", "fault_size", f, m, Scenario(f"fault+{f:g}", "rollback", f))
        for f in sizes
        for m in MODES
    ]


def e4_bake() -> list[Cell]:
    bakes = (10, 15, 20, 25, 35, 50)
    return [
        Cell("e4_bake", "bake_seconds", b, m, SCENARIO[name], bake=b)
        for b in bakes
        for name in ("good", "bad-error", "bad-subtle")
        for m in MODES
    ]


EXPERIMENTS = {"e1": e1_modes, "e2": e2_noise, "e3": e3_fault_size, "e4": e4_bake}


def _summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "sd": None, "p50": None, "p90": None}
    arr = np.asarray(values, dtype=float)
    return {
        "mean": float(arr.mean()),
        "sd": float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
    }


def run_cell(args: tuple[Cell, int, int]) -> dict:
    """All runs of one cell. Top-level so worker processes can pickle it."""
    cell, runs, seed = args
    cfg = ControllerConfig(ramp=RampConfig(bake_seconds=cell.bake))
    rng = np.random.default_rng(seed)
    results = [
        simulate(cell.mode, cell.scenario, cell.background, cfg, RPS, rng, MAX_SECONDS)  # type: ignore[arg-type]
        for _ in range(runs)
    ]
    expect = cell.scenario.expect
    hit = [r for r in results if r.outcome == expect]
    wrong = sum(1 for r in results if r.outcome not in (expect, "timeout"))
    timeout = sum(1 for r in results if r.outcome == "timeout")
    z = z_two_tailed(0.05)
    row: dict = {
        "experiment": cell.experiment,
        "param": cell.param,
        "value": cell.value,
        "mode": cell.mode,
        "scenario": cell.scenario.name,
        "expect": expect,
        "background_error": cell.background,
        "bake_seconds": cell.bake,
        "seed": seed,
        "runs": runs,
        "expected": len(hit),
        "wrong": wrong,
        "timeout": timeout,
    }
    for name, k in (("expected", len(hit)), ("wrong", wrong)):
        lo, hi = wilson_interval(k, runs, z)
        row |= {f"{name}_rate": k / runs, f"{name}_lo": lo, f"{name}_hi": hi}
    for metric, values in (
        ("seconds", [r.seconds for r in hit]),
        ("canary_requests", [r.canary_requests for r in hit]),
        ("canary_errors", [r.canary_errors for r in hit]),
    ):
        row |= {f"{metric}_{k}": v for k, v in _summary(values).items()}
    return row


def run_experiment(name: str, runs: int, pool: ProcessPoolExecutor) -> list[dict]:
    cells = EXPERIMENTS[name]()
    seeds = [
        int(np.random.SeedSequence([BASE_SEED, int(name[1:]), i]).generate_state(1)[0])
        for i in range(len(cells))
    ]
    return list(pool.map(run_cell, [(c, runs, s) for c, s in zip(cells, seeds, strict=True)]))


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    """Rows with numbers parsed back (empty cells -> None)."""

    def parse(v: str) -> object:
        if v == "":
            return None
        try:
            return float(v)
        except ValueError:
            return v

    with path.open(encoding="utf-8") as fh:
        return [{k: parse(v) for k, v in row.items()} for row in csv.DictReader(fh)]


def _pct(rate: float, lo: float, hi: float) -> str:
    return f"{100 * rate:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}]"


def _param(row: dict) -> str:
    return "-" if row["param"] == "scenario" else f"{row['param']}={row['value']:g}"


def _num(row: dict, metric: str) -> str:
    mean, sd = row[f"{metric}_mean"], row[f"{metric}_sd"]
    return "-" if mean is None else f"{mean:.1f} ± {sd:.1f}"


def markdown(name: str, rows: Iterable[dict]) -> str:
    rows = list(rows)
    title = {
        "e1_modes": "E1 · every scenario under every mode (1% background, 25s bake)",
        "e2_noise": "E2 · background error rate (25s bake)",
        "e3_fault_size": "E3 · fault size: extra error rate on the canary (1% background)",
        "e4_bake": "E4 · bake time per ramp stage (1% background)",
    }[name]
    lines = [
        f"### {title}",
        "",
        "| scenario | param | mode | expected outcome | wrong outcome | timed out "
        "| time to outcome s | canary requests | canary errors served |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['scenario']} | {_param(r)} | {r['mode']} "
            f"| {_pct(r['expected_rate'], r['expected_lo'], r['expected_hi'])} "
            f"| {_pct(r['wrong_rate'], r['wrong_lo'], r['wrong_hi'])} | {int(r['timeout'])} "
            f"| {_num(r, 'seconds')} | {_num(r, 'canary_requests')} | {_num(r, 'canary_errors')} |"
        )
    return "\n".join(lines)


def write_summary() -> None:
    parts = [
        "# Simulation experiments",
        "",
        "Generated by `analysis/experiments.py` (see `meta.json`). Rates carry 95% Wilson "
        "intervals; time and requests are mean ± sd over runs that reached the expected outcome.",
    ]
    for name in ("e1_modes", "e2_noise", "e3_fault_size", "e4_bake"):
        path = OUT / f"{name}.csv"
        if path.exists():
            parts += ["", markdown(name, read_csv(path))]
    (OUT / "summary.md").write_text("\n".join(parts) + "\n", encoding="utf-8")


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "controller", "analysis"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return out.stdout.strip() + (" (+ uncommitted changes)" if dirty else "")
    except OSError:
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", nargs="*", choices=list(EXPERIMENTS), default=list(EXPERIMENTS))
    parser.add_argument("--runs", type=int, default=1000, help="rollouts per cell")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for name in args.only:
            started = datetime.now(tz=UTC)
            rows = run_experiment(name, args.runs, pool)
            full = rows[0]["experiment"]
            write_csv(OUT / f"{full}.csv", rows)
            took = (datetime.now(tz=UTC) - started).total_seconds()
            print(f"{full}: {len(rows)} cells × {args.runs} runs in {took:.0f}s", flush=True)

    meta_path = OUT / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    defaults = ControllerConfig()
    meta |= {
        "generated": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "base_seed": BASE_SEED,
        "rps": RPS,
        "max_seconds": MAX_SECONDS,
        "runs_per_cell": {**meta.get("runs_per_cell", {}), **{n: args.runs for n in args.only}},
        "defaults": {
            "background_error": DEFAULT_BACKGROUND,
            "bake_seconds": DEFAULT_BAKE,
            "stages": list(defaults.ramp.stages),
            "poll_interval_seconds": defaults.poll_interval_seconds,
            "alpha": defaults.decision.alpha,
            "beta": defaults.decision.beta,
            "min_samples_per_window": defaults.decision.min_samples_per_window,
            "latency_tolerance": defaults.decision.latency_tolerance,
            "sequential_min_effect": defaults.decision.sequential_min_effect,
        },
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    write_summary()
    print(f"wrote {OUT.relative_to(ROOT)}/", flush=True)


if __name__ == "__main__":
    main()
