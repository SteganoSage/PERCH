"""Score the pre-registered predictions against a live campaign.

Reads the committed predictions file (predictions/prediction_*.md), the campaign's runs.jsonl and
the simulator's E1 results, and writes evaluation.md into the campaign folder:
- every cell: predicted range vs live mean (hit when the live mean falls inside the range);
- every falsifiable claim P1-P8 from the predictions file, checked mechanically and marked hit or
  miss with the numbers that decided it.

The criteria below are transcribed from the predictions file, not tuned after the fact; misses are
explained in the write-up, not by changing a criterion here.

Usage:
    python analysis/evaluate_predictions.py results/campaigns/<dir>
"""

from __future__ import annotations

import argparse
from pathlib import Path
from statistics import mean, stdev

from experiments import OUT as SIM_DIR
from experiments import read_csv
from plot_experiments import BAD, MODES, read_predictions, read_runs

LOUD = ("bad-error", "bad-latency", "bad-partial")


def _cell(runs: list[dict], scenario: str, mode: str, hits_only: bool = True) -> list[dict]:
    return [
        r
        for r in runs
        if r["scenario"] == scenario and r["mode"] == mode and (r.get("ok") or not hits_only)
    ]


def _mean(rows: list[dict], key: str) -> float | None:
    values = [r[key] for r in rows if r.get(key) is not None]
    return mean(values) if values else None


def _sd(rows: list[dict], key: str) -> float | None:
    values = [r[key] for r in rows if r.get(key) is not None]
    return stdev(values) if len(values) > 1 else None


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.1f}"


def cells_table(runs: list[dict], predicted: dict) -> tuple[str, int, int]:
    lines = [
        "| scenario | mode | runs ok | seconds: predicted → live | in range | "
        "canary requests: predicted → live | in range |",
        "|---|---|---|---|---|---|---|",
    ]
    hits = total = 0
    for scenario in ("good", *BAD):
        for mode in MODES:
            ok = _cell(runs, scenario, mode)
            every = _cell(runs, scenario, mode, hits_only=False)
            row = [scenario, mode, f"{len(ok)}/{len(every)}"]
            for metric, key in (("seconds", "seconds"), ("requests", "canary_requests")):
                pred = predicted.get((scenario, mode), {}).get(metric)
                live = _mean(ok, key)
                if pred is None or live is None:
                    row += ["-", "-"]
                    continue
                inside = pred[1] <= live <= pred[2]
                hits += inside
                total += 1
                row += [
                    f"{pred[0]:g} ({pred[1]:g}–{pred[2]:g}) → **{live:.1f}**",
                    "✅" if inside else "❌",
                ]
            lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines), hits, total


def claims(runs: list[dict], sim: dict) -> list[tuple[str, bool, str]]:
    out = []

    bad = [r for r in runs if r["scenario"] in BAD]
    promoted = [r for r in bad if r.get("promoted")]
    rolled = [r for r in bad if r.get("rolled_back")]
    out.append(
        (
            "P1. No bad canary escapes",
            not promoted and len(rolled) == len(bad),
            f"{len(rolled)}/{len(bad)} bad runs rolled back, {len(promoted)} promoted",
        )
    )

    detail, ok = [], True
    for s in LOUD:
        m = {mode: _mean(_cell(runs, s, mode), "canary_requests") for mode in MODES}
        holds = None not in m.values() and m["naive_threshold"] <= m["tier2_sprt"] < m["tier1"]
        ok &= bool(holds)
        detail.append(
            f"{s}: naive {_fmt(m['naive_threshold'])}, tier2 {_fmt(m['tier2_sprt'])}, "
            f"tier1 {_fmt(m['tier1'])}"
        )
    out.append(("P2. Loud-fault ordering naive ≤ tier2 < tier1", ok, "; ".join(detail)))

    detail, ok = [], True
    for s in ("bad-error", "bad-partial"):
        rows = _cell(runs, s, "tier1")
        m, sd = _mean(rows, "canary_requests"), _sd(rows, "canary_requests")
        holds = m is not None and sd is not None and sd < 5 and 47 <= m <= 54
        ok &= holds
        detail.append(f"{s}: mean {_fmt(m)}, sd {_fmt(sd)}")
    out.append(("P3. tier1's blast radius is its 40-request floor", ok, "; ".join(detail)))

    detail, ok = [], True
    for s in ("bad-error", "bad-partial"):
        for mode in ("naive_threshold", "tier2_sprt"):
            live = _mean(_cell(runs, s, mode), "canary_requests")
            gap = None if live is None else live - sim[(s, mode)]["canary_requests_mean"]
            holds = gap is not None and 1 <= gap <= 8
            ok &= holds
            detail.append(f"{s}/{mode}: live − sim {_fmt(gap)}")
        live = _mean(_cell(runs, s, "tier1"), "canary_requests")
        holds = live is not None and abs(live - 50) <= 4
        ok &= holds
        detail.append(f"{s}/tier1: live {_fmt(live)}")
    out.append(("P4. The live tax on first-poll detectors", ok, "; ".join(detail)))

    s = "bad-slow-onset"
    means = {mode: _mean(_cell(runs, s, mode), "canary_requests") for mode in MODES}
    sds = {mode: _sd(_cell(runs, s, mode), "canary_requests") for mode in MODES}
    ok = (
        None not in means.values()
        and None not in sds.values()
        and max(means, key=means.get) == "tier2_sprt"  # type: ignore[arg-type]
        and max(sds, key=sds.get) == "naive_threshold"  # type: ignore[arg-type]
    )
    out.append(
        (
            "P5. Slow onset is tier2's worst case; naive the most variable",
            ok,
            ", ".join(f"{m}: {_fmt(means[m])} ± {_fmt(sds[m])}" for m in MODES),
        )
    )

    good = {mode: _cell(runs, "good", mode, hits_only=False) for mode in MODES}
    wrong = {mode: sum(1 for r in good[mode] if r.get("rolled_back")) for mode in MODES}
    promoted_t2 = sum(1 for r in good["tier2_sprt"] if r.get("promoted"))
    ok = promoted_t2 == len(good["tier2_sprt"]) == 5 and (
        wrong["naive_threshold"] + wrong["tier1"] <= 2
    )
    out.append(
        (
            "P6. tier2 promotes 5/5; naive + tier1 show 0-2 false rollbacks in 10",
            ok,
            f"tier2 promoted {promoted_t2}/{len(good['tier2_sprt'])}; false rollbacks: "
            + ", ".join(f"{m} {wrong[m]}/{len(good[m])}" for m in MODES),
        )
    )

    times = [r["seconds"] for r in runs if r["scenario"] == "good" and r.get("promoted")]
    ok = bool(times) and all(128 <= t <= 150 for t in times)
    out.append(
        (
            "P7. Every promoted good run takes 128-150s",
            ok,
            f"{len(times)} runs, {min(times):.1f}–{max(times):.1f}s" if times else "no runs",
        )
    )

    m = {mode: _mean(_cell(runs, "bad-error", mode), "canary_errors") for mode in MODES}
    ratio = m["tier1"] / m["tier2_sprt"] if m["tier1"] is not None and m["tier2_sprt"] else None
    ok = (
        None not in m.values()
        and m["naive_threshold"] < m["tier2_sprt"] < m["tier1"]
        and ratio is not None
        and 2 <= ratio <= 4.5
    )
    out.append(
        (
            "P8. bad-error errors served: naive < tier2 < tier1, tier1 ≈ 3× tier2",
            ok,
            ", ".join(f"{mo} {_fmt(m[mo])}" for mo in MODES) + f"; tier1/tier2 {_fmt(ratio)}",
        )
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("campaign", type=Path)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    runs = read_runs(campaign)
    sim = {(r["scenario"], r["mode"]): r for r in read_csv(SIM_DIR / "e1_modes.csv")}
    table, hits, total = cells_table(runs, read_predictions())
    scored = claims(runs, sim)
    claim_lines = [
        f"| {name} | {'✅ hit' if ok else '❌ miss'} | {why} |" for name, ok, why in scored
    ]
    text = "\n".join(
        [
            "# Predictions vs live results",
            "",
            f"Campaign: `{campaign.name}` ({len(runs)} runs). Predictions: the committed "
            "`predictions/prediction_*.md`. Criteria are transcribed from that file.",
            "",
            f"## Falsifiable claims: {sum(ok for _, ok, _ in scored)}/{len(scored)} hit",
            "",
            "| claim | result | numbers |",
            "|---|---|---|",
            *claim_lines,
            "",
            f"## Per-cell predicted ranges: {hits}/{total} live means inside",
            "",
            table,
            "",
        ]
    )
    (campaign / "evaluation.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
