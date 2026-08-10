# Software-engineering task families

These tasks extend the controlled apparatus from a synthetic signup funnel to
software-engineering outcomes. They are apparatus fixtures, not research
results.

## Isolation contract

Each task has two surfaces:

- `seed/`: the issue, source, and public tests mounted read-write in the agent
  container.
- `oracle.py`: held-out tests or workloads copied into the oracle image. The
  agent container never mounts this path.

Both containers can access the candidate artifact volume, but only the oracle
can access its evaluator. The oracle mounts the artifact read-only and has no
network. A task-specific canary is scanned in the candidate before every
evaluation; a hit invalidates the run.

**Grading runs out of process.** Until 2026-08 the oracles imported the
candidate with `importlib` and called it inside their own interpreter, which put
the candidate and the answer key in one address space. A candidate could read
the canary and the workload out of `sys.modules['__main__']` at run time without
writing either to disk, so the file-scanning canary check reported clean while
the leak happened. That is now closed: `_sandbox/run_candidate.py` executes the
candidate in a separate interpreter whose working directory is the candidate
directory, the parent sends inputs and receives outputs, and expected values,
the canary, and the score function never enter the child. `test_oracle_integrity.py`
asserts that a probing candidate reaches none of those symbols.

Effort is counted by the runner rather than reported by the candidate. A
candidate that calls `sys.settrace(None)` to hide its work invalidates the
trajectory instead of appearing cheap.

## Task families

- S1 (`s1_defect_repair`): repair semantic-version comparison. The public tests
  cover ordinary versions; the held-out regression tests cover multi-digit
  components, malformed versions, and unequal lengths.
- S3 (`s3_production_ops`): harden a request handler under a hidden deterministic
  workload. Every response is checked against an answer the oracle computes
  independently, and effort is the line count observed by the runner. The score
  combines error rate, restarts, and an efficiency term.

  The earlier version of this oracle derived its latency percentile from the
  `work_units` integer the candidate returned about itself and never checked any
  answer. A handler that did nothing and reported zero scored a perfect 100.0
  while the seed scored 0.0, so the global optimum of the metric was to stop
  working, and the scripted improvement shipped in `se_experiment.py` was itself
  that hack. Both are fixed, and `test_oracle_integrity.py` now fails if a
  do-nothing candidate ever outscores the seed again.

  The seed was also made to do the quadratic work its issue text describes. It
  previously computed `len(payload) * len(payload)`, which is constant work, so
  there was no inefficiency for a candidate to remove or for the oracle to
  measure.

Use `python3 se_experiment.py --smoke-output results/se_smoke_matrix.json` to
exercise both oracles and all four factor cells with scripted candidates.

Run `python3 -m unittest test_oracle_integrity` on every change to either
oracle. It grades three adversarial baselines (a do-nothing candidate, the
shipped seed, and a correct reference) and asserts an ordering rather than a
number: doing nothing must score below the seed, and the seed must score below
the reference and above the floor. A seed pinned at zero is also a defect,
because a loop measuring deltas against a floor cannot tell a partial repair
from no repair.

Use `agent_adapters.py` for a disposable one-shot protocol check with either
Codex or Claude Code. The result retains run metadata, execution and isolation
status, aggregate scores, changed file names, timing, tokens, billing mode,
CLI-reported API-price-equivalent cost, and incremental billed cost. It
discards model text and source contents. Alias or session-default model
selections are acceptable only for an
apparatus smoke. Confirmatory runs require an immutable model identifier and a
frozen total cost ceiling in the preregistration.

When the host cannot create a nested CLI sandbox, pass `--container-image` plus
either `--auth-file` or `--auth-env`. The adapter then uses the agent image as
an external sandbox and mounts only the disposable task directory plus a
mode-0600 copy of the authentication file in a disposable private directory,
or forwards the named environment variable
through a mode-0600 temporary environment file without putting its value on the
command line. The file is outside the task mount and is deleted with the
disposable run directory. The adapter does not mount the repository or oracle.
The bypass flag used inside that container must never be used directly on a
research host.

The committed adapter smokes are operational records, not study data. Codex
completed S1 in the task-only container and improved the held-out score from
0.111111 to 1.0. Claude completed the same task-only-container protocol and
also improved the score from 0.111111 to 1.0. Claude Code reported 0.086998 USD
as an API-price-equivalent estimate, but the run used Max 20x subscription
OAuth and therefore incurred 0 USD in incremental billing while consuming plan
quota. Both source changes were discarded after scoring. This satisfies the
adapter protocol smoke gate but is not a confirmatory model comparison.
