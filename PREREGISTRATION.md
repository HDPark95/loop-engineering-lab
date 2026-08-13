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

- S1 defect repair: the agent edits a real program against public tests; the
  oracle runs held-out regression tests inaccessible to the agent.
- S3 production operations: the agent hardens a service; the oracle runs a
  hidden workload and measures error rate, kernel CPU ratio, and restart count.
- G1 generalization: the original signup-funnel task, retained to test whether
  the mechanism extends outside SE.
- B1 boundary control: a structural specification directly verifiable from the
  artifact.

The agent container mounts only the issue, public tests, and candidate artifact.
The oracle container contains held-out evaluation code, mounts the artifact
read-only, and has no network. Every trajectory begins with a mount/network
boundary proof, and every candidate is scanned for the task canary. A leak
invalidates the whole trajectory before outcome inspection.

### 4.1 Gate half and outcome half (added R8)

Each held-out oracle is split into two halves, HO-A and HO-B. **The gate reads
HO-A. No gate in any cell ever reads HO-B, and HO-B is the half on which every
confirmatory outcome is computed.**

- S1: the held-out regression suite is partitioned by stratified random
  assignment over test class, redrawn per seed.
- S3: the hidden workload is partitioned into two disjoint traffic profiles
  drawn from the same generator with different request mixes, redrawn per seed.
- G1 and B1: same construction, applied to the world-state features and to the
  structural requirements respectively.

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
- B-H2b (mechanism): task verifiability improves a separate in-band verifier but
  does not improve the generator's own self-report. In the pilot the bounded
  task closed the gap for the strong-judge gate (0.00) and did not close it for
  the self-claim gate (0.50); this hypothesis registers that asymmetry as a
  prediction rather than treating the second value as noise.
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
saying so here, is not a defensible position; the attenuation contrast and
B-H2b state what the pilot actually suggests.

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

- input tokens, output tokens, agent seconds, judge seconds, oracle seconds,
  CLI-reported API-price-equivalent dollars, and incremental billed dollars per
  cycle;
- gain per 1,000 tokens, gain per API-price-equivalent dollar, gain per
  incremental billed dollar where nonzero, and gain per wall-clock hour.

Subscription-authenticated runs record zero incremental billed dollars and
still record an API-price-equivalent estimate when the CLI provides one. The
latter is the cross-agent comparison metric; subscription quota consumption is
reported separately and is not represented as a monetary charge.

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
registered 20,000-trial simulation gives 0.982. The feedback interaction and
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
executions under the shared-cycle-one rule in §4.2. Agent identities, immutable model
versions, prompts, seeds, and API-equivalent shadow prices are written into this
document immediately before freeze. The planned execution mode is authenticated
subscription CLI prompting: incremental billed dollars are fixed at zero, while
tokens, wall clock, plan-quota events, and API-price-equivalent shadow cost are
recorded. Concurrency is capped at three and quota/rate-limit responses trigger
automatic waiting rather than a billing-mode switch. A failed or unavailable
agent is removed before freeze, never after its outcome is seen.

## 10. Exclusions and failure handling

- Boundary or canary failure: invalidate the trajectory and stop the cell.
- Agent/tool crash before a candidate exists: operational failure; retain the
  cost and rerun once with the same seed. A second failure is reported and not
  silently replaced.
- Oracle crash: no gate decision; repair before any affected cell resumes and
  record an amendment.
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
the manifest digest, and the preregistration freeze commit. A missing HO-B value,
model mismatch, effort mismatch, mixed manifest, or corrupt JSONL line makes the
replay report unclean and blocks inference.

## 12. Freeze gates

All boxes must be satisfied before confirmatory execution. The freeze is
non-self-referential and uses two steps:

1. Commit the completed preregistration, code, tests, prompts, task manifests,
   and analysis scripts as freeze commit F, then create annotated tag
   `prereg-v1` pointing to F.
2. In a later documentation commit, write F's object ID and tag name into the
   manuscript and run manifest. The manifest field `preregistration_commit`
   must equal F. Step 2 cannot change F or move the tag.

- [x] S1 and S3 public tests, held-out oracles, and canaries are versioned;
- [x] container mount and network proofs pass on the measurement host;
- [x] two agent adapters pass the same protocol smoke test;
- [ ] repository-scale S1 plus S3, G1, and B1 are all implemented in the same
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
- [x] the self-report elicitation of §5.1 is implemented, and a pilot run shows
  the elicited verdict taking both values;
- [x] `analysis/power_sim.py` and `analysis/fit_clustered.py` are committed and
  reproduce §8.3 while refusing apparatus, incomplete HO-B, or partial blocks;
- [ ] every oracle grades on values it observes rather than values the candidate
  reports, and an adversarial baseline test asserts the ordering
  null < seed < reference on every change.

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
