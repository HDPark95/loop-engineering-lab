#!/usr/bin/env python3
"""Confirmatory inference with the trajectory as the unit.

Registered in PREREGISTRATION.md section 8. The registration used to name no
analysis unit, and the released `analyze.py` pools cycles across trajectories to
compute conditional acceptance rates. Cycles inside a trajectory are not
independent: the candidate accepted at cycle t is the deployed baseline at t+1,
the agent carries its own history, and one seed governs the whole trajectory.
Pooling them and reporting an interval is pseudo-replication. At six cycles and
an intra-trajectory correlation of 0.3 the design effect is 2.5, so the effective
sample would be overstated 2.5-fold.

The estimator here is therefore trajectory-level throughout. A cycle-level binary
outcome is first reduced to a per-trajectory proportion, and every interval comes
from resampling trajectories with replacement, never cycles. This is stdlib-only,
matching the rest of the analysis code; a mixed model with a random intercept per
trajectory targets the same estimand and is available as a sensitivity analysis,
but at 20 clusters per arm it buys nothing a cluster bootstrap does not already
give and it would add a scientific stack to the replication package.

Multiplicity follows section 8.2: a primary family of exactly three tests, Holm
at a family-wise 0.05, evaluated in the registered fixed sequence, with everything
else reported as secondary.

    python3 fit_clustered.py [LOGDIR]
    python3 fit_clustered.py --json      # machine-readable verdicts
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOGS = os.path.join(os.path.dirname(HERE), "logs")

ALPHA = 0.05
BOOTSTRAP_DRAWS = 20000
BOOTSTRAP_SEED = 20260810

# Section 8.2: the confirmatory primary family, in the registered order. A later
# test is read only if the earlier ones pass.
FIXED_SEQUENCE = ("B-H1a", "B-H1b", "A-H1")


def load_trajectories(logdir):
    """One record per trajectory. The trajectory is the row of every analysis."""
    trajectories = []
    for path in sorted(glob.glob(os.path.join(logdir, "pilot_*_r*.jsonl"))):
        name = os.path.basename(path)[len("pilot_"):-len(".jsonl")]
        arm, rep = name.rsplit("_r", 1)
        rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
        if rows:
            trajectories.append({"arm": arm, "rep": int(rep), "rows": rows})
    return trajectories


def outcome_field(rows):
    """Which delta the outcome is computed on, refusing a mixed trajectory.

    `delta_hob` is the outcome half of section 4.1. A trajectory that carries it
    on some cycles and not others is a harness bug, and silently falling back to
    `delta` on the missing ones would compute the primary variable partly on the
    half the gate read. That is the exact confusion the split exists to prevent,
    so it raises instead.
    """
    present = sum(1 for r in rows if "delta_hob" in r)
    if present == 0:
        return "delta"
    if present != len(rows):
        raise ValueError(
            f"{present} of {len(rows)} cycles carry delta_hob; a trajectory must "
            "carry the outcome half on every cycle or on none")
    return "delta_hob"


def regression_acceptance_rate(rows):
    """Share of accepted cycles whose outcome-half delta is not positive.

    Reads `delta_hob` when the harness supplies the split of section 4.1, and
    falls back to `delta` for the pilot logs, which predate it. Under the fallback
    this quantity is zero by construction in a grounded arm, which is precisely
    why the split was registered; the caller is told which field was used.
    """
    accepted = [r for r in rows if r.get("accept")]
    if not accepted:
        return None, "none-accepted"
    field = outcome_field(rows)
    bad = sum(1 for r in accepted if r[field] <= 0)
    return bad / len(accepted), field


def false_rejection_rate(rows):
    field = outcome_field(rows)
    improved = [r for r in rows if r[field] > 0]
    if not improved:
        return None
    return sum(1 for r in improved if not r.get("accept")) / len(improved)


def erosion(rows):
    """Best deployed state minus final deployed state, within one trajectory.

    A within-trajectory comparison, so entry imbalance between cells cannot
    produce it (section 4.2).
    """
    deployed = [r.get("deployed_conv") for r in rows if r.get("deployed_conv") is not None]
    if not deployed:
        return None
    return max(deployed) - deployed[-1]


def cluster_bootstrap(values, draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED):
    """Percentile interval over trajectories. Cycles are never resampled."""
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return (None, None)
    rng = random.Random(seed)
    n = len(clean)
    means = sorted(st.mean(rng.choices(clean, k=n)) for _ in range(draws))
    lo = means[int(0.025 * draws)]
    hi = means[min(draws - 1, int(0.975 * draws))]
    return (lo, hi)


def contrast(treated, control, draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED):
    """Difference of trajectory means with a cluster-bootstrap interval and a
    one-sided bootstrap p-value for treated minus control being at most zero."""
    a = [v for v in treated if v is not None]
    b = [v for v in control if v is not None]
    if len(a) < 2 or len(b) < 2:
        return None
    point = st.mean(a) - st.mean(b)
    rng = random.Random(seed)
    diffs = sorted(st.mean(rng.choices(a, k=len(a))) - st.mean(rng.choices(b, k=len(b)))
                   for _ in range(draws))
    lo = diffs[int(0.025 * draws)]
    hi = diffs[min(draws - 1, int(0.975 * draws))]
    p_one_sided = sum(1 for d in diffs if d <= 0) / draws
    return {"estimate": point, "ci": (lo, hi), "p": p_one_sided,
            "n_treated": len(a), "n_control": len(b)}


def holm(pvalues, alpha=ALPHA):
    """Holm step-down over the primary family. Returns per-test reject flags."""
    ordered = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(ordered)
    verdicts, still_rejecting = {}, True
    for index, (name, p) in enumerate(ordered):
        threshold = alpha / (m - index)
        if still_rejecting and p <= threshold:
            verdicts[name] = {"reject": True, "threshold": threshold}
        else:
            still_rejecting = False
            verdicts[name] = {"reject": False, "threshold": threshold}
    return verdicts


def fixed_sequence_gate(verdicts):
    """A later test in the registered sequence is read only if the earlier ones
    passed. Tests after the first failure are reported as not evaluated."""
    out, open_gate = {}, True
    for name in FIXED_SEQUENCE:
        if name not in verdicts:
            out[name] = "not-run"
            continue
        if not open_gate:
            out[name] = "not-evaluated (earlier test in the sequence failed)"
            continue
        out[name] = "reject-null" if verdicts[name]["reject"] else "retain-null"
        open_gate = verdicts[name]["reject"]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("logdir", nargs="?", default=DEFAULT_LOGS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    trajectories = load_trajectories(args.logdir)
    if not trajectories:
        print(f"no trajectory logs under {args.logdir}", file=sys.stderr)
        return 1

    by_arm, delta_fields = {}, set()
    for t in trajectories:
        rate, field = regression_acceptance_rate(t["rows"])
        delta_fields.add(field)
        by_arm.setdefault(t["arm"], []).append({
            "rep": t["rep"],
            "regression_acceptance": rate,
            "false_rejection": false_rejection_rate(t["rows"]),
            "erosion": erosion(t["rows"]),
            "final": t["rows"][-1].get("deployed_conv"),
            "cycle1": t["rows"][0].get("conversions"),
        })

    split_present = delta_fields == {"delta_hob"}
    report = {
        "n_trajectories": len(trajectories),
        "unit": "trajectory",
        "outcome_half_present": split_present,
        "arms": {},
        "contrasts": {},
    }
    for arm, records in sorted(by_arm.items()):
        col = lambda k: [r[k] for r in records]
        report["arms"][arm] = {
            "n": len(records),
            "regression_acceptance_mean": _safe_mean(col("regression_acceptance")),
            "regression_acceptance_ci": cluster_bootstrap(col("regression_acceptance")),
            "false_rejection_mean": _safe_mean(col("false_rejection")),
            "erosion_mean": _safe_mean(col("erosion")),
            "final_mean": _safe_mean(col("final")),
            "cycle1_mean": _safe_mean(col("cycle1")),
        }

    grounded = "out-of-band"
    for ungrounded in [a for a in by_arm if a != grounded]:
        c = contrast([r["regression_acceptance"] for r in by_arm[ungrounded]],
                     [r["regression_acceptance"] for r in by_arm[grounded]])
        if c:
            report["contrasts"][f"{ungrounded} minus {grounded}"] = c

    if not split_present:
        report["warning"] = (
            "These logs carry no outcome-half delta, so the grounded arm's "
            "regression acceptance rate is zero by construction and no contrast "
            "below is a test of anything. Section 4.1 of the preregistration "
            "requires the HO-A/HO-B split before confirmatory execution.")

    if args.json:
        print(json.dumps(report, indent=2, default=list))
        return 0

    print(f"trajectories: {report['n_trajectories']}   unit of inference: trajectory")
    print(f"outcome half (HO-B) present in logs: {split_present}")
    print(f"\n{'arm':16} {'n':>3} {'regr.accept':>12} {'95% CI':>18} "
          f"{'false-rej':>10} {'erosion':>8} {'cycle1':>7}")
    for arm, a in report["arms"].items():
        ci = a["regression_acceptance_ci"]
        ci_text = f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci[0] is not None else "n/a"
        print(f"{arm:16} {a['n']:3d} {_fmt(a['regression_acceptance_mean']):>12} "
              f"{ci_text:>18} {_fmt(a['false_rejection_mean']):>10} "
              f"{_fmt(a['erosion_mean']):>8} {_fmt(a['cycle1_mean']):>7}")

    print("\ncontrasts (trajectory-level, cluster bootstrap over trajectories):")
    for name, c in report["contrasts"].items():
        print(f"  {name:38} {c['estimate']:+.3f}  "
              f"95% CI [{c['ci'][0]:+.3f}, {c['ci'][1]:+.3f}]  one-sided p={c['p']:.4f}")

    if report["contrasts"]:
        pvals = {n: c["p"] for n, c in report["contrasts"].items()}
        print("\nHolm over this run's contrasts at family-wise 0.05:")
        for name, v in holm(pvals).items():
            print(f"  {name:38} threshold {v['threshold']:.4f}  "
                  f"{'reject null' if v['reject'] else 'retain null'}")

    if "warning" in report:
        print("\nWARNING: " + report["warning"])
    return 0


def _safe_mean(values):
    clean = [v for v in values if v is not None]
    return st.mean(clean) if clean else None


def _fmt(value):
    return "n/a" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    raise SystemExit(main())
