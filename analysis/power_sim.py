#!/usr/bin/env python3
"""Power calculation for the two paired block contrasts in the primary family.

The input SD is the between-block standard deviation of a task-agent-seed
grounding contrast after each trajectory has already been reduced to one
incidence. It is not a cycle-level SD, so no ICC or design-effect parameter is
applied a second time.
"""

from __future__ import annotations

import argparse
import math
import random

if __package__:
    from .fit_clustered import exact_sign_flip_p
else:
    from fit_clustered import exact_sign_flip_p


PLANNED_SEEDS = 5
PLANNED_AGENTS = 2
DEFAULT_BLOCK_SD = 0.15
DEFAULT_EFFECT = 0.20
DEFAULT_ALPHA_PER_TEST = 0.025
Z = {
    0.80: 0.8416212335729143,
    0.95: 1.6448536269514722,
    0.975: 1.9599639845400545,
}


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _z_for_one_sided_alpha(alpha: float) -> float:
    if math.isclose(alpha, 0.05):
        return Z[0.95]
    if math.isclose(alpha, 0.025):
        return Z[0.975]
    raise ValueError("supported one-sided alpha values are 0.05 and 0.025")


def standard_error(block_sd: float, blocks: int) -> float:
    if blocks <= 0:
        raise ValueError("blocks must be positive")
    if block_sd < 0:
        raise ValueError("block_sd must be non-negative")
    return block_sd / math.sqrt(blocks)


def mde(block_sd: float, blocks: int, alpha: float, power: float = 0.80) -> float:
    if power not in Z:
        raise ValueError(f"supported power values are {sorted(Z)}")
    return (_z_for_one_sided_alpha(alpha) + Z[power]) * standard_error(block_sd, blocks)


def power_at(effect: float, block_sd: float, blocks: int, alpha: float) -> float:
    z = effect / standard_error(block_sd, blocks)
    return 1.0 - _norm_cdf(_z_for_one_sided_alpha(alpha) - z)


def simulate(
    effect: float,
    block_sd: float,
    blocks: int,
    alpha: float,
    trials: int,
    seed: int,
) -> float:
    if trials <= 0:
        raise ValueError("trials must be positive")
    standard_error(block_sd, blocks)
    rng = random.Random(seed)
    _z_for_one_sided_alpha(alpha)
    hits = 0
    for _ in range(trials):
        values = [rng.gauss(effect, block_sd) for _ in range(blocks)]
        pvalue = exact_sign_flip_p(values)
        if pvalue is not None and pvalue <= alpha:
            hits += 1
    return hits / trials


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sd",
        type=float,
        default=DEFAULT_BLOCK_SD,
        help="SD of paired task-agent-seed block differences",
    )
    parser.add_argument("--seeds", type=int, default=PLANNED_SEEDS)
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA_PER_TEST)
    parser.add_argument("--effect", type=float, default=DEFAULT_EFFECT)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--trials", type=int, default=20_000)
    parser.add_argument("--rng-seed", type=int, default=20260813)
    args = parser.parse_args()
    blocks = args.seeds * PLANNED_AGENTS
    if args.seeds < 1:
        parser.error("--seeds must be at least 1 so the design has two or more blocks")
    if args.trials < 1:
        parser.error("--trials must be positive")
    se = standard_error(args.sd, blocks)
    detectable = mde(args.sd, blocks, args.alpha)
    power = power_at(args.effect, args.sd, blocks, args.alpha)
    print(
        f"block_sd={args.sd:.3f} blocks/task={blocks} one-sided_alpha={args.alpha:.3f} "
        f"SE={se:.3f} MDE@80%={detectable:.3f} power(effect={args.effect:.2f})={power:.3f}"
    )
    print("B-H1a S1: same registered block design")
    print("B-H1b S3: same registered block design")
    if args.simulate:
        value = simulate(
            args.effect,
            args.sd,
            blocks,
            args.alpha,
            args.trials,
            args.rng_seed,
        )
        print(f"exact-sign-flip simulation power={value:.3f} trials={args.trials}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
