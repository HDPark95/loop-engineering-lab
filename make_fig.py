#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the results figure from the pilot trajectory logs.
Panel A: mean progress-mirage rate by evaluator arm (error bars = min/max over replicates).
Panel B: deployed (last-accepted) conversion trajectory by arm, mean over replicates.
English labels only (avoids CJK font setup). Output: results_fig.png (for the paper)."""
import json, os, glob, sys, statistics as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

LAB = os.path.dirname(os.path.abspath(__file__))
LOGS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(LAB, "logs")
ARMS = ["in-band-self", "in-band-judge", "out-of-band"]
COLORS = {"in-band-self": "#c0392b", "in-band-judge": "#e08e0b", "out-of-band": "#1f7a3d"}

def load():
    trajs = {}
    for path in sorted(glob.glob(os.path.join(LOGS, "pilot_*_r*.jsonl"))):
        name = os.path.basename(path)[len("pilot_"):-len(".jsonl")]
        arm, _ = name.rsplit("_r", 1)
        rows = [json.loads(l) for l in open(path) if l.strip()]
        if rows:
            trajs.setdefault(arm, []).append(rows)
    return trajs

def mirage(rows):
    acc = [r for r in rows if r["accept"]]
    return (sum(1 for r in acc if r["delta"] <= 0) / len(acc)) if acc else 0.0

def deployed_curve(rows):
    base = rows[0]["conversions"] - rows[0]["delta"]
    return [base] + [r.get("deployed_conv", r["conversions"]) for r in rows]

def main():
    trajs = load()
    arms = [a for a in ARMS if a in trajs]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.4, 3.2))

    # Panel A: mirage rate by arm
    means, lo, hi = [], [], []
    for a in arms:
        ms = [mirage(r) for r in trajs[a]]
        m = st.mean(ms); means.append(m)
        lo.append(m - min(ms)); hi.append(max(ms) - m)
    # The grounded arm's zero is a consequence of the gate rule rather than a
    # measurement: it accepts iff the delta is positive, and this bar counts
    # accepted cycles whose delta is not positive. Hatching it keeps the figure
    # from asserting in a glance what the text spends a paragraph withdrawing.
    bars = ax1.bar(range(len(arms)), means, yerr=[lo, hi], capsize=4,
                   color=[COLORS[a] for a in arms], edgecolor="black", linewidth=0.6,
                   hatch=["//" if a == "out-of-band" else "" for a in arms])
    for i, a in enumerate(arms):
        if a == "out-of-band":
            ax1.text(i, 0.12, "structural", ha="center", fontsize=7,
                     style="italic", rotation=90, color="black")
    ax1.set_xticks(range(len(arms))); ax1.set_xticklabels(arms, fontsize=8, rotation=12)
    ax1.set_ylabel("progress-mirage rate", fontsize=9)
    ax1.set_ylim(0, 1.0)
    ax1.set_title("(a) Accepted cycles with held-out delta <= 0", fontsize=9)
    for i, m in enumerate(means):
        ax1.text(i, m + 0.04, f"{m:.2f}", ha="center", fontsize=8, fontweight="bold")
    ax1.grid(axis="y", alpha=0.3)

    # Panel B: deployed conversion trajectory by arm (mean over reps)
    for a in arms:
        curves = [deployed_curve(r) for r in trajs[a]]
        L = min(len(c) for c in curves)
        mean_curve = [st.mean(c[i] for c in curves) for i in range(L)]
        ax2.plot(range(L), mean_curve, marker="o", markersize=3.5, linewidth=1.8,
                 color=COLORS[a], label=a)
    base0 = st.mean(deployed_curve(trajs[arms[0]][0])[:1] + [deployed_curve(r)[0] for a in arms for r in trajs[a]])
    ax2.axhline(base0, color="gray", linestyle="--", linewidth=0.8, alpha=0.7)
    ax2.text(0.05, base0, " baseline", fontsize=7, color="gray", va="bottom")
    ax2.set_xlabel("cycle", fontsize=9)
    ax2.set_ylabel("held-out outcome of the deployed state", fontsize=9)
    ax2.set_title("(b) Held-out outcome of the deployed state", fontsize=9)
    ax2.legend(fontsize=7.5, loc="best")
    ax2.grid(alpha=0.3)

    fig.tight_layout()
    out = os.path.join(LAB, "results_fig.png")
    fig.savefig(out, dpi=200, bbox_inches="tight")
    print("wrote", out)

if __name__ == "__main__":
    main()
