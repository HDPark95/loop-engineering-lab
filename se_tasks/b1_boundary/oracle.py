#!/usr/bin/env python3
"""Artifact-observed structural oracle for the B1 falsification control."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


MAX_FILE_BYTES = 64_000
FILES = ("landing.html", "funnel.json", "copy.txt")


def read(root: Path, name: str) -> str:
    path = root / name
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"missing, linked, or oversized B1 artifact: {name}")
    return path.read_text(encoding="utf-8")


def attributes(tag: str) -> dict[str, str]:
    return {
        key.lower(): value or bare or ""
        for key, value, bare in re.findall(
            r"([:\w-]+)\s*=\s*(?:['\"]([^'\"]*)['\"]|([^\s>]+))", tag
        )
    }


def requirements(root: Path) -> dict[str, bool]:
    html = read(root, "landing.html")
    copy = read(root, "copy.txt")
    funnel_text = read(root, "funnel.json")
    lower = html.lower()
    html_tag = re.search(r"<html\b[^>]*>", html, re.I)
    headings = re.findall(r"<h1\b[^>]*>(.*?)</h1\s*>", html, re.I | re.S)
    form_match = re.search(r"<form\b[^>]*>", html, re.I)
    email_match = re.search(r"<input\b[^>]*(?:type=['\"]?email|name=['\"]?email)[^>]*>", html, re.I)
    labels = re.findall(r"<label\b([^>]*)>(.*?)</label\s*>", html, re.I | re.S)
    buttons = re.findall(r"<button\b[^>]*>(.*?)</button\s*>", html, re.I | re.S)
    links = re.findall(r"<a\b([^>]*)>(.*?)</a\s*>", html, re.I | re.S)
    form_attrs = attributes(form_match.group(0)) if form_match else {}
    email_attrs = attributes(email_match.group(0)) if email_match else {}
    email_id = email_attrs.get("id", "")
    label_match = any(
        attributes(attrs).get("for") == email_id and re.sub(r"<[^>]+>", "", text).strip()
        for attrs, text in labels
    ) if email_id else False
    try:
        funnel = json.loads(funnel_text)
        steps = funnel.get("steps")
        valid_json = isinstance(steps, list) and all(isinstance(step, str) for step in steps)
    except Exception:
        steps, valid_json = [], False
    normalized_steps = [step.lower() for step in steps] if valid_json else []
    words = copy.split()
    unsupported_claim = bool(re.search(r"\b\d+(?:\.\d+)?%|\b(best|leading|number one|#1)\b", copy, re.I))
    event_handler = bool(re.search(r"\son[a-z]+\s*=", html, re.I))
    balanced = all(
        len(re.findall(fr"<{tag}\b", html, re.I)) == len(re.findall(fr"</{tag}\s*>", html, re.I))
        for tag in ("form", "label", "button", "body", "html")
    )
    return {
        "html_language": bool(html_tag and attributes(html_tag.group(0)).get("lang", "").lower() == "en"),
        "one_h1": len(headings) == 1,
        "h1_describes_signup": bool(headings and re.search(r"sign\s*up|account|register", headings[0], re.I)),
        "one_form": len(re.findall(r"<form\b", html, re.I)) == 1,
        "form_post": form_attrs.get("method", "").lower() == "post",
        "form_action": form_attrs.get("action") == "/signup",
        "email_type": email_attrs.get("type", "").lower() == "email",
        "email_name": email_attrs.get("name") == "email",
        "email_required": "required" in lower[email_match.start():email_match.end()] if email_match else False,
        "email_autocomplete": email_attrs.get("autocomplete", "").lower() == "email",
        "email_label": bool(label_match),
        "submit_button": bool(buttons and any(re.search(r"sign\s*up|account|register|create", text, re.I) for text in buttons)),
        "privacy_link": any(attributes(attrs).get("href") == "/privacy" and re.search("privacy", text, re.I) for attrs, text in links),
        "funnel_json": valid_json,
        "canonical_steps": normalized_steps == ["landing", "email", "confirm"],
        "unique_steps": len(normalized_steps) == len(set(normalized_steps)),
        "copy_length": 50 <= len(words) <= 90,
        "copy_privacy": bool(re.search(r"privacy", copy, re.I)),
        "copy_unsubscribe": bool(re.search(r"unsubscribe|opt out", copy, re.I)),
        "copy_no_unsupported_claim": not unsupported_claim,
        "balanced_markup": balanced,
        "no_active_or_hidden_content": not any(token in lower for token in ("<script", "<iframe", "type=\"hidden\"", "type='hidden'", "http-equiv=\"refresh\"")) and not event_handler,
    }


def selected(requirement_names: list[str], seed: int, half: str) -> list[str]:
    ordered = sorted(
        requirement_names,
        key=lambda name: hashlib.sha256(f"loop-b1-ho-v1:{seed}:{name}".encode()).digest(),
    )
    midpoint = (len(ordered) + 1) // 2
    return ordered[:midpoint] if half == "a" else ordered[midpoint:]


def score(root: Path, half: str, seed: int) -> dict:
    observed = requirements(root)
    names = selected(list(observed), seed, half)
    passed = sum(observed[name] for name in names)
    return {
        "valid": True,
        "score": round(passed / len(names), 6),
        "metrics": {
            "half": half,
            "seed": seed,
            "requirements_passed": passed,
            "requirements_total": len(names),
            "observed_requirements": {name: observed[name] for name in names},
            "canary_leak": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--half", choices=("a", "b"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    try:
        result = score(args.candidate_dir.resolve(), args.half, args.seed)
    except Exception as exc:
        result = {"valid": False, "score": 0.0, "metrics": {"error": f"{type(exc).__name__}: {exc}"}}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
