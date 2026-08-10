# Revised preregistration: evaluator grounding in agentic software engineering

Status: **revised draft, not frozen**. No confirmatory measurement may start
until every freeze gate in Section 12 is satisfied and the resulting commit hash
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
| H1: in-band self-evaluation has higher mirage rate | B-H1: ungrounded gates have a higher regression acceptance rate on the outcome half HO-B, on S1 and S3 | Retained, moved to SE tasks, dependent variable replaced at R8 |
| H2: gap disappears on bounded tasks | B-H2: the B1 gap is attenuated relative to S1 by at least 0.20 | Retained in direction; the equivalence form was infeasible at the planned size and is replaced at R10 |
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
assignment is written into the run manifest. Neither the analyst nor any gate
sees HO-B scores until the trajectory is complete.

### 4.2 Common first cycle (added R9)

Cycle 1 is executed **once per task-agent-seed**, and its resulting artifact is
branched into all four factor cells, which then diverge from cycle 2 onward.

The first candidate is produced with an empty feedback string, from the identical
reset artifact and the identical seed, before any gate has made a decision. It is
therefore a pre-treatment draw. Running it separately per cell lets the largest
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

### 5.1 The self-report is elicited, not inferred (added R11)

In the ungrounded self-claim cell, the gate reads a structured self-assessment
the agent is asked for explicitly. At the end of each cycle the agent returns

    {"improved": true | false, "confidence": 0.0-1.0, "evidence": "..."}

and the `improved` field, parsed from that object, is the exposure variable. An
unparseable or absent verdict is a reject, consistent with §10.

This replaces the pilot's definition, which computed the self-report as
`edited and ok`: the artifact hash changed and the agent process exited
cleanly. That is edit success, not the agent's assessment of its own work. The
pilot's headline observation that the agent claimed improvement in all 54 cycles
therefore states that all 54 edits completed, and the in-band-self arm was an
accept-any-successful-edit rule rather than a self-evaluating agent. For a study
whose construct is the information content of an agent's completion claim, that
variable was measuring something else, and its zero variance was arithmetic
rather than evidence.

Edit success is retained as a covariate (§7). Eliciting the verdict also aligns
the two layers: the field layer's exposure is an explicit completion assertion
in a PR body, so the controlled layer must measure an explicit assertion too if
the two are to be the same construct. If the elicited verdict turns out to vary,
B-H1 becomes a stronger result than the pilot's; if it does not vary, the claim
that self-report is uninformative is finally being made about a variable that
had the opportunity to be informative.

## 6. Confirmatory hypotheses and thresholds

Every quantity below is computed on HO-B, the oracle half no gate reads (§4.1).
Revised at R8 and R10; the superseded wording is in §13.

**Confirmatory primary family (exactly three tests, Holm-corrected, §8):**

- B-H1a: on S1, the ungrounded-minus-grounded difference in **regression
  acceptance rate** on HO-B is at least 0.20, averaged over the feedback factor.
- B-H1b: the same on S3.
- A-H1: in the confirmatory AIDev set, the agent- and task-stratified common
  odds ratio between an explicit completion claim and a merge differs from 1.

Regression acceptance rate is the share of accepted cycles whose HO-B delta is
at most zero. It replaces the mirage rate as the primary dependent variable
because the mirage rate is zero by construction in the grounded arm when gate
and outcome read the same oracle, and remains so under any oracle split that a
gate is allowed to observe.

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
- B-HA1: if the strong-judge regression acceptance rate on HO-B differs from the
  corresponding grounded rate by at most 0.05 on both S1 and S3, the
  judge-quality hypothesis is supported and this paper's mechanism must be
  narrowed.
- B-H3: outcome reward reduces median cycles to first positive HO-B delta
  relative to process reward.
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
| regression acceptance rate | accepted cycles with HO-B delta at most zero, divided by accepted cycles | cycle, clustered in trajectory |
| final delivered HO-B score | deployed state's HO-B score at budget exhaustion, adjusted for the cycle-1 covariate | trajectory |

**Secondary.**

- false-rejection rate: rejected cycles with positive HO-B delta divided by
  positive-delta cycles (cycle, clustered in trajectory);
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

- cycle-1 candidate HO-B score, the pre-treatment draw defined in §4.2. Final
  delivered score is reported both unadjusted and adjusted for it by ANCOVA, and
  the adjusted value is the one that carries the claim;
- task, agent, and seed;
- edit success, that is, whether the artifact hash changed and the agent process
  exited cleanly. This was previously used as the self-report variable; it is a
  covariate, not an exposure (§3.2).

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
pseudo-replication. At six cycles and an intra-trajectory correlation of 0.3 the
design effect is 2.5, so the effective sample would be overstated 2.5-fold and
intervals narrowed by about a factor of 1.58.

### 8.1 Unit and models

**The unit of confirmatory inference is the trajectory.**

- Cycle-level binary outcomes (regression acceptance, false rejection) are
  reduced to a per-trajectory proportion and analysed at the trajectory level.
  Every interval comes from resampling trajectories with replacement, never
  cycles. A generalized linear mixed model with a logit link and a random
  intercept per trajectory nested in task-agent-seed targets the same estimand
  and is reported as a sensitivity analysis; at 20 clusters per arm it is the
  more fragile of the two, and it would add a scientific stack to a replication
  package that is otherwise standard library. The estimator that carries the
  claim is the cluster bootstrap, implemented in `analysis/fit_clustered.py`.
- Trajectory-level outcomes (final delivered HO-B score, erosion, cycles to
  first positive delta) are analysed with the trajectory as the row. Final
  delivered score is an ANCOVA on the cycle-1 covariate of §4.2.
- The field layer's common odds ratio is Mantel-Haenszel over agent x task-type
  strata with a Breslow-Day homogeneity check, and repository-clustered
  intervals.
- **No interval anywhere in this study is computed treating cycles as
  independent.** The pooled conditional acceptance rates that `analyze.py`
  already emits are relabelled in the released output as descriptive only, not
  inferential quantities. `analysis/fit_clustered.py` refuses to present any
  contrast as a test while the logs carry no outcome-half delta, because in that
  state the grounded arm's rate is fixed at zero by construction.

### 8.2 Alpha, families, and multiplicity

The confirmatory primary family contains exactly three tests, listed in §6:
B-H1a (S1), B-H1b (S3), and A-H1 (field). Family-wise error is controlled at
0.05 by Holm, and the family is gated in a fixed sequence, so that a later test
is read only if the earlier ones pass:

    B-H1a  ->  B-H1b  ->  B-H2  ->  B-HA1

Everything else in §6 is secondary, reported with intervals, and never used to
support a confirmatory claim. This matters because the untrimmed test count is
at least fifteen once B-H1 is evaluated per task, B-HA1 per task, C-H1 per cost
denominator, and the field layer per outcome; at a nominal five percent each,
the probability of at least one false positive under a global null would exceed
one half. C-H1 is descriptive with no threshold and enters no family. G1 is
excluded from the confirmatory family; keeping a non-SE task in the primary
family of a software engineering submission would also be strategically poor.

### 8.3 Power

Every figure below is produced by `analysis/power_sim.py` and nothing here is
typed by hand. An arm pools the two feedback levels and holds 20 trajectories
(two agents x two feedback levels x five seeds); a single cell holds 10. The
between-trajectory standard deviations the pilot exhibits are 0.0962 for the
self-claim arm and 0.1925 for the judge arm, and the table is computed at 0.15.

| Contrast | n | SE | MDE at 80% power | Sided |
| --- | --- | --- | --- | --- |
| B-H1, grounded vs ungrounded | 20 per arm | 0.047 | 0.118 | one |
| RQ-B2, grounded-sign vs ungrounded-numeric | 10 per cell | 0.067 | 0.167 | one |
| RQ-B2, full 2x2 interaction | 10 per cell | 0.095 | 0.266 | two |

Simulation at each minimum detectable effect returns 0.799 and 0.797, which is
the 0.80 the closed form was solved for. B-H1 is amply powered: at a true effect
of 0.20 its power is 0.995, and the pilot value was 0.56.

**The full 2x2 interaction is underpowered by design and is declared so here
rather than discovered later.** The primary test of RQ-B2 is therefore a single
two-cell contrast, grounded-sign versus ungrounded-numeric. That contrast
answers the gate-versus-information question directly: if a grounded gate that
returns only a sign beats an ungrounded gate that receives the full numeric
signal, the cause is the gate. The full interaction is reported as a secondary,
descriptive quantity.

`analysis/power_sim.py` is a registered artifact. It takes the assumed
intra-trajectory correlation, per-cell true values, and seed count, and returns
power by simulation. It is committed at freeze, and every number in the table
above is reproducible from it.

## 9. Planned size

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

## 10. Exclusions and failure handling

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

## 11. Mechanized outputs

Every run writes append-only JSONL containing configuration, task, agent,
model, seed, factor cell, candidate hash, claim/judge decision, oracle result,
deployed score, canary result, and cost fields. Aggregate scripts consume only
those logs and the frozen AIDev tables. Raw field text is not republished; only
aggregate results and classifier validation counts enter this repository.

## 12. Freeze gates

All boxes must be satisfied in one commit before confirmatory execution:

- [ ] two-annotator claim coding reaches the 0.80 precision/recall thresholds;
- [ ] confirmatory AIDev IDs are locked without outcome inspection;
- [x] S1 and S3 public tests, held-out oracles, and canaries are versioned;
- [x] container mount and network proofs pass on the measurement host;
- [x] two agent adapters pass the same protocol smoke test;
- [ ] model identifiers, prices, seeds, and the 960-cycle budget are filled in;
- [ ] cost ceiling is approved;
- [ ] this commit hash is recorded in the manuscript and run manifest;
- [ ] the HO-A/HO-B oracle split of §4.1 is implemented, versioned, and shown by
  test to keep HO-B unreachable from every gate;
- [ ] the common first cycle of §4.2 is implemented and a branching run is
  verified to produce identical cycle-1 artifacts across all four cells;
- [ ] the self-report elicitation of §5.1 is implemented, and a pilot run shows
  the elicited verdict taking both values;
- [ ] `analysis/power_sim.py` and the mixed-model fitting script are committed
  and reproduce §8.3;
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
  primary family of exactly three tests under fixed-sequence gating, and a
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
  by `--exclude-packet`, and the registered gate still requires two human
  annotators; machine annotation is development evidence, not the gate.
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
  record the plateau. The decision on what replaces it is pending and is a design
  decision, not a threshold decision: the registered 0.80 stands.
- R15 (2026-08-10): the validation packet is stratified by agent and by the
  classifier's own preliminary verdict, so raw packet precision and recall
  estimate the packet rather than the corpus. Stratum weights on the second packet
  range from 1.12 to 91.28, and reweighting moves recall from 0.824 to 0.955 while
  moving precision from 0.739 to 0.726. All validation figures are henceforth
  reported inverse-probability weighted to the frame, with the raw packet figures
  alongside. This corrects an estimation defect in the registered procedure, not a
  threshold.
