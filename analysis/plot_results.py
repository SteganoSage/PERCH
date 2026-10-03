"""Plot one scenario run from results/decision_log.jsonl."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt


def load_log(path: Path) -> list[dict]:
    """Load the decision log from a JSONL file.

    Args:
        path: Path to the decision log file.

    Returns:
        A list of dictionaries, where each dictionary represents one log entry.
    """
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def plot_run(rows: list[dict], out_dir: Path, title: str) -> Path:
    """Generate a multi-panel matplotlib plot of the rollout process.

    Creates a 3-panel plot showing:
    1. Canary weight over time (with rollback/promote/advance events)
    2. Error rate for both stable and canary over time
    3. p95 latency for both stable and canary over time

    Args:
        rows: List of decision log entries.
        out_dir: Directory to save the output plot (rollout.png).
        title: Title of the plot.

    Returns:
        The path to the generated plot image.
    """
    if not rows:
        raise SystemExit("decision log is empty")
    t0 = rows[0]["unix"]
    xs = [r["unix"] - t0 for r in rows]
    weights = [r["canary_weight"] for r in rows]
    canary_err = [r["canary"]["error_rate"] for r in rows]
    stable_err = [r["stable"]["error_rate"] for r in rows]
    canary_p95 = [r["canary"]["p95_ms"] for r in rows]
    stable_p95 = [r["stable"]["p95_ms"] for r in rows]

    fig, axes = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
    fig.suptitle(title)

    axes[0].step(xs, weights, where="post", color="#1f77b4")
    axes[0].set_ylabel("canary weight %")
    axes[0].set_ylim(-5, 105)
    for row, x in zip(rows, xs):
        if row["action"] == "rollback":
            axes[0].axvline(x, color="#d62728", linestyle="--", label="rollback")
        elif row["action"] == "promote":
            axes[0].axvline(x, color="#2ca02c", linestyle="--", label="promote")
        elif row["action"] == "advance":
            axes[0].axvline(x, color="#7f7f7f", linestyle=":", alpha=0.5)
    handles, labels = axes[0].get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    if by_label:
        axes[0].legend(by_label.values(), by_label.keys(), loc="best")

    axes[1].plot(xs, stable_err, label="stable", color="#1f77b4")
    axes[1].plot(xs, canary_err, label="canary", color="#ff7f0e")
    axes[1].set_ylabel("error rate")
    axes[1].legend(loc="best")

    axes[2].plot(xs, stable_p95, label="stable", color="#1f77b4")
    axes[2].plot(xs, canary_p95, label="canary", color="#ff7f0e")
    axes[2].set_ylabel("p95 latency (ms)")
    axes[2].set_xlabel("seconds from first decision")
    axes[2].legend(loc="best")

    fig.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "rollout.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"wrote {out}")
    return out


def main() -> None:
    """Main entrypoint for the plotting script.

    Parses command-line arguments and triggers the plotting logic.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default="results/decision_log.jsonl")
    parser.add_argument("--out-dir", default="results")
    parser.add_argument("--title", default="canary rollout")
    args = parser.parse_args()
    rows = load_log(Path(args.log))
    plot_run(rows, Path(args.out_dir), args.title)


if __name__ == "__main__":
    main()
