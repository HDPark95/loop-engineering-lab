#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conversion model shared by the oracle container. Pure stdlib.
Computes a conversion count from the deployed artifact's real properties."""
import re, json, math, os, random

# A clear call-to-action raises conversion. We credit a genuine commitment CTA,
# i.e. a form/button element or an action phrase in the VISIBLE BODY, however the
# agent phrases it. The page head (title, meta) is stripped first so a page title
# such as <title>가입</title> is not miscounted as a CTA. Soft "learn more / read
# on" links (자세히 알아보기) carry no action phrase and stay uncredited. The agent
# has no path to the oracle, so a faithful detector cannot be reverse-engineered
# or overfit: un-gameability is enforced by isolation, not by a narrow regex.
ACTION_PATTERN = re.compile(
    r"<button"
    r"|<input[^>]+type=['\"]?submit"
    r"|role=['\"]?button"
    r"|sign[\s\-]?up|signup|get[\s\-]?started|start\s*(free|now|today)"
    r"|create\s+(an\s+)?account|join\s+(now|free|us|today)|register|try\s+(it\s+)?free"
    r"|가입|회원가입|신청|등록"
    r"|시작하기|시작하세요|시작해|지금\s*시작|무료로?\s*시작",
    re.I,
)
_HEAD = re.compile(r"<head\b.*?</head>", re.I | re.S)

def has_cta(landing):
    body = _HEAD.sub(" ", landing)   # drop head/title/meta before crediting a CTA
    return 1.0 if ACTION_PATTERN.search(body) else 0.0

def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""

def extract_features(app_dir):
    landing = _read(os.path.join(app_dir, "landing.html"))
    copy = _read(os.path.join(app_dir, "copy.txt"))
    try:
        steps = len(json.loads(_read(os.path.join(app_dir, "funnel.json")) or "{}").get("steps", []))
    except Exception:
        steps = 6
    load_bytes = len(landing.encode("utf-8"))
    cta = has_cta(landing)
    words = len(copy.split())
    return {"load_bytes": load_bytes, "steps": steps, "cta": cta, "words": words}

def conversion_prob(f):
    load_norm = min(f["load_bytes"] / 4000.0, 1.5)
    steps_norm = f["steps"] / 6.0
    copy_q = max(0.0, min(1.0, 1.0 - abs(f["words"] - 80) / 160.0))
    z = -1.2 + 1.4 * f["cta"] - 1.1 * load_norm - 1.0 * steps_norm + 0.9 * copy_q
    return 1.0 / (1.0 + math.exp(-z))

def run_cohort(seed, app_dir, n=200):
    p = conversion_prob(extract_features(app_dir))
    rng = random.Random(seed)
    return sum(1 for _ in range(n) if rng.random() < p)
