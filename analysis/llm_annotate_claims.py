#!/usr/bin/env python3
"""Label the blinded claim-annotation packet with an LLM annotator.

The packet is already blinded: it carries the PR body and nothing else. The
annotator never sees the merge outcome, the agent name, or the lexical
classifier's preliminary label, so its judgement cannot be anchored on either
the outcome under study or the rule it is used to validate.

Two annotators are run with different engines and different prompt wordings so
that their errors are not forced to correlate. Output is one CSV per annotator
with the same schema score_claim_annotation.py reads. Rows are appended as each
chunk returns, so an interrupted run resumes instead of starting over.

This is machine annotation. Whether it stands in for the preregistered human
annotators is a reporting decision, not something this script decides.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

FIELDNAMES = ("annotation_id", "completion_claim", "verification_claim", "unclassifiable")

CONSTRUCT = """A PR body carries a COMPLETION CLAIM when its text explicitly asserts that the
work of the pull request is done: for example "implemented X", "fixed the bug",
"this completes the migration", "all requirements are now met", a task
checklist whose items are marked done. Describing what the change does without
asserting completeness is NOT a completion claim; "adds a retry helper" is a
description, "the retry problem is now solved" is a claim.

A PR body carries a VERIFICATION CLAIM when its text explicitly asserts that
the change was checked and found good: "tests pass", "verified locally",
"confirmed the fix", "no regressions", "CI is green", pasted successful test
output presented as evidence. Announcing that tests were added or that CI will
run is NOT a verification claim. Reporting a failure is NOT a verification
claim.

Mark UNCLASSIFIABLE only when the body is empty, is pure boilerplate or a bare
template with no substantive prose, or is written in a language you cannot read
well enough to judge. A short but readable body is classifiable.

Judge the body text only. A title is not part of the body here. Do not guess at
the repository, the author, or whether the PR was merged."""

PROMPT_A = """You are annotating pull-request bodies for an empirical software engineering
study. Apply the definitions exactly as written. Do not soften them and do not
add criteria of your own.

{construct}

For each item below, decide the three labels. Answer 1 for yes and 0 for no.
completion_claim and verification_claim are independent; a body may carry both,
one, or neither. When unclassifiable is 1, set the other two to 0.

Return ONLY a JSON array, one object per item, in the order given, with keys
"id", "completion_claim", "verification_claim", "unclassifiable". No prose, no
code fence, no commentary.

{items}"""

PROMPT_B = """Task: read each pull-request description and record whether the author states
that the work is finished and whether the author states that it was checked.

Definitions to apply, verbatim:

{construct}

Work through the items one at a time. For each, ask two questions in order.
First: does the text assert the work is done, as opposed to merely describing
what changed? Second: does the text assert the change was checked and found
good, as opposed to merely mentioning tests or CI? A body can answer yes to
both, to one, or to neither. Reserve unclassifiable for empty, boilerplate, or
unreadable bodies, and when you use it set the other two labels to 0.

Emit your answer as a bare JSON array, one object per item in the given order,
keys "id", "completion_claim", "verification_claim", "unclassifiable", values 0
or 1. Output nothing except that array.

{items}"""

PROMPT_C = """You are the third rater on an empirical software engineering study. Two earlier
raters split on the items below, so read each one carefully. You are not told
how they split, and you should not try to infer it.

{construct}

Method for each item. Find the specific sentence, if any, in which the author
asserts the work is done, and the specific sentence, if any, in which the author
asserts it was checked. If you cannot point to such a sentence, the label is 0.
A heading, a template placeholder, or a bullet that merely names a change is not
such a sentence. Do not credit a claim that lives only in an unchecked checkbox.

Return ONLY a JSON array, one object per item in the given order, with keys
"id", "completion_claim", "verification_claim", "unclassifiable", values 0 or 1.
When unclassifiable is 1 the other two are 0. Emit no prose.

{items}"""

PROMPTS = {"a": PROMPT_A, "b": PROMPT_B, "c": PROMPT_C}


def load_packet(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def done_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["annotation_id"] for row in csv.DictReader(handle)}


def render_items(chunk: list[dict[str, str]], body_cap: int) -> str:
    blocks = []
    for row in chunk:
        body = row["body"] or ""
        if len(body) > body_cap:
            body = body[:body_cap] + "\n[body truncated for length]"
        blocks.append(f'--- ITEM id={row["annotation_id"]} ---\n{body}\n--- END ITEM ---')
    return "\n\n".join(blocks)


def extract_json_array(text: str) -> list[dict]:
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    for candidate in [*fenced, text]:
        start = candidate.find("[")
        end = candidate.rfind("]")
        if start == -1 or end <= start:
            continue
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return parsed
    raise ValueError("no JSON array in model output")


def call_engine(engine: str, model: str | None, prompt: str, timeout: int) -> str:
    if engine == "claude":
        command = ["claude", "-p"]
        if model:
            command += ["--model", model]
    elif engine == "codex":
        command = ["codex", "exec", "--skip-git-repo-check"]
        if model:
            command += ["-m", model]
    else:
        raise SystemExit(f"unknown engine {engine}")
    result = subprocess.run(
        command, input=prompt, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(f"{engine} exited {result.returncode}: {result.stderr[-400:]}")
    return result.stdout


def binary(value) -> int:
    if value in (0, 1):
        return int(value)
    if value in ("0", "1"):
        return int(value)
    if isinstance(value, bool):
        return int(value)
    raise ValueError(f"non-binary label {value!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--annotator", choices=sorted(PROMPTS), required=True)
    parser.add_argument("--engine", choices=("claude", "codex"), required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--chunk-size", type=int, default=10)
    parser.add_argument("--body-cap", type=int, default=8000)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()

    packet = load_packet(args.packet)
    already = done_ids(args.output)
    pending = [row for row in packet if row["annotation_id"] not in already]
    print(f"{len(packet)} in packet, {len(already)} already labelled, {len(pending)} pending")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    handle = args.output.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, lineterminator="\n")
    if not already:
        writer.writeheader()
        handle.flush()

    failures = 0
    for start in range(0, len(pending), args.chunk_size):
        chunk = pending[start : start + args.chunk_size]
        prompt = PROMPTS[args.annotator].format(
            construct=CONSTRUCT, items=render_items(chunk, args.body_cap)
        )
        wanted = {row["annotation_id"] for row in chunk}
        for attempt in range(1, args.attempts + 1):
            try:
                parsed = extract_json_array(
                    call_engine(args.engine, args.model, prompt, args.timeout)
                )
                labels = {}
                for item in parsed:
                    key = str(item["id"])
                    if key not in wanted:
                        continue
                    labels[key] = {
                        "annotation_id": key,
                        "completion_claim": binary(item["completion_claim"]),
                        "verification_claim": binary(item["verification_claim"]),
                        "unclassifiable": binary(item["unclassifiable"]),
                    }
                missing = wanted - set(labels)
                if missing:
                    raise ValueError(f"{len(missing)} items missing from the response")
                for row in chunk:
                    writer.writerow(labels[row["annotation_id"]])
                handle.flush()
                print(f"chunk {start // args.chunk_size + 1}: {len(labels)} rows", flush=True)
                break
            except Exception as error:  # noqa: BLE001 - retry any engine or parse failure
                print(f"  attempt {attempt} failed: {error}", file=sys.stderr, flush=True)
                if attempt == args.attempts:
                    failures += len(chunk)

    handle.close()
    if failures:
        raise SystemExit(f"{failures} items could not be labelled; rerun to resume")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
