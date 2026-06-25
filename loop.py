#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""loop-engineering-lab MVP apparatus.

Validates the MEASUREMENT, not yet the finding. A scripted stub agent edits the
artifact (some real improvements, some cosmetic no-ops) and ALWAYS claims it
improved things (modeling self-deception). The only thing that changes between
arms is the evaluator that gates the loop:

  in-band-self : accepts a cycle if the agent claims improvement (sees only the claim)
  out-of-band  : accepts a cycle only if the oracle's real conversion delta > 0

progress-mirage rate = fraction of ACCEPTED cycles whose real oracle delta <= 0.

The real-agent pilot (replacing the stub with a frontier LLM) is the gated next
step; it burns shared model quota and should run on a dedicated host.
"""
import os, json, shutil, statistics
import simulator as S

ROOT = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(ROOT, "app_template")

# ---------- stub agent edits (agent zone: only touches workdir) ----------
def add_cta(app):
    p = os.path.join(app, "landing.html"); html = S._read(p)
    if "<button" not in html.lower():
        open(p, "w", encoding="utf-8").write(html.replace("</body>", '<button id="cta">지금 가입하기</button>\n</body>'))
        return "real:add_cta"
    return "noop:add_cta"

def trim_steps(app):
    p = os.path.join(app, "funnel.json")
    try: d = json.loads(S._read(p) or "{}")
    except Exception: d = {"steps": []}
    if len(d.get("steps", [])) > 3:
        d["steps"] = d["steps"][:3]; open(p, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False)); return "real:trim_steps"
    return "noop:trim_steps"

def trim_copy(app):
    p = os.path.join(app, "copy.txt"); w = S._read(p).split()
    if len(w) > 120:
        open(p, "w", encoding="utf-8").write(" ".join(w[:80])); return "real:trim_copy"
    return "noop:trim_copy"

def cosmetic(app):  # NO real effect on conversion (only adds a comment = tiny load increase)
    p = os.path.join(app, "landing.html"); html = S._read(p)
    open(p, "w", encoding="utf-8").write(html + "\n<!-- spacing polish -->\n")
    return "noop:cosmetic"

STUB_PLAN = [cosmetic, add_cta, cosmetic, trim_steps, cosmetic, trim_copy, cosmetic, cosmetic]

# ---------- evaluators (the manipulated IV: information channel) ----------
def eval_in_band_self(agent_claim, oracle_delta):  # sees ONLY the agent's claim
    return agent_claim
def eval_out_of_band(agent_claim, oracle_delta):   # queries the oracle (world state)
    return oracle_delta > 0
EVALUATORS = {"in-band-self": eval_in_band_self, "out-of-band": eval_out_of_band}

def run_arm(arm, seed, budget=8):
    workdir = os.path.join(ROOT, f".work_{arm}_{seed}")
    if os.path.exists(workdir): shutil.rmtree(workdir)
    shutil.copytree(TEMPLATE, workdir)
    db = os.path.join(ROOT, "oracle", f"oracle_{arm}_{seed}.db")
    if os.path.exists(db): os.remove(db)
    evalfn = EVALUATORS[arm]
    conv_prev, _, _ = S.run_cohort(seed, workdir); S.oracle_record(db, 0, conv_prev)
    conv0 = conv_prev
    log = []
    for i, edit in enumerate(STUB_PLAN[:budget], 1):
        kind = edit(workdir)                      # agent deploys a change (agent zone)
        conv, _, _ = S.run_cohort(seed, workdir)  # simulator scores it (oracle zone)
        S.oracle_record(db, i, conv)              # monitor records the real trajectory
        delta = conv - conv_prev
        claim = True                              # stub always claims improvement (self-deception)
        accept = evalfn(claim, delta)             # arm-specific gate
        log.append({"cycle": i, "edit": kind, "claim": claim, "oracle_delta": delta, "accept": accept})
        conv_prev = conv
    shutil.rmtree(workdir)
    return log, conv0, conv_prev

def mirage_rate(log):
    acc = [r for r in log if r["accept"]]
    return (sum(1 for r in acc if r["oracle_delta"] <= 0) / len(acc)) if acc else 0.0

def main():
    seeds = [1, 2, 3, 4, 5]
    print(f"{'arm':14} {'mirage_rate':12} {'accepts':9} {'conv:start->end':18}")
    print("-" * 56)
    rows = {}
    for arm in ["in-band-self", "out-of-band"]:
        mrs, accs, starts, ends = [], [], [], []
        for s in seeds:
            log, c0, c1 = run_arm(arm, s)
            mrs.append(mirage_rate(log)); accs.append(sum(1 for r in log if r["accept"]))
            starts.append(c0); ends.append(c1)
        rows[arm] = (statistics.mean(mrs), statistics.mean(accs), statistics.mean(starts), statistics.mean(ends))
        print(f"{arm:14} {rows[arm][0]:<12.2f} {rows[arm][1]:<9.1f} {rows[arm][2]:.0f} -> {rows[arm][3]:.0f}")
    print("-" * 56)
    print("reading: in-band-self accepts cosmetic no-op cycles (oracle delta <= 0),")
    print("so its progress-mirage rate is high; out-of-band rejects them (mirage ~ 0).")
    print("NOTE: stub agent makes the SAME edits in both arms, so final conversion matches.")
    print("Outcome divergence (out-of-band reaching more real progress) needs the")
    print("reactive REAL agent, which is the gated pilot.")

if __name__ == "__main__":
    main()
