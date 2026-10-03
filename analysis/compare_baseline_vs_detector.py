"""Naive threshold vs statistical detector, same fault, blast-radius comparison.

Default is one repeat so a laptop demo finishes. Pass --repeats 5 for the
mean/variance the write-up wants.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_once(mode: str) -> dict:
    """Run the bad-error scenario once for a given decision mode.

    Sets the DECISION_MODE environment variable and invokes the scenario runner.
    Parses the SUMMARY line from the runner's output.

    Args:
        mode: The decision mode to use (e.g., "naive_threshold", "tier1").

    Returns:
        A dictionary containing the parsed summary metrics from the run.
    """
    env = os.environ.copy()
    env["DECISION_MODE"] = mode
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scenarios" / "run.py"), "bad-error"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = None
    for line in proc.stdout.splitlines():
        if line.startswith("SUMMARY "):
            summary = json.loads(line[len("SUMMARY ") :])
    if summary is None:
        raise SystemExit(f"no SUMMARY line from {mode} run:\n{proc.stdout}\n{proc.stderr}")
    summary["mode"] = mode
    return summary


def main() -> None:
    """Main entrypoint for the comparison script.

    Parses command-line arguments to determine the number of repeats,
    runs the bad-error scenario in both 'naive_threshold' and 'tier1' modes,
    and prints a formatted table comparing the blast radius (canary requests).
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    rows = []
    for i in range(args.repeats):
        for mode in ("naive_threshold", "tier1"):
            print(f"--- repeat {i + 1}/{args.repeats} mode={mode} ---", flush=True)
            rows.append(run_once(mode))

    print("\nmode              seconds  canary_reqs  blast_%")
    for row in rows:
        print(
            f"{row['mode']:<16} {row['seconds']:>7}  {row['canary_requests']:>11}  "
            f"{row['blast_radius_pct']:>7}"
        )


if __name__ == "__main__":
    main()
