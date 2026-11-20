"""Figures for the experiments: the simulation sweeps and, given a live campaign, live vs predicted.

Reads results/experiments/simulation/*.csv (analysis/experiments.py) and, with --campaign, a
results/campaigns/<dir>/runs.jsonl plus the pre-registered predictions file. Writes PNGs to
results/experiments/figures/.

Colour: the three decision modes take the first three slots of the validated categorical palette,
in fixed order, in every figure: naive threshold blue, tier1 orange, tier2 aqua (all-pairs CVD
ΔE ≥ 9.2). Aqua is under 3:1 contrast on the light surface, so every figure carries a legend and
direct labels, and every plotted number is also in a markdown table (simulation/summary.md, the
campaign's table.md).

Usage:
    python analysis/plot_experiments.py
    python analysis/plot_experiments.py --campaign results/campaigns/<dir>
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from experiments import OUT as SIM_DIR  # noqa: E402
from experiments import read_csv  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIGURES = ROOT / "results" / "experiments" / "figures"

# Reference palette, light mode (dataviz skill references/palette.md).
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
MODES = ("naive_threshold", "tier1", "tier2_sprt")
COLOR = {"naive_threshold": "#2a78d6", "tier1": "#eb6834", "tier2_sprt": "#1baf7a"}
LABEL = {
    "naive_threshold": "naive threshold",
    "tier1": "tier1 · confidence intervals",
    "tier2_sprt": "tier2 · sequential test (SPRT)",
}
BAD = ("bad-error", "bad-latency", "bad-partial", "bad-slow-onset")
MARKER = {"marker": "o", "markersize": 8, "markeredgecolor": SURFACE, "markeredgewidth": 2}


def style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "font.family": "sans-serif",
            "font.size": 10,
            "text.color": INK,
            "axes.titlecolor": INK,
            "axes.titlesize": 11,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
            "axes.titlepad": 10,
            "axes.labelcolor": INK_2,
            "axes.edgecolor": AXIS,
            "axes.linewidth": 1,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": False,
            "axes.grid": True,
            "axes.grid.axis": "y",
            "grid.color": GRID,
            "grid.linewidth": 1,
            "grid.linestyle": "-",
            "xtick.color": AXIS,
            "ytick.color": AXIS,
            "xtick.labelcolor": MUTED,
            "ytick.labelcolor": MUTED,
            "ytick.left": False,
            "lines.linewidth": 2,
            "lines.solid_capstyle": "round",
            "lines.solid_joinstyle": "round",
            "legend.frameon": False,
            "legend.fontsize": 10,
            "legend.labelcolor": INK_2,
        }
    )


def legend(fig: plt.Figure, modes: tuple[str, ...] = MODES) -> None:
    handles = [plt.Line2D([], [], color=COLOR[m], linestyle="none", **MARKER) for m in modes]
    fig.legend(
        handles,
        [LABEL[m] for m in modes],
        loc="upper left",
        bbox_to_anchor=(0.01, 0.995),
        ncol=len(modes),
        handletextpad=0.4,
        columnspacing=1.6,
    )


def caption(fig: plt.Figure, text: str) -> None:
    fig.text(0.01, 0.01, text, color=MUTED, fontsize=8.5, ha="left", va="bottom", wrap=True)


def save(fig: plt.Figure, name: str) -> Path:
    FIGURES.mkdir(parents=True, exist_ok=True)
    path = FIGURES / name
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"wrote {path.relative_to(ROOT)}")
    return path


def rows(experiment: str, **match: object) -> list[dict]:
    return [
        r
        for r in read_csv(SIM_DIR / f"{experiment}.csv")
        if all(r[k] == v for k, v in match.items())
    ]


def series(ax: plt.Axes, xs, ys, mode: str, lo=None, hi=None) -> None:
    ax.plot(xs, ys, color=COLOR[mode], **MARKER, zorder=3)
    if lo is not None:
        ax.fill_between(xs, lo, hi, color=COLOR[mode], alpha=0.10, linewidth=0, zorder=1)


def end_label(ax: plt.Axes, x: float, y: float, text: str, dy: float = 0) -> None:
    ax.annotate(
        text,
        (x, y),
        xytext=(8, dy),
        textcoords="offset points",
        color=INK_2,
        fontsize=9,
        va="center",
    )


def percent_axis(ax: plt.Axes) -> None:
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}%"))


# --- simulation figures ---------------------------------------------------------------------


def fig_noise() -> Path:
    """E2: false rollbacks and blast radius as background errors grow."""
    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4.6))
    for mode in MODES:
        good = rows("e2_noise", scenario="good", mode=mode)
        xs = [100 * r["value"] for r in good]
        ys = [100 * r["wrong_rate"] for r in good]
        series(
            left,
            xs,
            ys,
            mode,
            [100 * r["wrong_lo"] for r in good],
            [100 * r["wrong_hi"] for r in good],
        )
        end_label(left, xs[-1], ys[-1], f"{ys[-1]:.0f}%")
        bad = rows("e2_noise", scenario="bad-error", mode=mode)
        ys = [r["canary_requests_mean"] for r in bad]
        series(right, xs, ys, mode)
        end_label(right, xs[-1], ys[-1], f"{ys[-1]:.0f}")
    left.axhline(5, color=INK_2, linewidth=1, zorder=2)
    left.annotate(
        "5% budget", (2.1, 5), xytext=(0, 2), textcoords="offset points", color=INK_2, fontsize=9
    )
    left.set_title("Good canaries wrongly rolled back")
    left.set_xlabel("Background error rate shared by both versions")
    percent_axis(left)
    left.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}%"))
    right.set_title("Requests reaching a 30%-error canary before rollback")
    right.set_xlabel("Background error rate shared by both versions")
    right.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}%"))
    right.set_ylim(bottom=0)
    for ax in (left, right):
        ax.set_xlim(-0.2, 5.9)
    legend(fig)
    caption(
        fig,
        "Simulation, 1,000 rollouts per point (analysis/experiments.py, E2). Bands: 95% Wilson "
        "intervals. Noise makes the naive rule roll back two in three good canaries at 5% "
        "background errors; tier2 stays at its budget and pays in blast radius instead.",
    )
    fig.tight_layout(rect=(0, 0.06, 1, 0.93))
    return save(fig, "e2_noise.png")


def fig_fault_size() -> Path:
    """E3: detection rate and blast radius by fault size."""
    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4.6))
    for mode in MODES:
        cells = rows("e3_fault_size", mode=mode)
        xs = [100 * r["value"] for r in cells]
        series(
            left,
            xs,
            [100 * r["expected_rate"] for r in cells],
            mode,
            [100 * r["expected_lo"] for r in cells],
            [100 * r["expected_hi"] for r in cells],
        )
        caught = [r for r in cells if r["expected_rate"] >= 0.9]
        cx = [100 * r["value"] for r in caught]
        cy = [r["canary_requests_mean"] for r in caught]
        series(right, cx, cy, mode)
        end_label(right, cx[-1], cy[-1], f"{cy[-1]:.0f}")
    for ax in (left, right):
        ax.set_xscale("log")
        ax.set_xticks([0.5, 1, 2, 3, 5, 10, 20, 30])
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"+{v:g}"))
        ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        ax.set_xlabel("Canary fault: extra error rate, percentage points over 1% background")
    left.set_title("Bad canaries caught before reaching 100%")
    percent_axis(left)
    left.set_ylim(0, 105)
    right.set_title("Requests reaching the canary before rollback")
    right.set_yscale("log")
    right.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    right.set_yticks([10, 20, 50, 100, 200, 300])
    right.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    legend(fig)
    caption(
        fig,
        "Simulation, 1,000 rollouts per point (E3). Right: only fault sizes a mode catches in at "
        "least 90% of runs, so the mean is not just its lucky catches. Small-fault 'catches' by "
        "the naive rule are mostly its 12% false-rollback noise.",
    )
    fig.tight_layout(rect=(0, 0.06, 1, 0.93))
    return save(fig, "e3_fault_size.png")


def fig_bake() -> Path:
    """E4: the bake-time trade-off."""
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.4))
    panels = (
        ("good", "seconds_mean", "Time to ship a good canary, s", 1),
        ("good", "wrong_rate", "Good canaries wrongly rolled back", 100),
        ("bad-subtle", "canary_requests_mean", "Requests reaching a +5-point fault", 1),
    )
    for ax, (scenario, metric, title, scale) in zip(axes, panels, strict=True):
        for mode in MODES:
            cells = [
                r for r in rows("e4_bake", scenario=scenario, mode=mode) if r[metric] is not None
            ]
            cells = [r for r in cells if r["expected"] > 0]
            xs = [r["value"] for r in cells]
            ys = [scale * r[metric] for r in cells]
            series(ax, xs, ys, mode)
        ax.axvline(25, color=AXIS, linewidth=1, zorder=0)
        ax.set_title(title)
        ax.set_xlabel("Bake time per ramp stage, s")
        ax.set_xticks([10, 15, 20, 25, 35, 50])
        ax.set_ylim(bottom=0)
    percent_axis(axes[1])
    axes[0].annotate(
        "chosen: 25s", (25, 0), xytext=(4, 6), textcoords="offset points", color=INK_2, fontsize=9
    )
    axes[0].annotate(
        "no tier1 point at ≤15s: the 5% stage\nsees < 40 requests, so it never decides",
        (10, 225),
        color=INK_2,
        fontsize=9,
        ha="left",
    )
    legend(fig)
    caption(
        fig,
        "Simulation, 1,000 rollouts per point (E4). Detection of loud faults does not depend on "
        "bake time (bad-error is caught in the first polls at every setting); rollout time grows "
        "~5 s per extra second of bake. 25s is the shortest bake that gives tier1 its 40 requests "
        "at the 5% stage with margin.",
    )
    fig.tight_layout(rect=(0, 0.08, 1, 0.92))
    return save(fig, "e4_bake.png")


def fig_modes() -> Path:
    """E1: blast radius per bad scenario, and false rollbacks of good canaries, per mode."""
    fig, (left, right) = plt.subplots(
        1, 2, figsize=(12, 4.8), gridspec_kw={"width_ratios": [2.6, 1]}
    )
    scenarios = (*BAD, "bad-subtle")
    offset = {"naive_threshold": -0.22, "tier1": 0.0, "tier2_sprt": 0.22}
    for mode in MODES:
        for i, name in enumerate(scenarios):
            (r,) = rows("e1_modes", scenario=name, mode=mode)
            y = i + offset[mode]
            left.plot(
                [r["canary_requests_p10"], r["canary_requests_p90"]],
                [y, y],
                color=COLOR[mode],
                linewidth=2,
                zorder=2,
            )
            left.plot([r["canary_requests_mean"]], [y], color=COLOR[mode], **MARKER, zorder=3)
        (g,) = rows("e1_modes", scenario="good", mode=mode)
        y = MODES.index(mode)
        right.plot([100 * g["wrong_lo"], 100 * g["wrong_hi"]], [y, y], color=COLOR[mode], zorder=2)
        right.plot([100 * g["wrong_rate"]], [y], color=COLOR[mode], **MARKER, zorder=3)
        end_label(right, 100 * g["wrong_hi"], y, f"{100 * g['wrong_rate']:.1f}%")
    left.set_yticks(range(len(scenarios)), scenarios)
    left.invert_yaxis()
    left.set_title("Requests reaching a bad canary before rollback")
    left.set_xlabel("Canary requests (dot: mean · line: 10th–90th percentile of runs)")
    right.set_yticks(range(len(MODES)), [LABEL[m].split(" · ")[0] for m in MODES])
    right.invert_yaxis()
    right.set_title("Good canaries rolled back")
    right.set_xlabel("Share of runs (line: 95% interval)")
    right.axvline(5, color=INK_2, linewidth=1, zorder=1)
    right.annotate(
        "5% budget", (5, -0.45), xytext=(3, 0), textcoords="offset points", color=INK_2, fontsize=9
    )
    right.set_xlim(0, 17)
    right.set_ylim(2.6, -0.8)
    right.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}%"))
    for ax in (left, right):
        ax.grid(axis="x")
        ax.grid(axis="y", visible=False)
        ax.tick_params(axis="y", labelcolor=INK_2, length=0)
        ax.spines["bottom"].set_visible(False)
    legend(fig)
    caption(
        fig,
        "Simulation, 1,000 rollouts per cell (E1), 1% background errors, 25s bake. bad-subtle "
        "(+5 points) is simulation-only. The naive rule is fastest and least reliable; tier2 sits "
        "near naive on loud faults with a sixth of tier1's false rollbacks.",
    )
    fig.tight_layout(rect=(0, 0.06, 1, 0.93))
    return save(fig, "e1_modes.png")


# --- live campaign figures ------------------------------------------------------------------


def read_predictions() -> dict[tuple[str, str], dict[str, tuple[float, float, float]]]:
    """{(scenario, mode): {"seconds": (point, lo, hi), "requests": (...)}} from the committed
    pre-registration table, so the file stays the single source of the predicted numbers."""
    path = max((ROOT / "predictions").glob("prediction_*.md"))
    cell = re.compile(r"\*\*([\d,.]+)(?:–[\d,.]+)?\*\* \(([\d,.]+)–([\d,.]+)\)")
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) != 6 or parts[1] not in MODES:
            continue
        found = {}
        for key, text in (("seconds", parts[3]), ("requests", parts[4])):
            m = cell.search(text)
            if m:
                found[key] = tuple(float(g.replace(",", "")) for g in m.groups())
        out[(parts[0], parts[1])] = found
    return out


def read_runs(campaign: Path) -> list[dict]:
    lines = (campaign / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def fig_live_vs_predicted(campaign: Path) -> Path:
    predicted = read_predictions()
    sim = {(r["scenario"], r["mode"]): r for r in read_csv(SIM_DIR / "e1_modes.csv")}
    runs = read_runs(campaign)
    fig, axes = plt.subplots(len(BAD), 2, figsize=(12, 2.0 * len(BAD) + 1.4), squeeze=False)
    rng = np.random.default_rng(0)
    for row, name in enumerate(BAD):
        for col, (metric, key, sim_key) in enumerate(
            (
                ("canary_requests", "requests", "canary_requests_mean"),
                ("seconds", "seconds", "seconds_mean"),
            )
        ):
            ax = axes[row][col]
            for i, mode in enumerate(MODES):
                pred = predicted.get((name, mode), {}).get(key)
                if pred:
                    ax.barh(
                        i,
                        pred[2] - pred[1],
                        left=pred[1],
                        height=0.5,
                        color=COLOR[mode],
                        alpha=0.15,
                        linewidth=0,
                        zorder=1,
                    )
                live = [
                    r[metric]
                    for r in runs
                    if r["scenario"] == name and r["mode"] == mode and r.get("ok")
                ]
                jitter = rng.uniform(-0.12, 0.12, len(live))
                ax.plot(
                    live,
                    i + jitter,
                    linestyle="none",
                    color=COLOR[mode],
                    **{**MARKER, "markersize": 7},
                    zorder=3,
                )
                s = sim.get((name, mode))
                if s and s[sim_key] is not None:
                    ax.plot(
                        [s[sim_key]] * 2, [i - 0.3, i + 0.3], color=INK, linewidth=1.5, zorder=2
                    )
            ax.set_yticks(
                range(len(MODES)), [LABEL[m].split(" · ")[0] for m in MODES] if col == 0 else []
            )
            ax.set_ylim(len(MODES) - 0.4, -0.6)
            ax.grid(axis="x")
            ax.grid(axis="y", visible=False)
            ax.tick_params(axis="y", labelcolor=INK_2, length=0)
            ax.set_xlim(left=0)
            metric_title = ("canary requests before rollback", "seconds to rollback")[col]
            ax.set_title(f"{name} · {metric_title}", fontsize=10)
    legend(fig)
    caption(
        fig,
        "Dots: live runs that rolled back (5 per cell, jittered vertically). Shaded bar: "
        "pre-registered predicted range. Black tick: simulator mean (1,000 runs). Live seconds "
        "are wall-clock: the Docker VM's clock is stepped back ~2.3s at a time, so some fall "
        "below a poll.",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    return save(fig, "live_vs_predicted.png")


def fig_live_timelines(campaign: Path) -> Path:
    """Canary weight over time, first repeat of each scenario: one row per mode, so modes that
    share the 5% stage until they roll back do not hide each other."""
    runs = [r for r in read_runs(campaign) if r["repeat"] == 1 and r.get("run_dir")]
    names = ("good", *BAD)
    fig, axes = plt.subplots(
        len(MODES), len(names), figsize=(15, 6.4), sharey=True, sharex="col", squeeze=False
    )
    for col, name in enumerate(names):
        for row, mode in enumerate(MODES):
            ax = axes[row][col]
            match = [r for r in runs if r["scenario"] == name and r["mode"] == mode]
            if match:
                log = ROOT / match[0]["run_dir"] / "decision_log.jsonl"
                lines = log.read_text(encoding="utf-8").splitlines()
                decisions = [json.loads(line) for line in lines if line]
                t0 = decisions[0]["unix"]
                xs = [d["unix"] - t0 for d in decisions]
                ax.step(
                    xs,
                    [d["canary_weight"] for d in decisions],
                    where="post",
                    color=COLOR[mode],
                    zorder=3,
                )
            ax.set_ylim(-4, 104)
            if row == 0:
                ax.set_title(name)
            if row == len(MODES) - 1:
                ax.set_xlabel("seconds")
            if col == 0:
                ax.set_ylabel(LABEL[mode].split(" · ")[0], color=INK_2)
                ax.yaxis.set_major_formatter(
                    matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}%")
                )
    legend(fig)
    caption(
        fig,
        "Live, first repeat of each scenario: the canary's traffic share as the controller set it.",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.93))
    return save(fig, "live_timelines.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--campaign", type=Path, help="also plot a live campaign folder")
    args = parser.parse_args()
    style()
    fig_modes()
    fig_noise()
    fig_fault_size()
    fig_bake()
    if args.campaign:
        campaign = args.campaign.resolve()
        fig_live_vs_predicted(campaign)
        fig_live_timelines(campaign)


if __name__ == "__main__":
    main()
