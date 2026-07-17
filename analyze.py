#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mechanical analysis of the real-agent pilot logs (no human discretion).
Reads every append-only trajectory log logs/pilot_<arm>_r<rep>.jsonl, recomputes
per-trajectory metrics from the raw rows, aggregates per arm with bootstrap CIs,
and emits the dependent variables and the preregistered hypothesis verdicts.
Pure standard library (PREREGISTRATION policy). Single source of truth = the raw
cycle logs. Run: python3 analyze.py"""
import json, os, glob, statistics as st, random, sys

LAB = os.path.dirname(os.path.abspath(__file__))
LOGS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(LAB, "logs")
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
    # PRIMARY (amended) definition: mirage = accepted cycles whose oracle delta is <= 0.
    # The registered draft wording said "delta = 0"; both are computed and reported so
    # the definitional amendment is fully transparent (see PREREGISTRATION amendment).
    mirage = (sum(1 for r in acc if r["delta"] <= 0) / len(acc)) if acc else 0.0
    mirage_eq0 = (sum(1 for r in acc if r["delta"] == 0) / len(acc)) if acc else 0.0
    baseline = rows[0]["conversions"] - rows[0]["delta"]   # delta_1 = conv_1 - baseline
    # real outcome = the deployed (last accepted) state at budget end; a rejected
    # final candidate is reverted, so this is not necessarily the last candidate.
    final = rows[-1].get("deployed_conv", rows[-1]["conversions"])
    gain = final - baseline
    peak = max([baseline] + [r["deployed_conv"] for r in rows if "deployed_conv" in r])
    ttf = next((r["cycle"] for r in rows if r["delta"] > 0), None)  # None = right-censored
    wasted = sum(1 for r in rows if r["delta"] <= 0) / len(rows)
    accept_rate = len(acc) / len(rows)
    claim_rate = sum(1 for r in rows if r.get("claim_improved")) / len(rows)
    # discrimination of the gate: acceptance conditioned on the true delta sign.
    neg = [r for r in rows if r["delta"] <= 0]
    pos = [r for r in rows if r["delta"] > 0]
    acc_given_neg = (sum(1 for r in neg if r["accept"]) / len(neg)) if neg else None
    acc_given_pos = (sum(1 for r in pos if r["accept"]) / len(pos)) if pos else None
    false_reject = (sum(1 for r in pos if not r["accept"]) / len(pos)) if pos else None
    canary_leaks = sum(1 for r in rows if r.get("canary_leak"))
    return {"mirage_rate": mirage, "mirage_rate_eq0": mirage_eq0, "baseline": baseline,
            "final": final, "real_gain": gain, "peak_deployed": peak,
            "ttf": ttf, "wasted_cycle_ratio": wasted, "accept_rate": accept_rate,
            "claim_rate": claim_rate, "acc_given_neg": acc_given_neg,
            "acc_given_pos": acc_given_pos, "false_reject_rate": false_reject,
            "canary_leaks": canary_leaks,
            "n_neg": len(neg), "n_pos": len(pos), "n_cycles": len(rows)}

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
    allrows = [r for t in trajs for r in t["rows"]]
    def col(k):
        return [m[k] for m in ms]
    mir = col("mirage_rate")
    gain = col("real_gain")
    fin = col("final")
    ttfs = [m["ttf"] for m in ms if m["ttf"] is not None]
    # pooled (cycle-level) discrimination across the arm's trajectories
    neg = [r for r in allrows if r["delta"] <= 0]
    pos = [r for r in allrows if r["delta"] > 0]
    pooled_acc_neg = (sum(1 for r in neg if r["accept"]) / len(neg)) if neg else None
    pooled_acc_pos = (sum(1 for r in pos if r["accept"]) / len(pos)) if pos else None
    return {
        "arm": arm, "n_traj": len(ms),
        "mirage_mean": st.mean(mir), "mirage_ci": boot_ci(mir),
        "mirage_eq0_mean": st.mean(col("mirage_rate_eq0")),
        "real_gain_mean": st.mean(gain), "real_gain_ci": boot_ci(gain),
        "final_mean": st.mean(fin),
        "peak_deployed_mean": st.mean(col("peak_deployed")),
        "baseline_mean": st.mean(col("baseline")),
        "ttf_mean": (st.mean(ttfs) if ttfs else None), "ttf_censored": sum(1 for m in ms if m["ttf"] is None),
        "wasted_mean": st.mean(col("wasted_cycle_ratio")),
        "accept_rate_mean": st.mean(col("accept_rate")),
        "claim_rate_mean": st.mean(col("claim_rate")),
        "pooled_acc_given_neg": pooled_acc_neg, "pooled_acc_given_pos": pooled_acc_pos,
        "pooled_false_reject": (1 - pooled_acc_pos) if pooled_acc_pos is not None else None,
        "pooled_n_neg": len(neg), "pooled_n_pos": len(pos),
        "canary_leaks_total": sum(col("canary_leaks")),
        "ci_note": "bootstrap percentile interval over n=3 trajectories; with n=3 this "
                   "reads as an observed range, not a calibrated 95% interval",
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

    # gate discrimination (base-rate independent evidence)
    print("\nGATE DISCRIMINATION (pooled cycles: acceptance conditioned on true delta sign)")
    for arm in order:
        a = aggs[arm]
        an = f"{a['pooled_acc_given_neg']:.3f}" if a["pooled_acc_given_neg"] is not None else "n/a"
        ap_ = f"{a['pooled_acc_given_pos']:.3f}" if a["pooled_acc_given_pos"] is not None else "n/a"
        fr = f"{a['pooled_false_reject']:.3f}" if a["pooled_false_reject"] is not None else "n/a"
        print(f"  {arm:16} P(accept|delta<=0)={an} (n={a['pooled_n_neg']})  "
              f"P(accept|delta>0)={ap_} (n={a['pooled_n_pos']})  false-reject={fr}  "
              f"claim_rate={a['claim_rate_mean']:.2f}  canary_leaks={a['canary_leaks_total']}")

    # preregistered hypothesis verdicts (thresholds frozen in PREREGISTRATION.md)
    print("\nPREREGISTERED HYPOTHESIS VERDICTS (primary = amended mirage definition, delta<=0)")
    if "in-band-self" in aggs and "out-of-band" in aggs:
        d = aggs["in-band-self"]["mirage_mean"] - aggs["out-of-band"]["mirage_mean"]
        d0 = aggs["in-band-self"]["mirage_eq0_mean"] - aggs["out-of-band"]["mirage_eq0_mean"]
        print(f"  H1 (in-band-self mirage - out-of-band >= 0.20): diff={d:+.2f}  -> {'HIT' if d >= 0.20 else 'NOT MET'}"
              f"   [as-registered delta==0 definition: diff={d0:+.2f} -> {'HIT' if d0 >= 0.20 else 'NOT MET'}]")
    if "in-band-judge" in aggs and "out-of-band" in aggs:
        d = aggs["in-band-judge"]["mirage_mean"] - aggs["out-of-band"]["mirage_mean"]
        d0 = aggs["in-band-judge"]["mirage_eq0_mean"] - aggs["out-of-band"]["mirage_eq0_mean"]
        verdict = "HA1 ADOPTED (judge closes gap; reframe as judge quality)" if d <= 0.05 \
                  else "HA1 REJECTED (grounding matters; strong judge does NOT close gap)"
        print(f"  HA1 (in-band-judge mirage - out-of-band <= 0.05): diff={d:+.2f}  -> {verdict}"
              f"   [as-registered: diff={d0:+.2f}]")
    if "in-band-self" in aggs and "in-band-judge" in aggs:
        d = aggs["in-band-self"]["mirage_mean"] - aggs["in-band-judge"]["mirage_mean"]
        print(f"  (in-band-self mirage - in-band-judge): diff={d:+.2f}  (does a critical judge help at all)")

    # H2 boundary-condition verdict against the registered threshold, when a
    # boundary-task (B1) analysis directory is supplied as the second argument.
    if len(sys.argv) > 2:
        b1trajs = {}
        for path in sorted(glob.glob(os.path.join(sys.argv[2], "pilot_*_r*.jsonl"))):
            name = os.path.basename(path)[len("pilot_"):-len(".jsonl")]
            arm, rep = name.rsplit("_r", 1)
            rows = [json.loads(l) for l in open(path) if l.strip()]
            if rows:
                b1trajs.setdefault(arm, []).append({"rep": int(rep), "rows": rows})
        if b1trajs:
            b1aggs = {arm: agg(arm, b1trajs[arm]) for arm in b1trajs}
            print("\nH2 BOUNDARY CONDITION (registered threshold: |gap| <= 0.05 on the visible-spec task)")
            for jarm in ("in-band-judge", "in-band-self"):
                if jarm in b1aggs and "out-of-band" in b1aggs:
                    g = b1aggs[jarm]["mirage_mean"] - b1aggs["out-of-band"]["mirage_mean"]
                    verdict = "MET" if abs(g) <= 0.05 else "NOT MET (direction supported iff gap shrinks vs T1)"
                    print(f"  H2 [{jarm}]: B1 gap={g:+.2f} -> {verdict}")

    json.dump({"aggregates": {a: {k: v for k, v in aggs[a].items() if k != "per_traj"} for a in aggs},
               "per_traj": {a: aggs[a]["per_traj"] for a in aggs},
               "n_trajectories": {a: aggs[a]["n_traj"] for a in aggs}},
              open(os.path.join(LOGS, "analysis.json"), "w"), ensure_ascii=False, indent=2, default=str)
    print(f"\nwrote {os.path.join(LOGS, 'analysis.json')}")

if __name__ == "__main__":
    main()
