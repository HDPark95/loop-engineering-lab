# S1: repository-scale defect repair

S1 uses the pinned SWE-bench Verified instance `django__django-16938` at
`1136aa5005f0ae70fea12796b7e37d6f027b9263`. The selection rule, dataset
digest, official evaluation-image digest, test counts, and harness revision are
frozen in `instance.json`.

The agent receives the complete base repository and issue but not the issue's
test patch, gold patch, oracle cache, or evaluation image. The oracle mounts the
candidate read-only into the official image, disables networking, applies the
hidden test patch only after capturing the candidate diff, and runs the observed
serializer tests. Any change below `tests/` or `django/test/`, any symlink, or a
registered test-skipping/caller-inspection pattern invalidates the candidate.

FAIL_TO_PASS and PASS_TO_PASS cases are separately shuffled by a registered
SHA-256 seed and split into HO-A and HO-B. A half's score is the equal-weighted
mean of its observed FAIL_TO_PASS and PASS_TO_PASS pass rates. Thus maintenance
cannot disappear behind a larger regression denominator, and every score is
derived from test processes rather than candidate-reported values.

The original six-line semantic-version fixture remains in
`s1_defect_repair/` as an apparatus and task-size sensitivity fixture. It is not
the confirmatory S1 task.
