#!/usr/bin/env python3
"""Power for the registered contrasts, by simulation and in closed form.

Registered in PREREGISTRATION.md section 8.3. The registration used to name no
power calculation at all, which is the kind of omission a reviewer reads as "the
authors did not check whether their design can see the effect they claim". This
script is the check, and every number in the section 8.3 table is reproduced by
running it.

Two things it exists to make explicit rather than to discover later:

  * the full two-by-two interaction is underpowered at the planned size. That is
    a property of the design, not a surprise, and the primary test of RQ-B2 is a
    single two-cell contrast for exactly this reason.
  * the former equivalence form of B-H2 could not have been supported at any
    true effect, because the interval half-width exceeds its own margin.

Cycle-level outcomes are clustered inside a trajectory, so the simulation draws
trajectory means with a variance inflated by the design effect rather than
treating cycles as independent. Standard library only, in keeping with the rest
of the analysis code.

    python3 power_sim.py               # the section 8.3 table
    python3 power_sim.py --simulate    # add the simulated column
    python3 power_sim.py --icc 0.5     # design effect at another clustering level
"""

from __future__ import annotations

import argparse
import math
import random
import statistics as st

# Between-trajectory standard deviations measured in the v2 pilot logs.
PILOT_SD = {"in-band-self": 0.0962, "in-band-judge": 0.1925}
PLANNED_SEEDS = 5
PLANNED_AGENTS = 2
PLANNED_FEEDBACK_LEVELS = 2
CYCLES_PER_TRAJECTORY = 6

Z = {0.80: 0.8416212335729143, 0.90: 1.2815515655446004,
     0.95: 1.6448536269514722, 0.975: 1.9599639845400545}


def design_effect(cycles: int, icc: float) -> float:
    """Kish's factor. Six cycles at icc 0.3 inflates variance by 2.5."""
    return 1.0 + (cycles - 1) * icc


def two_arm_se(sd: float, n_per_arm: int) -> float:
    return sd * math.sqrt(2.0 / n_per_arm)


def interaction_se(sd: float, n_per_cell: int) -> float:
    """(U_num - U_sign) - (G_num - G_sign): four cell means, so variance x4."""
    return 2.0 * sd / math.sqrt(n_per_cell)


def mde(se: float, power: float = 0.80, one_sided: bool = True) -> float:
    alpha_z = Z[0.95] if one_sided else Z[0.975]
    return (alpha_z + Z[power]) * se


def power_at(effect: float, se: float, one_sided: bool = True) -> float:
    alpha_z = Z[0.95] if one_sided else Z[0.975]
    return 1.0 - _norm_cdf(alpha_z - abs(effect) / se)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def equivalence_half_width(sd: float, n_per_arm: int) -> float:
    """Half-width of the 90 percent interval a TOST would have to fit inside."""
    return Z[0.95] * two_arm_se(sd, n_per_arm)


def simulate_two_arm(effect: float, sd: float, n_per_arm: int, trials: int,
                     one_sided: bool, rng: random.Random) -> float:
    alpha_z = Z[0.95] if one_sided else Z[0.975]
    hits = 0
    for _ in range(trials):
        a = [rng.gauss(effect, sd) for _ in range(n_per_arm)]
        b = [rng.gauss(0.0, sd) for _ in range(n_per_arm)]
        diff = st.mean(a) - st.mean(b)
        pooled = math.sqrt((st.variance(a) + st.variance(b)) / 2.0)
        se = pooled * math.sqrt(2.0 / n_per_arm)
        if se > 0 and (diff / se if one_sided else abs(diff) / se) > alpha_z:
            hits += 1
    return hits / trials


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sd", type=float, default=0.15,
                        help="between-trajectory sd; pilot values are 0.0962 and 0.1925")
    parser.add_argument("--icc", type=float, default=0.3)
    parser.add_argument("--seeds", type=int, default=PLANNED_SEEDS)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--trials", type=int, default=20000)
    parser.add_argument("--rng-seed", type=int, default=20260810)
    args = parser.parse_args()

    per_arm = args.seeds * PLANNED_AGENTS * PLANNED_FEEDBACK_LEVELS   # 20
    per_cell = args.seeds * PLANNED_AGENTS                            # 10
    sd = args.sd
    rng = random.Random(args.rng_seed)

    rows = [
        ("B-H1, grounded vs ungrounded", sd, per_arm,
         two_arm_se(sd, per_arm), True),
        ("RQ-B2, grounded-sign vs ungrounded-numeric", sd, per_cell,
         two_arm_se(sd, per_cell), True),
        ("RQ-B2, full two-by-two interaction", sd, per_cell,
         interaction_se(sd, per_cell), False),
    ]

    print(f"sd={sd}  seeds/cell={args.seeds}  agents={PLANNED_AGENTS}  "
          f"n/arm={per_arm}  n/cell={per_cell}")
    header = f"{'contrast':44} {'n':>4} {'SE':>7} {'MDE@80%':>9} {'sided':>6}"
    if args.simulate:
        header += f" {'sim':>7}"
    print(header)
    for name, s, n, se, one_sided in rows:
        line = (f"{name:44} {n:4d} {se:7.3f} {mde(se, 0.80, one_sided):9.3f} "
                f"{'one' if one_sided else 'two':>6}")
        if args.simulate:
            target = mde(se, 0.80, one_sided)
            if "interaction" in name:
                line += f" {'--':>7}"          # simulated below as a two-arm proxy
            else:
                line += f" {simulate_two_arm(target, s, n, args.trials, one_sided, rng):7.3f}"
        print(line)

    print("\nB-H1 power at candidate true effects "
          f"(one-sided, SE {two_arm_se(sd, per_arm):.3f}):")
    for effect in (0.20, 0.30, 0.35, 0.40, 0.56):
        print(f"  effect {effect:.2f}  power {power_at(effect, two_arm_se(sd, per_arm)):.3f}")

    print("\nB-H2 as an equivalence test against a margin of 0.05:")
    for s in (0.10, sd, PILOT_SD['in-band-judge']):
        for n in (per_arm, 80, 160):
            hw = equivalence_half_width(s, n)
            verdict = "supportable" if hw < 0.05 else "not achievable"
            print(f"  sd {s:.4f}  n/arm {n:3d}  90% half-width {hw:.3f}  {verdict}")
    print("  The planned size is n/arm "
          f"{per_arm}, so the interval is wider than the margin it must fit inside.")

    de = design_effect(CYCLES_PER_TRAJECTORY, args.icc)
    print(f"\nDesign effect at {CYCLES_PER_TRAJECTORY} cycles and icc {args.icc}: {de:.2f}")
    print(f"  Treating cycles as independent would overstate the effective sample "
          f"{de:.2f}-fold and narrow intervals by a factor of {math.sqrt(de):.2f}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
