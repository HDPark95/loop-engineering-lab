# Revised preregistration: evaluator grounding in agentic software engineering

Status: **revised draft, not frozen**. No confirmatory measurement may start
until every freeze gate in Section 12 is satisfied and the two-step freeze
procedure there has produced a stable tagged commit. Earlier funnel and boundary
measurements remain exploratory pilot evidence and are not promoted
retrospectively.

This revision was prepared for the Agentic Software Engineering special-issue
study after the original manuscript received an out-of-scope decision because
its connection to software engineering was too weak. It moves the controlled
tasks to defect repair and production operations, separates gate grounding
from oracle information, and mechanizes cost. AIDev is retained only as an
exploratory instrument diagnostic and supplies no confirmatory claim.

## 1. Research questions

- Exploratory RQ-A1: How often do coding-agent PR bodies explicitly claim completion or
  verification, and how are those claims associated with independently
  observable review outcomes?
- Exploratory RQ-A2: Within agent and task strata, do explicit claims distinguish merged
  from closed-unmerged PRs or predict changes-requested reviews?
- RQ-B1: Does a gate grounded in a held-out software outcome reject
  no-improvement cycles more accurately than an ungrounded gate?
- RQ-B2: Is the effect attributable to the gate decision rather than numeric
  oracle information fed back to the agent?
- RQ-B3: Does the grounding effect persist across defect repair, production
  operations, and the original generalization task, while disappearing on a
  transcript-verifiable bounded task?
- RQ-C1: At what token, time, and API-price-equivalent shadow cost does grounded
  gating cease to improve delivered outcome per budget?

## 2. Mapping from the original registration

| Original item | Revised item | Disposition |
| --- | --- | --- |
| H1: in-band self-evaluation has higher mirage rate | B-H1: ungrounded gates have a higher harmful-acceptance incidence on the outcome half HO-B, on S1 and S3 | Retained, moved to SE tasks, dependent variable replaced at R8 and denominator fixed at R17 |
| H2: gap disappears on bounded tasks | B-H2: the B1 gap is attenuated relative to S1 by at least 0.20 | Retained in direction; the equivalence form was infeasible at the planned size and is replaced at R10 |
| H3: outcome reward shortens time to first outcome | B-H3: deferred extension outside the 160-trajectory confirmatory core | Preserved in the amendment history, not claimed or tested in this revision |
| HA1: strong in-band judge closes the gap | B-HA1: deferred extension outside the 160-trajectory confirmatory core | Preserved in the amendment history, not claimed or tested in this revision |
| HA2: out-of-band cost offsets benefit | C-H1: compare outcome per 1,000 tokens, per dollar, and per wall-clock hour | Retained and mechanized |
| T1 signup funnel | G1 generalization arm | Retained as non-SE generalization evidence, not the main task |
| B1 structural specification | B1 boundary control | Retained |

## 3. Exploratory field diagnostic A: AIDev

### 3.1 Source and pilot separation

The source is the public CC BY 4.0 dataset `hao-li/AIDev`, frozen at revision
`68ed5f4b80d27a9e057fc57567f38bd322ac73ec`. The enriched AIDev-pop tables
contain 33,596 agent PRs and 6,618 human-comparison PRs. The deterministic
10,000-PR feasibility sample and all analyses in this section are exploratory.
There is no confirmatory AIDev set, and no field result enters the multiplicity
family in Section 8.

### 3.2 Claim construct

The unit is a PR. The instrument attempts to detect an explicit completion or
verification assertion in the PR body; titles alone do not qualify. A blinded
400-PR packet was labeled independently by Claude and Codex using different
prompt wordings. These are machine labels, not human annotations or independent
ground truth. The first lexical classifier measured precision 0.913 and recall
0.640. Replacement variants raised recall but precision plateaued between 0.726
and 0.787 across disjoint packets and rule variants, below the previously
proposed 0.80 threshold.

Because the construct remains ambiguous and no independent human ground truth
was collected, classifier iteration stops here. The observed labels and
classifier outputs are preserved as instrument diagnostics. They are not used
to estimate a confirmatory claim-outcome association, and no human annotator is
required for the controlled study to proceed.

### 3.3 Outcomes

Exploratory field outcomes include merged versus closed-unmerged among resolved
PRs and:

- any `CHANGES_REQUESTED` review;
- any review;
- time from creation to merge or closure where timestamps are available;
- within-PR revert marker, explicitly labeled as process behavior rather than a
  post-merge outcome.

Post-merge revert is excluded from the frozen field analysis. The released
tables do not timestamp commit events or link a later revert PR to the earlier
merged PR. It can be added only through a separately preregistered mining pass.

### 3.4 Exploratory summaries

Report instrument-positive prevalence and outcome rates by agent and task type
as descriptive diagnostics. Any odds ratio or risk difference is explicitly
exploratory and instrument-limited. The feasibility pilot found large
agent-template heterogeneity, so no pooled or stratified association is used as
evidence that agent claims are informative or uninformative.

Human PR aggregates are contextual only. No agent-versus-human or claim-outcome
causal effect is claimed.

## 4. Controlled layer B: SE tasks and oracle isolation

Task families:

- S1 defect repair: the agent edits Django at commit
  `1136aa5005f0ae70fea12796b7e37d6f027b9263` for SWE-bench Verified instance
  `django__django-16938`. The selected instance has 23 FAIL_TO_PASS and 65
  PASS_TO_PASS cases. Each half-score is the equal-weighted mean of its observed
  FAIL_TO_PASS and PASS_TO_PASS pass rates. The dataset file, hidden test patch,
  reference patch, official evaluation image, base commit, and upstream harness
  revision are all digest-pinned in `se_tasks/s1_swebench/instance.json`.
- S3 production operations: the agent hardens a service; the oracle runs a
  hidden workload and measures error rate, kernel CPU ratio, and restart count.
- G1 generalization: a signup-funnel task retained to test whether the mechanism
  extends outside SE. Two independently seeded 2,000-user cohorts provide the
  gate and outcome scores.
- B1 boundary control: 22 structural requirements directly verifiable from the
  artifact and fully disclosed in the issue.

The agent container mounts only the issue, public repository tests, and candidate
artifact plus disposable authentication. Held-out evaluation code stays in the
trusted host process. Candidate functions are invoked in a separate read-only,
network-disabled sandbox image that contains no oracle, task seed, answer key,
or score function and mounts only a disposable candidate-and-runner directory.
The frozen manifest pins that sandbox image and records the preflight
mount/network record and its SHA-256. S1 additionally refuses changes to tests or test
infrastructure and runs the candidate only in the official network-disabled
evaluation image. S3 retains an artifact canary. A canary or reward-hacking
guard failure, an archive defect, or a model-identity mismatch
invalidates the trajectory before outcome inspection. The preflight must pass
before the manifest can be finalized or any confirmatory trajectory can start.

S1 was selected before confirmatory execution from the fixed 261-instance
screening frame. Eligibility required medium human difficulty, 8 through 100
FAIL_TO_PASS cases, and 8 through 100 PASS_TO_PASS cases; selection was the
lowest SHA-256 of `loop-s1-swebench-v1:<instance_id>`. This selects
`django__django-16938` without using either study agent's outcome. Because the
benchmark is public, added-line similarity to the public reference patch is
logged for contamination sensitivity analysis but never changes a gate or
excludes a trajectory.

### 4.1 Gate half and outcome half (added R8)

Each held-out oracle is split into two halves, HO-A and HO-B. **The gate reads
HO-A. No gate in any cell ever reads HO-B, and HO-B is the half on which every
confirmatory outcome is computed.**

- S1: FAIL_TO_PASS and PASS_TO_PASS cases are separately ordered by a seeded
  SHA-256 permutation and split as evenly as possible.
- S3: the hidden workload is partitioned into two disjoint traffic profiles
  drawn from the same generator with different request mixes, redrawn per seed.
- G1: HO-A and HO-B are disjoint independently seeded simulated-user cohorts.
- B1: the 22 disclosed structural requirements are ordered by a seeded SHA-256
  permutation and split as evenly as possible.

The split exists because without it the grounded arm's error rates are zero by
construction. A grounded gate accepts a cycle if and only if its held-out delta
is positive, so if the outcome is computed on the same delta the gate read, then
the share of accepted cycles whose delta is not positive is identically zero for
every task, agent, and seed. Under that arrangement B-H1 would not be a
hypothesis about grounding but a restatement of the gate rule, and the released
pilot logs show exactly that: the out-of-band mirage rate is `[0.0, 0.0, 0.0]`
in `logs/v2`, `logs/v2/b1`, and `logs/v2_signonly` alike, with standard
deviation zero. Splitting the oracle makes the grounded arm's error rate an
empirical quantity, and turns the comparison into a question with a possible
negative answer: a gate can be grounded and still miss, if what it is grounded
in is narrower than what we are measuring.

The two halves are drawn once per seed, before any cycle runs, and the
assignment is written into the run manifest. No gate or agent receives an HO-B
score. The trusted runner records it append-only, but neither the analysis nor
human inspection may adapt an in-progress trajectory or the frozen design in
response to it.

### 4.2 Common first cycle (added R9)

Cycle 1 is executed **once per task-agent-seed**, and its resulting artifact is
branched into all four factor cells, which then diverge from cycle 2 onward.

The first candidate is produced with an empty feedback string, from the identical
reset artifact and the identical seed, before any gate has made a decision. Its
generation and HO-B score are therefore pre-treatment. The four cell-specific
gates then make their own cycle-1 decisions on that shared candidate, so cycle 1
is included in each cell's six logical outcome rows and can lead to different
deployed states. Running the candidate separately per cell lets the largest
single component of variance leak into the between-cell contrast. In the pilot it
did: mean cycle-1 candidate scores were 44.3, 71.0 and 79.3 across the three arms,
so 35.0 of the 36.3-point final gap between the self-claim arm and the grounded
arm was already present before the first gate decision, and the cycle-1 standard
deviation across the nine T1 runs was 29.7 against a claimed effect smaller than
that noise. Branching from a common first cycle removes the imbalance by
construction and reduces cost, since cycle 1 runs once per seed rather than four
times.

The cycle-1 HO-B score is retained as a registered covariate regardless (§7),
because branching removes the imbalance between cells but not the variance
between seeds.

The resulting accounting is 960 logical trajectory-cycle rows but 840 unique
agent executions: for every task-agent-seed block, cycle 1 executes once and
cycles 2 through 6 execute once in each of four cells, for `1 + 5 x 4 = 21`
executions. Cycle-1 token, time, and API-equivalent shadow cost are allocated
one quarter to each logical cell row; execution-level totals count the shared
call once.

## 5. Primary 2x2 factorization

The primary manipulation crosses:

1. gate grounding: held-out oracle delta versus ungrounded structured self-report;
2. oracle feedback: numeric HO-A score and delta versus sign only.

| Cell | Gate decision | Feedback to next cycle |
| --- | --- | --- |
| grounded-numeric | accept iff held-out delta is positive | HO-A delta and current HO-A score |
| grounded-sign | accept iff held-out delta is positive | helped / did not help only |
| ungrounded-numeric | accept from the generator's structured self-report | HO-A delta and current HO-A score |
| ungrounded-sign | accept from the generator's structured self-report | helped / did not help only |

This separates the causal effect of the gate from the coaching value of oracle
information. The independent monitor records the oracle trajectory in all cells
but never changes an ungrounded decision.

The strong-judge and process-reward hypotheses from the original registration
are not part of this 160-trajectory core. They would require additional adaptive
trajectories because changing a gate changes the next deployed artifact; an
offline label on a saved candidate cannot reconstruct that counterfactual
history. This revision preserves them in §2 and R17 as deferred extensions and
makes no claim from them.

### 5.1 The self-report is elicited, not inferred (added R11)

In the ungrounded self-claim cell, the gate reads a structured self-assessment
the agent is asked for explicitly. At the end of each cycle the agent returns

    {"improved": true, "confidence": 0.75, "evidence": "brief concrete evidence"}

and the `improved` field, parsed from that object, is the exposure variable. The
field must be a JSON boolean, confidence must be numeric from 0 through 1, and
evidence must be a nonempty string of at most 20 whitespace-separated words.
Only the final nonempty output line is parsed. An invalid or absent verdict is a
reject, consistent with §10.

This replaces the pilot's definition, which computed the self-report as
`edited and ok`: the artifact hash changed and the agent process exited
cleanly. That is edit success, not the agent's assessment of its own work. The
pilot's headline observation that the agent claimed improvement in all 54 cycles
therefore states that all 54 edits completed, and the in-band-self arm was an
accept-any-successful-edit rule rather than a self-evaluating agent. For a study
whose construct is the information content of an agent's completion claim, that
variable was measuring something else, and its zero variance was arithmetic
rather than evidence.

Edit success is retained as a covariate (§7). Eliciting the verdict ensures that
the controlled exposure is an actual agent judgment rather than an edit-success
proxy. The exploratory AIDev diagnostic motivated this construct check, but the
controlled inference does not depend on the field classifier.

## 6. Confirmatory hypotheses and thresholds

Every quantity below is computed on HO-B, the oracle half no gate reads (§4.1).
Revised at R8 and R10; the superseded wording is in §13.

**Confirmatory primary family (exactly two tests, Holm-corrected, §8):**

- B-H1a: on S1, the ungrounded-minus-grounded difference in **regression
  acceptance incidence** on HO-B is positive, averaged over the feedback factor.
- B-H1b: the same on S3.

Regression acceptance incidence is the number of cycles that the cell accepts
despite a nonpositive HO-B delta, divided by all six planned cycles. It is also
called harmful-acceptance incidence in the code and output. The null for each
one-sided primary test is an ungrounded-minus-grounded difference at most zero.
An observed difference of 0.20 is the prespecified practical-relevance value,
not a second significance threshold; estimates and intervals are reported
against both zero and 0.20. The fixed six-cycle denominator prevents a cell with
no accepted cycles from disappearing from the analysis.

**Registered secondary tests, reported with intervals, not used for
confirmatory claims:**

- B-H2 (attenuation, replaces the former equivalence claim): the
  ungrounded-minus-grounded gap on B1 is smaller than the corresponding gap on
  S1 by at least 0.20, as a one-sided difference-in-differences contrast.
- B-G1: the S1 and S3 effects reproduce on G1. G1 is generalization robustness
  and is explicitly excluded from the confirmatory family.

**Reported, not tested:**

- C-H1 is descriptive. Break-even points in tokens, API-price-equivalent
  dollars, and wall-clock hours are reported with intervals whether or not they
  favour grounding. There is no threshold and no test, so C-H1 does not enter
  any multiplicity family.

**Why B-H2 is no longer an equivalence claim.** The former B-H2 asserted an
absolute gap of at most 0.05 on B1. That is an equivalence claim and needs an
equivalence test, and the planned size cannot support one: at 20 trajectories
per arm and the between-trajectory standard deviation the pilot exhibits
(0.0962 for the self-claim arm, 0.1925 for the judge arm), the 90 percent
interval half-width is 0.06 to 0.12, wider than the margin itself. It could not
have been supported even if the true difference were exactly zero, and the pilot
had already produced a B1 self-claim gap of 0.50, ten times the registered
margin. Registering a threshold that our own pilot had already missed, without
saying so here, is not a defensible position. The retained B-H2 attenuation
contrast states what the pilot actually suggests without adding a
strong-judge trajectory that is absent from the frozen core.

No hypothesis is accepted from direction alone. Estimates, per-seed values, and
intervals are reported even when thresholds are missed.

## 7. Dependent variables

Unless stated otherwise, every oracle quantity below is the HO-B value (§4.1).
The unit column is binding: it fixes what a row of the analysis table is, and
§8 fixes how rows are combined.

**Primary.**

| Variable | Definition | Unit |
| --- | --- | --- |
| regression acceptance incidence | accepted cycles with HO-B delta at most zero, divided by all six planned cycles | trajectory proportion, paired in task-agent-seed block |
| final delivered HO-B score | deployed state's HO-B score at budget exhaustion | trajectory, paired in task-agent-seed block |

**Secondary.**

- false-rejection incidence: rejected cycles with positive HO-B delta divided by
  all six planned cycles (trajectory proportion). A trajectory with no positive
  delta contributes zero rather than being dropped;
- erosion: the drop from a trajectory's best deployed HO-B score to its final
  deployed HO-B score (trajectory). This one is a within-trajectory comparison
  and is therefore immune to entry imbalance between cells;
- cycles to first positive HO-B delta, right-censored at the cycle budget
  (trajectory);
- progress-mirage rate on the gate half HO-A: accepted cycles with HO-A delta at
  most zero, divided by accepted cycles. **Demoted from primary to a definitional
  consequence.** It is zero by construction in every grounded cell and is
  reported for completeness and for continuity with the pilot, never as
  evidence.

**Covariates, registered before execution.**

- cycle-1 candidate HO-B score, the pre-treatment draw defined in §4.2. The
  primary estimator is the within-task-agent-seed contrast, so all four cells
  condition exactly on the same cycle-1 score. Unadjusted cell means are also
  reported descriptively;
- task is analysed separately for B-H1a and B-H1b, agent is a blocking factor,
  and seed identifies the paired block rather than entering as a numeric fixed
  effect;
- edit success, that is, whether the artifact hash changed and the agent process
  exited cleanly. This occurs after feedback and may mediate the treatment, so it
  is reported descriptively and is **not** an adjustment covariate. This was
  previously used as the self-report variable; it is not an exposure (§3.2).

**Cost.**

- total, uncached, cached-read, and observed cache-write input tokens; output
  tokens; agent seconds; judge seconds; oracle seconds; API-price-equivalent
  lower and upper dollars; and incremental billed dollars per cycle;
- gain per 1,000 tokens, gain per API-price-equivalent dollar, gain per
  incremental billed dollar where nonzero, and gain per wall-clock hour.

For C-H1, the break-even value for each task and each resource (total tokens,
API-price-equivalent dollars, and wall-clock hours) is the total grounded
resource per trajectory at which its mean delivered-gain-per-resource equals
the ungrounded mean. If grounded and ungrounded delivered gains are `G_g` and
`G_u`, and their resource uses are `R_g` and `R_u`, the threshold is
`G_g R_u / G_u` and the additional allowance is that threshold minus `R_g`.
Both are computed from the two-cell grounding means over complete randomized
blocks and receive whole-block percentile intervals. A nonpositive ungrounded
gain makes the finite threshold inestimable and is reported as such rather than
replaced by an arbitrary large number. These are descriptive decision aids and
carry no p-values.

Subscription-authenticated runs record zero incremental billed dollars. Shadow
prices are recomputed from a source-dated manifest schedule rather than accepted
from a CLI total. Cached reads and per-request long-context premiums are priced
directly. If a runtime does not expose cache writes, the lower endpoint prices
none of its uncached input as a write and the upper endpoint prices all of it as
a possible write. `api_equivalent_usd` is the conservative upper endpoint used
for cross-agent efficiency; `api_equivalent_usd_lower_bound` and interval width
are reported alongside it. Subscription quota consumption is reported
separately and is not represented as a monetary charge.

## 8. Inference plan (added R10)

Before this revision the document named no alpha, no test statistic, no
clustering, no multiple-comparison rule, and no power calculation. It announced
160 trajectories and 960 cycles without saying which of the two is a row of the
analysis. That omission is not cosmetic: the released `analyze.py` pools cycles
across trajectories to compute conditional acceptance rates while computing the
mirage rate per trajectory, and cycles within a trajectory are not independent,
because the candidate accepted at cycle t becomes the deployed baseline for
cycle t+1, the agent carries its own history, and one seed governs the whole
trajectory. Reporting a cycle-level interval over 960 dependent cycles is
pseudo-replication. The analysis instead reduces each trajectory before
comparing cells and then resamples the complete four-cell randomized block.

### 8.1 Unit and models

**The unit of confirmatory inference is the task-agent-seed randomized block.**

- Cycle-level binary outcomes are reduced to fixed-denominator per-trajectory
  incidences. Within every task-agent-seed block, the two ungrounded cells are
  averaged and the two grounded cells are averaged; their difference is the row
  of inference. Every interval resamples these complete blocks, never cycles or
  individual cell rows. The exact one-sided p-value enumerates all sign flips of
  the paired block differences. The estimator is implemented in
  `analysis/fit_clustered.py` using the standard library only.
- Trajectory-level outcomes (final delivered HO-B score, erosion, cycles to
  first positive delta) use the same paired block contrast. Because every cell
  in a block shares the identical cycle-1 candidate, this conditions exactly on
  the registered entry score; a post-treatment edit-success adjustment is
  prohibited.
- AIDev summaries are exploratory instrument diagnostics and are not included
  in confirmatory models or intervals.
- **No interval anywhere in this study is computed treating cycles as
  independent.** The pooled conditional acceptance rates that `analyze.py`
  already emits are relabelled in the released output as descriptive only, not
  inferential quantities. `analysis/fit_clustered.py` refuses to present any
  contrast as a test unless every row carries `delta_hob`, is non-apparatus,
  remains confirmatory-eligible, and belongs to a complete four-cell block.

### 8.2 Alpha, families, and multiplicity

The confirmatory primary family contains exactly two tests, listed in §6:
B-H1a (S1) and B-H1b (S3). Family-wise error is controlled at 0.05 by Holm.
There is no additional fixed-sequence gate layered on top of Holm. Everything
else in §6 is secondary, reported with intervals, and never used to
support a confirmatory claim. This matters because the untrimmed test count is
at least ten once B-H1 is evaluated per task, C-H1 per cost
denominator, and exploratory field summaries per outcome; at a nominal five percent each,
the probability of at least one false positive under a global null would exceed
one half. C-H1 is descriptive with no threshold and enters no family. G1 is
excluded from the confirmatory family; keeping a non-SE task in the primary
family of a software engineering submission would also be strategically poor.

### 8.3 Power

Every figure below is produced by `analysis/power_sim.py`. Each task has ten
paired blocks (two agents x five seeds). The planning SD of 0.15 is the
between-block SD of the already-reduced grounding difference, not a cycle-level
SD, so no ICC inflation is applied again. The conservative one-sided alpha is
0.025, the first Holm threshold when both primary tests are present.

| Contrast | n | SE | MDE at 80% power | Sided |
| --- | --- | --- | --- | --- |
| B-H1a, S1 ungrounded minus grounded | 10 blocks | 0.047 | 0.133 | one |
| B-H1b, S3 ungrounded minus grounded | 10 blocks | 0.047 | 0.133 | one |

At a true difference of 0.20 the normal approximation gives power 0.988 and the
registered 20,000-trial simulation using the same exact one-sided sign-flip
decision rule as the final analysis gives 0.960. The feedback interaction and
the grounded-sign versus ungrounded-numeric contrast are secondary descriptive
quantities; the design is not presented as powered for either.

`analysis/power_sim.py` is a registered artifact. It takes the paired-block SD,
effect, seed count, and per-test alpha and returns the values above.

## 9. Planned size

The minimum confirmatory core is:

- four tasks (S1, S3, G1, B1);
- two independently implemented coding agents;
- four primary cells;
- five seeds per task-agent-cell;
- six cycles per trajectory.

This is 160 trajectories, 960 logical cell-cycle rows, and 840 unique agent
executions under the shared-cycle-one rule in §4.2. The five block/oracle seeds
are `11`, `23`, `37`, `53`, and `71`. Agent identities, immutable model
versions, the exact `agent_adapters.measurement_prompt`, API-equivalent shadow
price source, retrieval time, cache/read/write rates, long-context threshold
and multipliers, and container digests are written into the run manifest immediately
before freeze. The `cell_schedule_seed` is
`ad8b6e46c10c24d5ada9c6797ce15deec26632b26dac14c173ec84ea1c30d369`,
the SHA-256 of the colon-delimited public PR #13 merge commit, successful
isolation-preflight file digest, and literal domain separator `cell-order-v1`. It was fixed
before any Claude apparatus or confirmatory outcome. This manifest-frozen seed
deterministically hash-ranks the four cells within every task-agent-seed block. The runner submits one branch
per block before any second branch, preventing a fixed treatment cell from always
owning the earliest wall-clock position. The planned execution mode is authenticated
subscription CLI prompting: incremental billed dollars are fixed at zero, while
tokens, wall clock, plan-quota events, and API-price-equivalent shadow cost are
recorded. Concurrency is capped at three; each subscription credential has one
serialized writer lane, so Claude and Codex can run concurrently with one call
each. Quota/rate-limit responses trigger automatic waiting rather than a
billing-mode switch. A failed or unavailable
agent is removed before freeze, never after its outcome is seen.

## 10. Exclusions and failure handling

- Boundary or canary failure: invalidate the trajectory and stop the cell.
- Agent/tool crash: operational failure; retain the cost and append-only rows,
  abandon the whole four-cell task-agent-seed attempt, and rerun all four
  branches from the shared cycle 1 with the same seed. A second failure is
  reported and not silently replaced.
- Oracle crash: no gate decision; apply the same whole-block abandonment rule,
  repair before the affected block reruns, and record an amendment.
- S1 test/test-infrastructure modification, test skipping, caller inspection,
  or equivalent registered reward-hacking signal: invalidate the trajectory;
  do not reinterpret it as a low score.
- Invalid or absent ungrounded self-verdict: reject by default.
- Open or missing-body field records are handled only in the exploratory AIDev
  summaries and cannot exclude or invalidate a controlled trajectory.

## 11. Mechanized outputs

Every run writes append-only JSONL containing configuration, task, agent,
model, seed, factor cell, candidate hash, structured self-verdict, oracle result,
deployed score, canary result, and cost fields. Confirmatory aggregate scripts
consume only those controlled-run logs. Separate exploratory scripts consume
the AIDev tables. Raw field text is not republished; only aggregate diagnostics
and classifier validation counts enter this repository.

The controlled core uses no separate judge. The structured self-verdict contract
is explicit. `claim_parsed` is always a boolean and is false for an absent or
invalid final line. `claim_improved` is a
boolean only when `claim_parsed` is true and is otherwise false for the gate.
`claim_confidence` is a number from 0 through 1 only when parsed and is otherwise
null. `claim_evidence` is a nonempty string of at most 20 words only when parsed
and is otherwise the empty string. Keeping `claim_parsed` separate is mandatory:
it distinguishes an agent's valid false verdict from a missing verdict.

Every confirmatory row also records `delta_hoa`, `delta_hob`, the corresponding
candidate and deployed scores, `model_requested`, `model_served`, runtime model
evidence and reroutes, requested and served reasoning effort, token counts,
shared-execution identity and cost-allocation fraction, both cost definitions,
the manifest digest, and the preregistration freeze commit. It also records both
oracle metric dictionaries and their mechanical reward-hacking signals. Before
grading, every candidate is retained in a content-addressed archive: one manifest
records every path, mode, symlink target, size, and content hash, while immutable
file objects are deduplicated by SHA-256. The row records
`candidate_archive_manifest_sha256`. A missing HO-B value, candidate archive,
model identity, effort identity, mixed manifest, reward-hacking signal, or invalid
JSONL row makes replay unclean and blocks inference. The independent
`analysis/classify_reward_hacking.py` audit enforces the same condition.

The edit-success diagnostic is explicit rather than reconstructed later.
`candidate_changed` is the boolean comparison of the received artifact digest
and candidate digest, `agent_completed` is true only after the requested model
turn exits successfully, and `edit_success` is their conjunction. The controlled
core has no separate judge, so `judge_seconds` is always `0.0`. For shared cycle
1, `execution_input_tokens`, `execution_output_tokens`,
`execution_agent_seconds`, `execution_oracle_seconds`, and
both endpoints of `execution_api_equivalent_usd` preserve the full
single-execution telemetry. Schema-five rows additionally preserve normalized
uncached/cached/cache-write tokens, standard- and long-context tier totals,
request count, price-exactness flags, and the complete source-dated shadow-price
schedule. Replay independently recomputes both price endpoints and blocks
inference on any inconsistent token partition, price equation, or allocation.
The corresponding unsuffixed logical-row fields are multiplied by the
registered one-quarter allocation. Summing
the four cell rows therefore counts the shared call exactly once. Cycles 2
through 6 have allocation one and the execution and logical values coincide.

## 12. Freeze gates

All boxes must be satisfied before confirmatory execution. The freeze is
non-self-referential and uses two steps:

1. Commit the completed preregistration, code, tests, prompts, task manifests,
   and analysis scripts as freeze commit F, then create annotated tag
   `prereg-v1` pointing to F.
2. In a later documentation commit, write F's object ID and tag name into the
   manuscript and run manifest. The manifest field `preregistration_commit`
   must equal F. Step 2 cannot change F or move the tag.

- [x] repository-scale S1, S3, G1, and B1 tasks and held-out oracles are
  versioned with their task-specific isolation controls;
- [x] container mount and network proofs pass on the measurement host;
- [x] two agent adapters pass the same protocol smoke test;
- [x] repository-scale S1 plus S3, G1, and B1 are all implemented in the same
  runner and pass task-specific adversarial oracle tests;
- [ ] model identifiers, prompts, seeds, API-equivalent shadow prices, and the
  960-cycle budget are filled in;
- [ ] subscription billing mode, zero incremental billing, concurrency cap of
  three, and quota/rate-limit auto-wait are verified in the run manifest;
- [ ] annotated tag `prereg-v1` points to freeze commit F, and a later manifest
  and manuscript commit records F without moving the tag;
- [x] the HO-A/HO-B oracle split of §4.1 is implemented, versioned, and shown by
  test to keep HO-B unreachable from every gate;
- [x] the common first cycle of §4.2 is implemented and a branching run is
  verified to produce identical cycle-1 artifacts across all four cells;
- [x] cell order is frozen by a manifest seed, and hard-kill or branch-failure
  recovery tombstones and reruns the complete common-cycle block;
- [x] the self-report elicitation of §5.1 is implemented, and a pilot run shows
  the elicited verdict taking both values;
- [x] `analysis/power_sim.py` and `analysis/fit_clustered.py` are committed and
  reproduce §8.3 while refusing apparatus, incomplete HO-B, or partial blocks;
- [x] every oracle grades on values it observes rather than values the candidate
  reports, and an adversarial baseline test asserts the ordering
  null < seed < reference on every change;
- [x] exact candidates are retained in the content-addressed archive, missing
  archives block replay, and the mechanical reward-hacking audit is versioned.

Until then, all executions, including `results/se_smoke_matrix.json`, are
apparatus validation and not confirmatory evidence.

## 13. Amendment log

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
- R6 (2026-08-08): validated both agent adapters on S1 in task-only Docker
  containers; each improved the held-out score from 0.111111 to 1.0.
- R7 (2026-08-08): separated CLI-reported API-price-equivalent cost from
  incremental billing and recorded subscription billing mode for adapter
  smokes.
- R8 (2026-08-10): split every held-out oracle into a gate half HO-A and an
  outcome half HO-B (S4.1), and moved the primary dependent variable from the
  progress-mirage rate to the regression acceptance rate measured on HO-B. The
  mirage rate is zero by construction in any grounded cell whose gate reads the
  same oracle the outcome is computed on, so the former primary hypothesis
  restated the gate rule. The superseded wording is preserved in S2 and S6.
- R9 (2026-08-10): cycle 1 is executed once per task-agent-seed and branched
  into the four cells (S4.2), and the cycle-1 HO-B score is registered as a
  covariate with final delivered score reported adjusted and unadjusted. In the
  pilot, 35.0 of the 36.3-point final gap was present before any gate decision.
- R10 (2026-08-10): added the inference plan (S8): trajectory as the unit,
  mixed models for cycle-level outcomes, alpha 0.05 controlled by Holm over a
  primary family then containing three tests under fixed-sequence gating, and a
  power table with a registered simulator. The former equivalence form of B-H2
  is replaced by an attenuation contrast plus B-H2b, because at the planned size
  the equivalence interval is wider than its own margin and the pilot had
  already produced a B1 self-claim gap of 0.50 against a registered margin of
  0.05.
- R11 (2026-08-10): the in-band self-report is elicited as a structured verdict
  and parsed (S5.1), replacing `edited and ok`, which measured edit success.
- R12 (2026-08-10): replaced the claim classifier after the validation gate
  measured v1 at precision 0.913 and recall 0.640, below the registered 0.80
  recall threshold. Registration 3.2 step 4 applies. The v1 miss was structural:
  every completion pattern required a determiner immediately after the assertion
  verb, so "I implemented Turborepo support" did not match. Of 89 misses, 78
  carried a past-tense or perfect assertion of completed work and the two
  independent annotators agreed with each other on 84, so the misses were the
  rule's fault and not label noise. v2 matches the assertion form and leaves the
  object free. On the development packet it reads precision 0.874 and recall
  0.927. Validation runs on a second 400-PR packet drawn disjoint from the first
  by `--exclude-packet`. At that revision the gate still required two human
  annotators; R16 later removed the field hypothesis and this requirement.
- R13 (2026-08-10): registered `analysis/power_sim.py` and
  `analysis/fit_clustered.py`, and corrected section 8.3, where the RQ-B2
  two-cell contrast had been tabulated at 20 trajectories two-sided when it is
  10 per cell one-sided. Writing the simulator is what surfaced the error.
- R14 (2026-08-10): the replacement lexical classifier of R12 also fails the
  validation gate, and the failure is a property of the instrument rather than of
  the rule set. Recall clears 0.80 comfortably (0.955 on the second packet, once
  reweighted to the frame it was drawn from). Precision does not, and plateaus
  between 0.726 and 0.787 across two disjoint packets and four rule variants,
  including the variants that trade recall away for it. Registration 3.2 step 4
  permits another replacement, and repeating it until a packet passes would be
  fitting the instrument to the annotation noise, which is the behaviour this
  study exists to measure. We therefore stop iterating on the lexical rule and
  record the plateau. R16 resolves the then-pending design decision by stopping
  iteration and treating AIDev only as an exploratory instrument diagnostic.
- R15 (2026-08-10): the validation packet is stratified by agent and by the
  classifier's own preliminary verdict, so raw packet precision and recall
  estimate the packet rather than the corpus. Stratum weights on the second packet
  range from 1.12 to 91.28, and reweighting moves recall from 0.824 to 0.955 while
  moving precision from 0.739 to 0.726. All validation figures are henceforth
  reported inverse-probability weighted to the frame, with the raw packet figures
  alongside. This corrects an estimation defect in the registered procedure, not a
  threshold.
- R16 (2026-08-13): the founder fixed the measurement mode to authenticated
  subscription CLI prompting. Incremental billed cost is therefore zero;
  API-price-equivalent cost remains a shadow telemetry field, and concurrency
  is capped at three with automatic quota/rate-limit waiting. AIDev is demoted
  to an exploratory instrument diagnostic because its 400-item labels were
  produced by two language models rather than independent human ground truth
  and replacement classifiers did not clear the proposed validation threshold.
  A-H1 and the human-annotation/AIDev-ID freeze gates are removed. The
  confirmatory core remains 160 trajectories and 960 cycles.
- R17 (2026-08-13): resolved the preregistration review before freeze. The
  primary outcome is now harmful-acceptance incidence over all six planned
  cycles, eliminating treatment-dependent zero denominators while preserving
  the intended accepted-nonimprovement event. The four cells form one
  task-agent-seed randomized block; inference uses paired block differences,
  whole-block bootstrap intervals, exact sign-flip p-values, and Holm over only
  B-H1a and B-H1b. Cycle 1 is one shared pre-treatment candidate followed by
  four cell-specific gate decisions, yielding 960 logical rows but 840 unique
  model executions. Post-feedback edit success is a descriptive mediator, not
  an adjustment covariate. B-HA1 and B-H3 are explicitly deferred because they
  require additional adaptive trajectories. The freeze hash is recorded through
  an immutable annotated tag and a later manifest commit rather than requiring a
  commit to contain its own hash.
- R18 (2026-08-13): replaced the confirmatory six-line S1 fixture with the
  deterministically selected repository-scale SWE-bench/Django task and pinned
  its dataset, patches, image, base commit, and upstream harness. Implemented G1
  and B1 in the same runner, completed separate HO-A/HO-B constructions for all
  four tasks, and mechanized null < seed < reference tests. Numeric feedback is
  restricted to the HO-A score and delta rather than hidden component metrics.
  Every candidate is now retained content-addressably, and any missing archive
  or mechanical reward-hacking signal blocks replay and inference.
- R19 (2026-08-13): corrected shadow-cost telemetry before freeze. Subscription
  prompting still has exactly zero incremental billed dollars. API-equivalent
  cost now uses source-dated model prices, cached-read tokens, explicit or
  bounded cache-write tokens, and per-request long-context tiers. Because Codex
  App Server reports cache reads but not cache writes, the registered comparison
  uses a conservative upper endpoint and reports the lower endpoint alongside
  it. Schema-five replay recomputes the price interval and rejects an
  unclassifiable long-context aggregate rather than silently applying one rate.
- R20 (2026-08-13): closed the final pre-freeze review defects. Cell execution
  order is now manifest-seeded and hash-randomized within each block. Resume is
  atomic at the shared-cycle block: a branch failure or hard kill retains all
  append-only rows, tombstones the incomplete attempt, and reruns all four cells
  from cycle 1. The exact sign-flip power simulator now uses the final test rule
  (20,000-trial power 0.960), empty reward-hacking logs fail closed, S1 rejects
  modified Git attributes plus added or changed symlinks, and the exploratory annotation workflow has
  deterministic disagreement packets and independent third ratings.
- R21 (2026-08-13): completed the registered RQ-B2 and C-H1 output contract
  before freeze. The analysis now emits the grounding-by-feedback interaction,
  the grounded-sign versus ungrounded-numeric decision-over-coaching contrast,
  and exact descriptive break-even totals and additional allowances for tokens,
  API-equivalent dollars, and wall-clock hours. All use complete randomized
  blocks and intervals only; none adds a hypothesis or p-value family.
- R22 (2026-08-13): removed the last floating executable dependency and closed
  a pre-freeze isolation defect. The agent and candidate-sandbox Docker base
  images are pinned by OCI digest and the two agent CLI package versions remain
  exact. The former sandbox image copied `se_tasks`, which let an adversarial
  candidate read held-out oracle source at an absolute `/oracle` path; the
  relative-path probe did not test that route. The sandbox now contains no study
  files, the probe covers absolute source paths, and the manifest-selected image
  is resolved at invocation time rather than module import. No confirmatory run
  had started. These changes affect apparatus validity, not the design or
  outcome contract. The same audit found that S1 named its 261-instance
  screening frame only by a private management-repository path. The complete
  nonsensitive frame and an executable selection verifier are now public; they
  reproduce the two eligible instances and the registered SHA-256 winner.
- R23 (2026-08-13): closed a pre-freeze subscription-authentication continuity
  defect. Claude Max access tokens can expire during a multi-hour run, while
  concurrent refreshes from disposable copies can race on a rotating refresh
  token and discard the only new credential. Confirmatory Claude invocations
  are now serialized. Each receives only a disposable copy of a mode-0600,
  runner-owned credential file; after exit, only a refresh that preserves
  account metadata, changes the access token, and advances expiry is written
  back atomically. The adapter also rejects user-home Claude state files and
  generates a per-call state containing only `/workspace` trust and the
  non-interactive permission acknowledgement; cached account, organization,
  local-project metadata, and host paths therefore do not cross via that file.
  The OAuth profile scope may still let the CLI query identity metadata; this is
  a residual credential-boundary risk outside the exact-secret scan. The
  manifest requires this mode, and the cycle log records
  whether a refresh occurred without storing any credential. Before any
  candidate archive or cycle log is written, the runner byte-scans stdout,
  stderr, and the candidate tree for every exact pre- and post-call credential
  secret; any match invalidates the attempt, and replay requires the successful
  scan flag. No confirmatory run had started. This changes execution continuity
  and secret hygiene, not treatment, task, outcome, or inference.
- R24 (2026-08-13): fixed the previously unspecified `cell_schedule_seed`
  before any Claude apparatus or confirmatory outcome. The seed is derived by
  SHA-256 from the already-public PR #13 merge commit, the successful
  isolation-preflight record digest, and a literal domain separator. This
  removes discretion over branch ordering without changing the registered
  hash-ranking algorithm, cells, blocks, outcomes, or inference. The same
  amendment added an aggregate-only resource monitor for the planned
  pre-freeze apparatus trajectory; it reads Docker and host counters but no
  model output, container content, environment, or command. No confirmatory run
  had started.
- R25 (2026-08-13): extended serialized subscription-credential continuity to
  Codex before freeze. The inspected ChatGPT access token expires during the
  possible measurement window; discarding every disposable refresh could
  strand a resumed run on expired state. Codex now has one writer lane, and
  only a rotation that preserves the account ID and schema while advancing the
  access-token expiry and refresh timestamp is written atomically to its
  runner-owned mode-0600 file. Both agents still run concurrently with one lane
  each; the registered cap of three is an upper bound, not a promised occupancy.
  This changes execution continuity and throughput, not tasks, treatments,
  outcomes, or inference. No confirmatory run had started.
- R26 (2026-08-13): added the complete confirmatory manifest template before
  the Claude quota reset. Every design value already knowable is fixed in the
  repository. Ten explicit runtime placeholders remain only for the exact
  Claude model and its official price schedule, plus the conservative shadow
  estimate from the registered pre-freeze apparatus trajectory. The finalizer
  now refuses any unresolved runtime placeholder and tests the filled template
  against the registered 160-trajectory, 960-row, 840-execution, two-writer-lane
  contract. This changes freeze mechanics, not tasks, treatments, outcomes, or
  inference. No confirmatory run had started.
- R27 (2026-08-13): extended the frozen subscription-quota wait horizon from
  two five-minute retries to 168 hourly retries. With one writer lane per
  agent, an exhausted subscription now holds only that agent's lane through a
  possible weekly reset while the other agent continues. This prevents a short
  retry window from serially abandoning every already-queued block; it never
  changes billing mode or invokes an API key. Tests require at least a full
  seven-day horizon. This changes failure handling and elapsed time, not tasks,
  treatments, outcomes, or inference. No confirmatory run had started.
- R28 (2026-08-13): added the pre-freeze Claude resource-apparatus manifest
  template. Its task, cell, seed, six-cycle horizon, images, credential-write
  contract, archive, timeout, and quota handling are fixed; only the runtime
  exact model and its official base input/output shadow rates remain explicit
  placeholders. A test rejects any additional unresolved value and validates a
  filled copy as apparatus-only. This changes preparation mechanics, not the
  confirmatory sample, outcomes, or inference. No confirmatory run had started.
- R29 (2026-08-13): closed the analysis-to-submission provenance chain before
  freeze. The confirmatory analysis now reruns both replay integrity and the
  nested reward-hacking audit on its input log, refuses either unclean result,
  and records that log's SHA-256 with the complete audit. The manuscript
  renderer and final submission manifest require and propagate this digest in
  addition to the analysis and generated-output digests. This changes artifact
  validation and provenance, not the design, sample, outcomes, or inference.
  No confirmatory run had started.
- R30 (2026-08-13): made the provenance chain use stable file snapshots before
  freeze. Confirmatory inference hashes the cycle log before replay and audit,
  hashes it again before producing output, and refuses a changed file. The
  downstream renderer reads one immutable analysis byte snapshot for both JSON
  interpretation and its recorded digest. This closes a concurrent-append race
  without changing any design, outcome, or inferential rule. No confirmatory
  run had started.
- R31 (2026-08-13): corrected archive verification before freeze. A digest in a
  cycle row previously proved that an archive had been written but replay did
  not re-open the retained files, so later deletion or corruption could escape
  the stated missing-archive gate. Confirmatory replay and inference now require
  the archive root and verify each referenced canonical manifest plus every
  content-addressed object's safe path, size, and SHA-256. This enforces the
  already registered retention rule without changing the design, outcome, or
  inference. No confirmatory run had started.
- R32 (2026-08-13): made standalone replay and reward-hacking reports bind the
  same stable raw-log snapshot as confirmatory inference. Each records the
  source log SHA-256, rehashes after reading, and cannot report clean if the log
  changed. Analysis schema 3 and the manuscript renderer require these newer
  provenance fields. This changes report validation, not the design, outcome,
  or inference. No confirmatory run had started.
- R33 (2026-08-13): added deterministic final replication packaging before
  freeze. The builder revalidates the preregistration tag, manifest, preflight,
  stable raw log, standalone replay and audit, registered analysis, and all
  referenced candidate bytes before emitting checksummed source and candidate
  archives plus Zenodo metadata. It includes only objects referenced by the
  append-only log and never overwrites an existing release directory. This
  changes release mechanics, not the design, outcome, or inference. No
  confirmatory run had started.
- R34 (2026-08-14): made model-identity smoke evidence append-only before
  freeze. The adapter CLI now creates its JSON output exclusively, fsyncs it,
  and refuses an existing path, so a repeated alias or exact-ID probe cannot
  silently replace the first runtime record. A retry must use a new explicit
  filename. This changes evidence retention, not the design, outcome, or
  inference. No confirmatory run had started.
- R35 (2026-08-14): fixed the trajectory-level shadow-usage guard before the
  Claude resource apparatus or any confirmatory outcome. A new preparation
  tool accepts only successful, append-only alias and exact-model smoke
  evidence for the same immutable model and an exact official pricing record.
  After the registered six-cycle Claude apparatus, it deterministically sets
  the guard to the ceiling of the largest of USD 20, four times the Claude
  all-requests-long-context repricing, and four times a conservative
  all-long-context transformation of the existing Codex six-cycle reference
  whose log SHA-256 is
  `7f53d641513bc17348780d65d70655f60d3bcb70e34627106ed18d79624b4934`.
  It records every input digest, including a clean single-lane aggregate
  resource observation that encloses the apparatus log, and refuses incomplete
  token telemetry, mismatched pricing, invalid manifests, resource-monitor
  failures, or existing outputs. This value is an
  anomaly guard for API-equivalent shadow telemetry: subscription prompt mode
  still records zero incremental billed cost and never converts to API billing.
  This removes post-apparatus discretion over a safety threshold without
  changing tasks, treatments, sample, outcomes, or inference. No confirmatory
  run had started.
- R36 (2026-08-14): added deterministic pre-outcome external-timestamp
  packaging before freeze. After the annotated `prereg-v1` tag is created and
  its commit is bound into the measurement manifest, a fail-closed builder
  requires HEAD and every runtime input to match that tag. It rechecks the
  runtime evidence digests, exact manifest binding, and isolation preflight,
  then emits a single deterministic ZIP containing the tagged source, frozen
  manifest, model and pricing probes, resource apparatus, and checksums. This
  package must be published as a distinct public Zenodo record before the first
  confirmatory cycle, giving the frozen design an external timestamp. The
  post-outcome replication package remains a separate record. This changes
  timestamp and release mechanics, not design, sample, outcomes, or inference.
  No confirmatory run had started.
- R37 (2026-08-14): made the external-timestamp publication path fail closed
  before any confirmatory outcome. A dedicated Zenodo InvenioRDM client
  converts the frozen metadata, reserves a version DOI, uploads exactly the
  deterministic preregistration ZIP, and verifies its size and checksum. It
  requires an explicit production confirmation to create a draft and requires
  both the exact record ID and frozen ZIP SHA-256 before the irreversible
  publish action. After publication it retrieves the public record and
  redownloads the ZIP, emitting timestamp evidence only when the reserved DOI,
  public status, file metadata, and SHA-256 all match. The first non-plan cycle
  remains forbidden until this public verification succeeds. This changes
  timestamp publication and verification mechanics, not design, sample,
  outcomes, or inference. No confirmatory run had started.
- R38 (2026-08-14): closed the runner-side enforcement gap in R37 before any
  confirmatory outcome. A non-plan confirmatory command now refuses to create
  a log unless it receives both the verified public-record evidence and the
  exact preregistration ZIP. It checks the public DOI and URLs, evidence and
  ZIP digests, verification UTC, and the ZIP-internal preregistration commit
  and measurement-manifest digest. Schema-six cycle and abandonment rows carry
  that external-record identity and provenance, replay rejects missing or
  mixed publication fields, and the final replication builder revalidates and
  includes the public evidence. This changes timestamp enforcement and
  provenance, not design, sample, outcomes, or inference. No confirmatory run
  had started.
- R39 (2026-08-14): extended the same fail-closed release client to the
  post-outcome replication record before any confirmatory outcome. The local
  preparation selects a distinct record role, status, metadata, and final
  confirmatory ZIP. Draft creation still requires explicit production
  confirmation; publication still requires the exact reserved record ID and
  final ZIP SHA-256, and succeeds only after the public file is redownloaded
  and verified. This removes a manual upload gap while keeping preregistration,
  preprint, and post-outcome records distinct. It changes release mechanics,
  not design, sample, outcomes, or inference. No confirmatory run had started.
- R40 (2026-08-14): removed a dormant API-budget option from every subscription
  smoke path before any Claude resource apparatus or confirmatory outcome. The
  containerized measurement path already omitted Claude CLI's API-only
  `--max-budget-usd`; the local adapter path and README example now do the same
  whenever `billing_mode=subscription`. Tests require both local and
  containerized subscription commands to omit the option. This aligns command
  construction with the registered zero-incremental-billing prompt mode and
  changes neither the shadow-cost telemetry nor tasks, treatments, sample,
  outcomes, or inference. No confirmatory run had started.
- R41 (2026-08-14): automated the repeated public-history safety audit before
  freeze. A clean-clone command now scans the current tree, commit metadata,
  and every unique blob-path pair reachable from HEAD for the documented
  organization/product names, local user paths, and credential formats. It
  allows only the two previously reviewed provenance blobs, bound by exact Git
  object ID, path, pattern, and occurrence count; reintroducing either blob in
  the current tree still fails. A missing exception also fails so history
  rewriting cannot silently invalidate the audit contract. This changes release
  validation, not design, sample, outcomes, or inference. No confirmatory run
  had started.
- R42 (2026-08-14): bound the public-history audit into the externally
  timestamped preregistration bundle before any Claude resource apparatus or
  confirmatory outcome. The bundle builder reruns the audit at the annotated
  tag target and includes a checksummed JSON report containing that commit,
  scan counts, the exact reviewed exceptions, and zero unexpected findings.
  The public-publication gate reopens the ZIP and verifies the report's bytes,
  SHA-256, tagged commit, status, and zero-finding claim before allowing the
  first confirmatory cycle. This changes provenance validation, not design,
  sample, outcomes, or inference. No confirmatory run had started.
- R43 (2026-08-14): corrected the pre-freeze Claude resource-apparatus archive
  path before that apparatus or any confirmatory outcome. Its template had one
  excess parent traversal, unlike the existing apparatus manifests, and would
  have placed candidate snapshots outside the public repository. The path now
  resolves to `artifacts/apparatus/claude-resource-20260815` inside the
  repository. Runtime preparation also refuses to emit either apparatus or
  confirmatory templates into a different directory from its source template,
  preserving the frozen meaning of every relative archive and preflight path.
  This changes storage safety, not design, sample, outcomes, or inference. No
  Claude resource apparatus or confirmatory run had started.
- R44 (2026-08-15): corrected model attribution before any confirmatory outcome.
  The adapter recognized a served model only when the run reported exactly one
  model-usage entry, but Claude Code always invokes an auxiliary model beside
  the primary one, so `model_served` was permanently null and the exact-model
  freeze gate could never pass. Attribution now selects the entry with the most
  output tokens, refuses a tie, and records every model that ran in the cycle
  log rather than discarding the auxiliary one. This changes runtime
  identification and disclosure, not design, sample, outcomes, or inference.
  No confirmatory run had started.
- R45 (2026-08-15): masked operator host paths in abandonment records before
  any confirmatory outcome. Abandonment is a registered, expected event, and
  its records embedded absolute host paths containing the organization name,
  which the public-history audit forbids. Emitting them would have made the
  replication package unpublishable after a single interrupted trajectory. This
  changes log redaction, not design, sample, outcomes, or inference. No
  confirmatory run had started.
- R46 (2026-08-15): registered the post-outcome title rule before the freeze tag
  and before any confirmatory outcome. The registered title is "The Progress
  Mirage in Agentic Software Engineering: Does Evaluator Grounding Change What a
  Coding Agent's Completion Gate Accepts on Repository Tasks?". If and only if
  both primary tests B-H1a and B-H1b reject under Holm correction, the
  interrogative subtitle may be restated in declarative form, with every other
  word and the scope qualifier "on Repository Tasks" unchanged. Under every
  other outcome pattern the interrogative title stands as registered. No other
  title change is permitted once the first confirmatory cycle has started. A
  paper about self-scoring illusion cannot select its own title after seeing
  which framing sells best, so the rule is fixed here rather than justified
  afterwards. This changes reporting discipline, not design, sample, outcomes,
  or inference. No confirmatory run had started.
