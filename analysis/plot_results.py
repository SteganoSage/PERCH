"""Plot one scenario run from its decision_log.jsonl: weight, error rate and p95 latency over time.

Same visual system as analysis/plot_experiments.py. Stable is the reference, drawn in muted gray;
the canary, the subject, carries the series colour. Rollback and promote are marked in the status
colours with a text label, never colour alone. Polls where a version had no traffic in its window
are left out of its error and latency lines rather than drawn as zero.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from plot_experiments import AXIS, INK_2, MARKER, MUTED, style  # noqa: E402

CANARY = "#2a78d6"  # categorical slot 1
STABLE = MUTED
ROLLBACK = "#d03b3b"  # status: critical
PROMOTE = "#0ca30c"  # status: good


def load_log(path: Path) -> list[dict]:
    """Load the decision log from a JSONL file (empty list if it is missing)."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _seen(rows: list[dict], service: str, value) -> list[float]:
    """Per-poll values for a service, None where its window had no requests."""
    return [value(r[service]) if r[service]["n"] > 0 else None for r in rows]


def plot_run(rows: list[dict], out_dir: Path, title: str) -> Path:
    """Three stacked panels (canary weight, error rate, p95 latency) -> out_dir/rollout.png."""
    if not rows:
        raise SystemExit("decision log is empty")
    style()
    t0 = rows[0]["unix"]
    xs = [r["unix"] - t0 for r in rows]

    fig, axes = plt.subplots(3, 1, figsize=(10, 8.4), sharex=True)
    fig.suptitle(title, x=0.01, ha="left", fontsize=12, fontweight="bold")

    weight = axes[0]
    weight.step(xs, [r["canary_weight"] for r in rows], where="post", color=CANARY, zorder=3)
    weight.set_ylim(-4, 104)
    weight.set_title("Canary traffic share", fontsize=10)
    weight.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}%"))
    for row, x in zip(rows, xs, strict=True):
        if row["action"] == "advance":
            weight.axvline(x, color=AXIS, linewidth=1, zorder=1)
        elif row["action"] in ("rollback", "promote"):
            color = ROLLBACK if row["action"] == "rollback" else PROMOTE
            for ax in axes:
                ax.axvline(x, color=color, linewidth=1.5, zorder=2)
            weight.annotate(
                row["action"],
                (x, 104),
                xytext=(4, -12),
                textcoords="offset points",
                color=INK_2,
                fontsize=9,
            )

    for ax, value, label, scale in (
        (axes[1], lambda s: s["error_rate"], "Error rate in the window", 100),
        (axes[2], lambda s: s["p95_ms"], "p95 latency in the window, ms", 1),
    ):
        for service, color in (("stable", STABLE), ("canary", CANARY)):
            ys = [None if v is None else scale * v for v in _seen(rows, service, value)]
            ax.plot(xs, ys, color=color, zorder=3 if service == "canary" else 2, **MARKER)
        ax.set_title(label, fontsize=10)
        ax.set_ylim(bottom=0)
    axes[1].yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}%"))
    axes[2].set_xlabel("seconds from the controller's first decision")

    fig.legend(
        [plt.Line2D([], [], color=c, linestyle="none", **MARKER) for c in (STABLE, CANARY)],
        ["stable", "canary"],
        loc="upper right",
        bbox_to_anchor=(0.99, 0.995),
        ncol=2,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "rollout.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"wrote {out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default="results/decision_log.jsonl")
    parser.add_argument("--out-dir", default="results")
    parser.add_argument("--title", default="canary rollout")
    args = parser.parse_args()
    plot_run(load_log(Path(args.log)), Path(args.out_dir), args.title)


if __name__ == "__main__":
    main()
