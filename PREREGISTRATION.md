# Revised preregistration: evaluator grounding in agentic software engineering

Status: **revised draft, not frozen**. No confirmatory measurement may start
until every freeze gate in Section 11 is satisfied and the resulting commit hash
is recorded. Earlier funnel and boundary measurements remain exploratory pilot
evidence and are not promoted retrospectively.

This revision was prepared for the Agentic Software Engineering special-issue
study after the original manuscript received an out-of-scope decision because
its connection to software engineering was too weak. It moves the controlled
tasks to defect repair and production operations, adds a field-observation
layer, separates gate grounding from oracle information, and mechanizes cost.

## 1. Research questions

- RQ-A1: How often do coding-agent PR bodies explicitly claim completion or
  verification, and how are those claims associated with independently
  observable review outcomes?
- RQ-A2: Within agent and task strata, do explicit claims distinguish merged
  from closed-unmerged PRs or predict changes-requested reviews?
- RQ-B1: Does a gate grounded in a held-out software outcome reject
  no-improvement cycles more accurately than an ungrounded gate?
- RQ-B2: Is the effect attributable to the gate decision rather than numeric
  oracle information fed back to the agent?
- RQ-B3: Does the grounding effect persist across defect repair, production
  operations, and the original generalization task, while disappearing on a
  transcript-verifiable bounded task?
- RQ-C1: At what token, time, and dollar cost does grounded gating cease to
  improve delivered outcome per budget?

## 2. Mapping from the original registration

| Original item | Revised item | Disposition |
| --- | --- | --- |
| H1: in-band self-evaluation has higher mirage rate | B-H1: ungrounded gates have higher mirage rate on S1, S3, and G1 | Retained and moved to SE tasks |
| H2: gap disappears on bounded tasks | B-H2: absolute mirage-rate gap on B1 is at most 0.05 | Retained unchanged |
| H3: outcome reward shortens time to first outcome | B-H3: outcome reward remains a registered secondary axis after the primary 2x2 | Retained, not silently dropped |
| HA1: strong in-band judge closes the gap | B-HA1: add a strong-judge ungrounded comparator to the primary matrix | Retained |
| HA2: out-of-band cost offsets benefit | C-H1: compare outcome per 1,000 tokens, per dollar, and per wall-clock hour | Retained and mechanized |
| T1 signup funnel | G1 generalization arm | Retained as non-SE generalization evidence, not the main task |
| B1 structural specification | B1 boundary control | Retained |

## 3. Field layer A: AIDev

### 3.1 Source and pilot separation

The source is the public CC BY 4.0 dataset `hao-li/AIDev`, frozen at revision
`68ed5f4b80d27a9e057fc57567f38bd322ac73ec`. The enriched AIDev-pop tables
contain 33,596 agent PRs and 6,618 human-comparison PRs. The deterministic
10,000-PR feasibility sample and its observed aggregates are exploratory and
will not be reused as a confirmatory test set.

The confirmatory agent set is the remaining 23,596 enriched PRs after excluding
the SHA-256-ranked pilot sample. The claim classifier is frozen before outcomes
on that set are queried.

### 3.2 Claim construct

The unit is a PR. The primary exposure is an explicit completion or verification
assertion in the PR body. Titles alone do not qualify. Before freezing:

1. Draw 400 PRs from the exploratory sample, stratified by agent and preliminary
   lexical claim status.
2. Have two annotators independently label completion claim, verification claim,
   assertion strength, and unclassifiable language.
3. Freeze written guidelines and a deterministic classifier only if both claim
   presence precision and recall are at least 0.80 against adjudicated labels.
4. If either threshold fails, replace the lexical classifier and repeat on a new
   400-PR exploratory subset. No confirmatory outcomes may be inspected during
   this iteration.

Non-English PR bodies are a prespecified subgroup. If a language has fewer than
100 confirmatory PRs with validated coding coverage, it is reported but excluded
from claim-outcome estimation.

### 3.3 Outcomes

Primary field outcome: merged versus closed-unmerged among resolved PRs.

Secondary outcomes:

- any `CHANGES_REQUESTED` review;
- any review;
- time from creation to merge or closure where timestamps are available;
- within-PR revert marker, explicitly labeled as process behavior rather than a
  post-merge outcome.

Post-merge revert is excluded from the frozen field analysis. The released
tables do not timestamp commit events or link a later revert PR to the earlier
merged PR. It can be added only through a separately preregistered mining pass.

### 3.4 Field estimands

Report claim prevalence and outcome rates by agent and task type. The primary
association is a common odds ratio stratified by agent x task type, with every
stratum table published. Also report risk differences within each agent and
task type. The unadjusted pooled difference is descriptive only because the
feasibility pilot found large agent-template heterogeneity.

Human PRs are a secondary benchmark. No agent-versus-human causal effect is
claimed unless repository and task-type overlap is established and reported.

## 4. Controlled layer B: SE tasks and oracle isolation

Task families:

- S1 defect repair: the agent edits a real program against public tests; the
  oracle runs held-out regression tests inaccessible to the agent.
- S3 production operations: the agent hardens a service; the oracle runs a
  hidden workload and measures error rate, p95 logical latency, and restarts.
- G1 generalization: the original signup-funnel task, retained to test whether
  the mechanism extends outside SE.
- B1 boundary control: a structural specification directly verifiable from the
  artifact.

The agent container mounts only the issue, public tests, and candidate artifact.
The oracle container contains held-out evaluation code, mounts the artifact
read-only, and has no network. Every trajectory begins with a mount/network
boundary proof, and every candidate is scanned for the task canary. A leak
invalidates the whole trajectory before outcome inspection.

## 5. Primary 2x2 factorization

The primary manipulation crosses:

1. gate grounding: held-out oracle delta versus ungrounded claim/judge verdict;
2. oracle feedback: numeric metrics versus sign only.

| Cell | Gate decision | Feedback to next cycle |
| --- | --- | --- |
| grounded-numeric | accept iff held-out delta is positive | delta and frozen metrics |
| grounded-sign | accept iff held-out delta is positive | helped / did not help only |
| ungrounded-numeric | accept from claim or strong judge | oracle delta and metrics |
| ungrounded-sign | accept from claim or strong judge | helped / did not help only |

This separates the causal effect of the gate from the coaching value of oracle
information. The independent monitor records the oracle trajectory in all cells
but never changes an ungrounded decision.

B-HA1 uses a strong in-band judge as an additional ungrounded comparator with
the same artifact, diff, history, and critical default-reject prompt used in the
pilot. B-H3 repeats the primary matrix under process reward only after the
outcome-reward matrix is complete; it is a registered secondary analysis.

## 6. Confirmatory hypotheses and thresholds

- B-H1: on each of S1 and S3, the ungrounded-minus-grounded mirage-rate
  difference is at least 0.20, averaged over the feedback factor.
- B-H2: on B1, the absolute ungrounded-minus-grounded mirage-rate difference is
  at most 0.05.
- B-H3: outcome reward reduces median cycles to first positive oracle delta
  relative to process reward.
- B-HA1: if the strong-judge mirage rate differs from the corresponding grounded
  rate by at most 0.05 on both S1 and S3, the judge-quality hypothesis is
  supported and the paper's mechanism must be narrowed.
- C-H1: grounded gating is practically preferred only where its final delivered
  gain remains positive per 1,000 tokens and per dollar. Break-even points are
  reported rather than hidden when unfavorable.

No hypothesis is accepted from direction alone. Estimates, per-seed values, and
intervals are reported even when thresholds are missed.

## 7. Dependent variables

- progress-mirage rate: accepted cycles with oracle delta at most zero divided
  by accepted cycles;
- final delivered score and gain from the initial deployed score;
- cycles to first positive oracle delta, right-censored at the cycle budget;
- false-rejection rate: rejected cycles with positive oracle delta divided by
  positive-delta cycles;
- regression acceptance rate: accepted cycles with negative oracle delta;
- input tokens, output tokens, agent seconds, judge seconds, oracle seconds,
  and dollars per cycle;
- gain per 1,000 tokens, gain per dollar, and gain per wall-clock hour.

## 8. Planned size

The minimum confirmatory core is:

- four tasks (S1, S3, G1, B1);
- two independently implemented coding agents;
- four primary cells;
- five seeds per task-agent-cell;
- six cycles per trajectory.

This is 160 trajectories and 960 cycles. Agent identities, exact model versions,
prices, prompts, and seeds are written into this document immediately before
freeze. A failed or unavailable agent is removed before freeze, never after its
outcome is seen.

## 9. Exclusions and failure handling

- Boundary or canary failure: invalidate the trajectory and stop the cell.
- Agent/tool crash before a candidate exists: operational failure; retain the
  cost and rerun once with the same seed. A second failure is reported and not
  silently replaced.
- Oracle crash: no gate decision; repair before any affected cell resumes and
  record an amendment.
- Unparseable ungrounded judge verdict: reject by default.
- Open field PR: exclude from the resolved-PR primary outcome but include in
  coverage counts.
- Missing PR body: no claim; report missingness by agent.

## 10. Mechanized outputs

Every run writes append-only JSONL containing configuration, task, agent,
model, seed, factor cell, candidate hash, claim/judge decision, oracle result,
deployed score, canary result, and cost fields. Aggregate scripts consume only
those logs and the frozen AIDev tables. Raw field text is not republished; only
aggregate results and classifier validation counts enter this repository.

## 11. Freeze gates

All boxes must be satisfied in one commit before confirmatory execution:

- [ ] two-annotator claim coding reaches the 0.80 precision/recall thresholds;
- [ ] confirmatory AIDev IDs are locked without outcome inspection;
- [ ] S1 and S3 public tests, held-out oracles, and canaries are versioned;
- [ ] container mount and network proofs pass on the measurement host;
- [ ] two agent adapters pass the same protocol smoke test;
- [ ] model identifiers, prices, seeds, and the 960-cycle budget are filled in;
- [ ] cost ceiling is approved;
- [ ] this commit hash is recorded in the manuscript and run manifest.

Until then, all executions, including `results/se_smoke_matrix.json`, are
apparatus validation and not confirmatory evidence.

## 12. Amendment log

- R1 (2026-08-08): moved main controlled tasks to S1 defect repair and S3
  production operations; retained G1 and B1 as generalization/boundary arms.
- R2 (2026-08-08): added the AIDev field layer and separated its deterministic
  10,000-PR feasibility sample from the future confirmatory set.
- R3 (2026-08-08): excluded post-merge revert after the pilot showed it is not
  identifiable in the released enriched tables.
- R4 (2026-08-08): replaced the confounded gate-plus-information comparison
  with the primary 2x2 factorization.
- R5 (2026-08-08): mechanized token, time, dollar, and break-even outcomes for
  the original HA2 cost hypothesis.
