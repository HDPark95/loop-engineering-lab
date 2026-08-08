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

## Task families

- S1 (`s1_defect_repair`): repair semantic-version comparison. The public tests
  cover ordinary versions; the held-out regression tests cover multi-digit
  components, malformed versions, and unequal lengths.
- S3 (`s3_production_ops`): harden a request handler under a hidden deterministic
  workload. The oracle reports error rate, logical p95 latency, and restart
  count, then converts them to a frozen higher-is-better score.

Use `python3 se_experiment.py --smoke-output results/se_smoke_matrix.json` to
exercise both oracles and all four factor cells with scripted candidates.

Use `agent_adapters.py` for a disposable one-shot protocol check with either
Codex or Claude Code. The command records only aggregate score, changed file
names, timing, tokens, and reported cost; it discards model text and source
changes. Alias or session-default model selections are acceptable only for an
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
also improved the score from 0.111111 to 1.0, with a reported cost of 0.086998
USD. Both source changes were discarded after scoring. This satisfies the
adapter protocol smoke gate but is not a confirmatory model comparison.
