#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conversion model shared by the oracle container. Pure stdlib.
Computes a conversion count from the deployed artifact's real properties."""
import re, json, math, os, random

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
    cta = 1.0 if re.search(r"(sign\s*up|signup|가입하기|신청하기|<button)", landing, re.I) else 0.0
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
