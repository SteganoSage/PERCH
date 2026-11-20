"""Repeat live scenario runs across decision modes and tabulate them.

Each run is one `scenarios/run.py <scenario>` with DECISION_MODE set, archived as usual under
results/archive/. The campaign appends every run's summary to
results/campaigns/<name>_<stamp>/runs.jsonl as it goes, so an interrupted campaign can be resumed,
and writes the aggregate table (mean, sd and variance per scenario and mode) to table.md there.

Within each repeat every scenario runs once per mode, and the mode order rotates from repeat to
repeat, so no mode is always first (cold caches) or always last.

Usage:
    python analysis/run_campaign.py --repeats 5                    # everything, ~2 hours
    python analysis/run_campaign.py --scenarios bad-error --modes tier1 tier2_sprt
    python analysis/run_campaign.py --resume results/campaigns/<dir>
    python analysis/run_campaign.py --summarize results/campaigns/<dir>
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, variance

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGNS = ROOT / "results" / "campaigns"
RUNNER = ROOT / "scenarios" / "run.py"
MODES = ("naive_threshold", "tier1", "tier2_sprt")

sys.path.insert(0, str(RUNNER.parent))
from run import SCENARIOS  # noqa: E402

# Lines of the runner's output worth echoing; everything else goes to the per-run log file.
ECHO_PREFIXES = ("===", "SUMMARY", "timed out", "moved stray", "Traceback", "SystemExit")


@dataclass(frozen=True)
class Stat:
    n: int
    mean: float
    var: float
    lo: float
    hi: float

    @property
    def sd(self) -> float:
        return self.var**0.5

    def __str__(self) -> str:
        if self.n == 0:
            return "-"
        return f"{self.mean:.1f} ± {self.sd:.1f} (var {self.var:.1f}; {self.lo:g}–{self.hi:g})"


def stat(values: list[float]) -> Stat:
    if not values:
        return Stat(0, 0.0, 0.0, 0.0, 0.0)
    return Stat(
        len(values),
        mean(values),
        variance(values) if len(values) > 1 else 0.0,
        min(values),
        max(values),
    )


def plan(scenarios: list[str], modes: list[str], repeats: int) -> list[tuple[int, str, str]]:
    """(repeat, scenario, mode) in run order, rotating the mode order each repeat."""
    cells = []
    for r in range(repeats):
        rotated = modes[r % len(modes) :] + modes[: r % len(modes)]
        for scenario in scenarios:
            for mode in rotated:
                cells.append((r, scenario, mode))
    return cells


def run_one(repeat: int, scenario: str, mode: str, log_dir: Path) -> dict:
    """One live scenario run. A run that misses its expected outcome is a result, not an error:
    the runner still prints its SUMMARY and exits non-zero, and that row is recorded as ok=false."""
    env = {**os.environ, "DECISION_MODE": mode}
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"r{repeat + 1}_{scenario}_{mode}.txt"
    summary: dict | None = None
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, str(RUNNER), scenario],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            if line.startswith(ECHO_PREFIXES):
                print("   " + line.rstrip(), flush=True)
            if line.startswith("SUMMARY "):
                summary = json.loads(line[len("SUMMARY ") :])
        code = proc.wait()
    row = {"repeat": repeat + 1, "scenario": scenario, "mode": mode, "exit_code": code}
    if summary is None:
        return {**row, "ok": False, "error": f"no SUMMARY line; see {log_path.name}"}
    return {**row, **summary}


def read_rows(campaign: Path) -> list[dict]:
    path = campaign / "runs.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def table(rows: list[dict]) -> str:
    """Markdown table: one line per (scenario, mode), in first-seen order."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["scenario"], row["mode"]), []).append(row)
    lines = [
        "| scenario | mode | expected outcome | time to outcome, s | canary requests "
        "| canary errors served |",
        "|---|---|---|---|---|---|",
    ]
    for (scenario, mode), group in groups.items():
        hit = [r for r in group if r.get("ok")]
        # Wrong: reached the opposite terminal state (good rolled back, bad promoted).
        wrong = sum(
            1 for r in group if not r.get("ok") and (r.get("rolled_back") or r.get("promoted"))
        )
        other = len(group) - len(hit) - wrong
        outcome = f"{len(hit)}/{len(group)}"
        if wrong:
            outcome += f", {wrong} wrong"
        if other:
            outcome += f", {other} timed out or failed"
        lines.append(
            f"| {scenario} | {mode} | {outcome} "
            f"| {stat([r['seconds'] for r in hit if r.get('seconds') is not None])} "
            f"| {stat([r['canary_requests'] for r in hit])} "
            f"| {stat([r['canary_errors'] for r in hit if 'canary_errors' in r])} |"
        )
    return "\n".join(lines)


def write_table(campaign: Path) -> str:
    text = table(read_rows(campaign))
    note = (
        "Stats over runs that reached the expected outcome: mean ± sd (sample variance; min–max). "
        "Time is from the controller's first decision; canary requests is the blast radius.\n\n"
    )
    (campaign / "table.md").write_text(note + text + "\n", encoding="utf-8")
    return text


def run_campaign(campaign: Path, cells: list[tuple[int, str, str]]) -> list[dict]:
    """Run every cell not already in campaign/runs.jsonl, appending each result as it lands."""
    campaign.mkdir(parents=True, exist_ok=True)
    done = {(r["repeat"] - 1, r["scenario"], r["mode"]) for r in read_rows(campaign)}
    todo = [cell for cell in cells if cell not in done]
    print(
        f"campaign {campaign.relative_to(ROOT)}: {len(todo)} of {len(cells)} runs to go", flush=True
    )
    for i, (repeat, scenario, mode) in enumerate(todo, start=1):
        print(f"[{i}/{len(todo)}] repeat {repeat + 1} · {scenario} · {mode}", flush=True)
        row = run_one(repeat, scenario, mode, campaign / "logs")
        with (campaign / "runs.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    return read_rows(campaign)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scenarios", nargs="*", default=list(SCENARIOS), choices=list(SCENARIOS))
    parser.add_argument("--modes", nargs="*", default=list(MODES), choices=list(MODES))
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--name", default="campaign", help="label for the campaign folder")
    parser.add_argument("--resume", type=Path, help="continue an interrupted campaign folder")
    parser.add_argument("--summarize", type=Path, help="only rebuild table.md for a campaign")
    args = parser.parse_args()

    if args.summarize:
        print(write_table(args.summarize.resolve()))
        return
    if args.resume:
        campaign = args.resume.resolve()
        spec = json.loads((campaign / "campaign.json").read_text(encoding="utf-8"))
        cells = [tuple(c) for c in spec["cells"]]
    else:
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        campaign = CAMPAIGNS / f"{args.name}_{stamp}"
        cells = plan(args.scenarios, args.modes, args.repeats)
        campaign.mkdir(parents=True, exist_ok=True)
        spec = {
            "started": stamp,
            "scenarios": args.scenarios,
            "modes": args.modes,
            "repeats": args.repeats,
            "cells": cells,
        }
        (campaign / "campaign.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    run_campaign(campaign, cells)  # type: ignore[arg-type]
    print("\n" + write_table(campaign))


if __name__ == "__main__":
    main()
