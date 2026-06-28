#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mechanical analysis of the real-agent pilot logs (no human discretion).
Reads every append-only trajectory log logs/pilot_<arm>_r<rep>.jsonl, recomputes
per-trajectory metrics from the raw rows, aggregates per arm with bootstrap CIs,
and emits the dependent variables and the preregistered hypothesis verdicts.
Pure standard library (PREREGISTRATION policy). Single source of truth = the raw
cycle logs. Run: python3 analyze.py"""
import json, os, glob, statistics as st, random

LAB = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(LAB, "logs")
ARMS_ORDER = ["in-band-self", "in-band-judge", "out-of-band"]

def load_trajectories():
    trajs = {}
    for path in sorted(glob.glob(os.path.join(LOGS, "pilot_*_r*.jsonl"))):
        name = os.path.basename(path)[len("pilot_"):-len(".jsonl")]
        arm, rep = name.rsplit("_r", 1)
        rows = [json.loads(l) for l in open(path) if l.strip()]
        if rows:
            trajs.setdefault(arm, []).append({"rep": int(rep), "rows": rows})
    return trajs

def traj_metrics(rows):
    acc = [r for r in rows if r["accept"]]
    mirage = (sum(1 for r in acc if r["delta"] <= 0) / len(acc)) if acc else 0.0
    baseline = rows[0]["conversions"] - rows[0]["delta"]   # delta_1 = conv_1 - baseline
    # real outcome = the deployed (last accepted) state at budget end; a rejected
    # final candidate is reverted, so this is not necessarily the last candidate.
    final = rows[-1].get("deployed_conv", rows[-1]["conversions"])
    gain = final - baseline
    ttf = next((r["cycle"] for r in rows if r["delta"] > 0), None)  # None = right-censored
    wasted = sum(1 for r in rows if r["delta"] <= 0) / len(rows)
    accept_rate = len(acc) / len(rows)
    return {"mirage_rate": mirage, "baseline": baseline, "final": final, "real_gain": gain,
            "ttf": ttf, "wasted_cycle_ratio": wasted, "accept_rate": accept_rate, "n_cycles": len(rows)}

def boot_ci(vals, reps=5000, lo=2.5, hi=97.5):
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return (vals[0] if vals else float("nan"), vals[0] if vals else float("nan"))
    rng = random.Random(20260628)
    means = []
    for _ in range(reps):
        s = [vals[rng.randrange(len(vals))] for _ in vals]
        means.append(sum(s) / len(s))
    means.sort()
    return (means[int(lo / 100 * reps)], means[int(hi / 100 * reps)])

def agg(arm, trajs):
    ms = [traj_metrics(t["rows"]) for t in trajs]
    def col(k):
        return [m[k] for m in ms]
    mir = col("mirage_rate")
    gain = col("real_gain")
    fin = col("final")
    ttfs = [m["ttf"] for m in ms if m["ttf"] is not None]
    return {
        "arm": arm, "n_traj": len(ms),
        "mirage_mean": st.mean(mir), "mirage_ci": boot_ci(mir),
        "real_gain_mean": st.mean(gain), "real_gain_ci": boot_ci(gain),
        "final_mean": st.mean(fin),
        "baseline_mean": st.mean(col("baseline")),
        "ttf_mean": (st.mean(ttfs) if ttfs else None), "ttf_censored": sum(1 for m in ms if m["ttf"] is None),
        "wasted_mean": st.mean(col("wasted_cycle_ratio")),
        "accept_rate_mean": st.mean(col("accept_rate")),
        "per_traj": ms,
    }

def fmt_ci(ci):
    return f"[{ci[0]:.2f}, {ci[1]:.2f}]"

def main():
    trajs = load_trajectories()
    if not trajs:
        print("no pilot logs found in logs/. run run_pilot.py first."); return
    aggs = {arm: agg(arm, trajs[arm]) for arm in trajs}
    order = [a for a in ARMS_ORDER if a in aggs] + [a for a in aggs if a not in ARMS_ORDER]

    print("=" * 78)
    print("REAL-AGENT PILOT  -  mechanical analysis (stdlib, no human discretion)")
    print("=" * 78)
    print(f"{'arm':16}{'n':4}{'mirage (95% CI)':24}{'real_gain (CI)':22}{'ttf':8}{'wasted':8}")
    print("-" * 78)
    for arm in order:
        a = aggs[arm]
        ttf = f"{a['ttf_mean']:.2f}" if a["ttf_mean"] is not None else "cens"
        print(f"{arm:16}{a['n_traj']:<4}"
              f"{a['mirage_mean']:.2f} {fmt_ci(a['mirage_ci']):20}"
              f"{a['real_gain_mean']:+.1f} {fmt_ci(a['real_gain_ci']):16}"
              f"{ttf:8}{a['wasted_mean']:.2f}")
    print("-" * 78)

    # preregistered hypothesis verdicts (thresholds frozen in PREREGISTRATION.md)
    print("\nPREREGISTERED HYPOTHESIS VERDICTS")
    if "in-band-self" in aggs and "out-of-band" in aggs:
        d = aggs["in-band-self"]["mirage_mean"] - aggs["out-of-band"]["mirage_mean"]
        print(f"  H1 (in-band-self mirage - out-of-band >= 0.20): diff={d:+.2f}  -> {'HIT' if d >= 0.20 else 'NOT MET'}")
    if "in-band-judge" in aggs and "out-of-band" in aggs:
        d = aggs["in-band-judge"]["mirage_mean"] - aggs["out-of-band"]["mirage_mean"]
        verdict = "HA1 ADOPTED (judge closes gap; reframe as judge quality)" if d <= 0.05 \
                  else "HA1 REJECTED (grounding matters; strong judge does NOT close gap)"
        print(f"  HA1 (in-band-judge mirage - out-of-band <= 0.05): diff={d:+.2f}  -> {verdict}")
    if "in-band-self" in aggs and "in-band-judge" in aggs:
        d = aggs["in-band-self"]["mirage_mean"] - aggs["in-band-judge"]["mirage_mean"]
        print(f"  (in-band-self mirage - in-band-judge): diff={d:+.2f}  (does a critical judge help at all)")

    json.dump({"aggregates": {a: {k: v for k, v in aggs[a].items() if k != "per_traj"} for a in aggs},
               "n_trajectories": {a: aggs[a]["n_traj"] for a in aggs}},
              open(os.path.join(LOGS, "analysis.json"), "w"), ensure_ascii=False, indent=2, default=str)
    print(f"\nwrote logs/analysis.json")

if __name__ == "__main__":
    main()
