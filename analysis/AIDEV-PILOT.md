# AIDev 10,000-PR feasibility pilot

Status: exploratory feasibility check, not a confirmatory result.

## Data and reproducibility

The pilot pins the public CC BY 4.0 dataset
[`hao-li/AIDev`](https://huggingface.co/datasets/hao-li/AIDev) at revision
`68ed5f4b80d27a9e057fc57567f38bd322ac73ec` (10 May 2026). The seven input
tables and their SHA-256 checksums are recorded in
`results/aidev_pilot/aidev_pilot_summary.json`.

The enriched PR, review, timeline, commit, and task-type tables cover the
33,596-PR AIDev-pop subset rather than the nearly one-million-PR aggregate
table. The human comparison table contains 6,618 PRs from the same released
package. This distinction matters: the pilot does not claim that review or
timeline coverage exists for every PR in the full AIDev corpus.

Reproduce from the repository root:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-aidev.txt
.venv/bin/python analysis/download_aidev.py --data-dir data/aidev
.venv/bin/python analysis/aidev_pilot.py \
  --data-dir data/aidev \
  --output-dir results/aidev_pilot \
  --sample-size 10000
```

Raw parquet files are intentionally gitignored. The committed outputs contain
aggregates only: no PR text, GitHub user name, repository name, URL, or numeric
identifier is written.

The preregistered validation gate has executable preparation and scoring tools.
Preparation writes raw PR text only below the gitignored `data/` directory. Each
annotator receives a separate copy of the blinded packet; only the aggregate
scorer output may be committed.

```sh
.venv/bin/python analysis/prepare_claim_annotation.py \
  --data-dir data/aidev \
  --output-dir data/claim_annotation
.venv/bin/python analysis/llm_annotate_claims.py \
  --packet data/claim_annotation/claim_annotation_packet.csv \
  --output data/claim_annotation/annotator_a.csv --annotator a --engine claude
.venv/bin/python analysis/llm_annotate_claims.py \
  --packet data/claim_annotation/claim_annotation_packet.csv \
  --output data/claim_annotation/annotator_b.csv --annotator b --engine codex
.venv/bin/python analysis/score_claim_annotation.py \
  --annotator-a data/claim_annotation/a.csv \
  --annotator-b data/claim_annotation/b.csv \
  --adjudicated data/claim_annotation/adjudicated.csv \
  --classifier data/claim_annotation/claim_annotation_classifier.csv \
  --output results/claim_validation.json
```

## Validation run of 2026-08-10: the gate did not pass

The annotators in this run were language models, not the two people the
preregistration names. Annotator A ran on Claude and annotator B on Codex under
different prompt wordings, so their errors are not forced to share an engine or
a phrasing. Contested items went to a third rater run on both engines, and an
item was adjudicated only where the two engines returned the same label. This is
machine annotation and is reported as such. It does not discharge the
preregistered human-annotation requirement; it establishes whether the lexical
classifier is worth taking to human validation at all.

`results/claim_validation.json` records the outcome on the 353 adjudicated
items: precision 0.913, recall 0.640, so the 0.80 recall threshold fails and
`freeze_gate_passed` is false. Preregistration section 3.2 step 4 therefore
applies: the classifier is replaced and revalidated on a fresh 400-PR subset
before any confirmatory field analysis.

Two numbers should be read with care. Cohen's kappa of 0.765 is computed on the
adjudicated subset only; across all 400 items, before adjudication removed the
hardest ones, the two annotators agreed on claim presence at kappa 0.604. And
the two third-rater engines agreed on only 50 of the 97 contested items, which
says those items are genuinely ambiguous under the current guidelines rather
than noisy.

Splitting the 87 false negatives that both annotators marked as claims:

- 51 are past-tense descriptions of completed work ("I implemented X",
  "Fixes #54706"). Whether these are completion claims is a construct decision,
  not a classifier defect.
- 30 carry explicit language the rules do not cover, mostly checklist test plans
  ("All acceptance criteria verified", "Confirmed the root cause").
- 3 are non-English bodies. Only 3 of the 400 packet items are non-English, so
  language coverage is not what drives the shortfall in this sample.

Recall also splits sharply by agent: 0.952 for OpenAI Codex against 0.582 for
Copilot, 0.596 for Devin, 0.635 for Claude Code, and 0.700 for Cursor. The
rules track one agent's PR template. The by-agent claim-prevalence spread in the
pilot below therefore measures the classifier as much as the agents, and must
not be read as an agent-level finding.

## Frozen pilot rule

The sample is the 10,000 PR IDs with the lowest SHA-256 value of
`loop-engineering-aidev-pilot-v1:<PR id>`. Claim coding uses conservative
English lexical rules for explicit completion or verification assertions in
the PR body. Titles alone do not count. This is a feasibility rule whose
precision and recall still require blinded manual validation before any
confirmatory field analysis.

## Results

- AI sample: 10,000 of 33,596 PRs; all five agents represented.
- Bodies available: 9,888/10,000 (98.88%).
- Explicit completion or verification claim: 1,375/10,000 (13.75%, Wilson 95%
  interval 13.09% to 14.44%).
- Merged: 7,136/10,000 (71.36%).
- Closed without merge: 2,182/10,000 (21.82%).
- At least one review: 2,523/10,000; changes requested: 322/10,000.
- Human comparison table: 6,618 PRs, of which 5,081 (76.78%) merged.

Claim prevalence differs sharply by agent, from 0.55% for OpenAI Codex to
42.20% for Copilot in this lexical coding. Therefore the unadjusted overall
association between claims and merging is confounded by agent-specific PR
templates and base rates. It must not be interpreted as a causal effect or as
evidence that claims are informative or uninformative. Confirmatory analysis
must stratify by agent and task type, validate the claim classifier, and report
within-stratum estimates.

## P3 decision

1. Completion and verification assertions are present often enough to code,
   but the lexical construct needs manual validation and multilingual coverage.
2. Merge, closed-unmerged, review, and changes-requested outcomes are directly
   observable.
3. A comparable human table exists, but its sampling relation to the agent PRs
   must be documented before adjusted comparisons.
4. Post-merge reverts are not identifiable from the supplied enriched tables:
   commit timeline events have no timestamp and later revert PRs are not linked
   back to the merged PR. The 303 within-PR revert markers in the sample are not
   post-merge outcomes.

The field design therefore proceeds with merge/closed-unmerged and
changes-requested as outcomes. Post-merge revert is excluded unless a separate,
timestamped cross-PR mining pass is preregistered and implemented.
