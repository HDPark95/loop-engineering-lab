#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Host runner (the TRUSTED experimenter). Orchestrates the two-container,
two-network isolated lab via docker exec. The AGENT container is network and
filesystem isolated from the ORACLE container; the runner bridges them only to
measure. First proves the isolation adversarially, then runs the two arms.
Requires: docker compose services up (see run_demo.sh)."""
import subprocess, json, os, statistics, time

LAB = os.path.dirname(os.path.abspath(__file__))
COMPOSE = ["sudo", "docker", "compose", "-f", os.path.join(LAB, "docker-compose.yml")]

def dexec(svc, *cmd, seed=None):
    args = COMPOSE + ["exec", "-T"]
    if seed is not None:
        args += ["-e", f"SEED={seed}"]
    args += [svc, *cmd]
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()

def oracle_ip():
    return subprocess.run(
        ["sudo", "docker", "inspect", "-f",
         "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", "loop-eng-lab-oracle-1"],
        capture_output=True, text=True).stdout.strip()

def boundary_probe():
    """Airtight, measured isolation proof. The oracle serves a secret canary on
    oraclenet:8000. The trusted host can retrieve it (server is alive). The agent,
    given the EXACT oracle IP, must not (no route). The agent also tries to read
    the oracle store on its own filesystem. Isolation holds iff host gets the
    canary and the agent gets it via neither network nor filesystem."""
    ip = oracle_ip()
    url = f"http://{ip}:8000/"
    host_get = ""  # wait for the canary server to be ready (no shell sleep)
    for _ in range(20):
        host_get = subprocess.run(["curl", "-s", "--max-time", "2", url], capture_output=True, text=True).stdout.strip()
        if "ORACLE_SECRET" in host_get:
            break
        time.sleep(0.3)
    agent_get = dexec("agent", "curl", "-s", "--max-time", "5", url)
    agent_fs = dexec("agent", "sh", "-c", "cat /oracle/oracle.db 2>&1 | head -c 80; echo; ls -d /oracle 2>&1")
    host_has = "ORACLE_SECRET" in host_get
    agent_net_leak = "ORACLE_SECRET" in agent_get
    agent_fs_leak = "ORACLE_SECRET" in agent_fs
    holds = host_has and (not agent_net_leak) and (not agent_fs_leak)
    report = (
        f"oracle container IP (oraclenet): {ip}\n"
        f"[host  -> oracle:8000] canary retrieved: {host_has}    resp: {host_get[:80]}\n"
        f"[agent -> oracle:8000] canary retrieved: {agent_net_leak}    resp: {(agent_get[:80] or '(empty: no route)')}\n"
        f"[agent -> /oracle  fs] canary leaked:    {agent_fs_leak}    resp: {agent_fs[:80].strip()}\n"
    )
    os.makedirs(os.path.join(LAB, "logs"), exist_ok=True)
    open(os.path.join(LAB, "logs", "boundary.txt"), "w", encoding="utf-8").write(report)
    return report, holds

def reset(seed):
    dexec("agent", "sh", "-c", "rm -rf /artifact/* 2>/dev/null; cp -r /seed/. /artifact/")
    dexec("oracle", "sh", "-c", "rm -f /oracle/oracle.db")

def sim_step(cycle, seed):
    return json.loads(dexec("oracle", "python", "/code/sim_step.py", str(cycle), seed=seed))

def read_claim():
    try:
        return json.loads(dexec("agent", "cat", "/artifact/claim.json")).get("improved", False)
    except Exception:
        return False

ARMS = {
    "in-band-self": lambda claim, delta: claim,        # sees only the agent's claim
    "out-of-band":  lambda claim, delta: delta > 0,    # queries the oracle
}

def run_arm(arm, seed, budget=8):
    reset(seed)
    sim_step(0, seed)  # baseline into the oracle store
    log = []
    for step in range(budget):
        dexec("agent", "python", "/code/agent_edit.py", str(step))  # agent deploys
        r = sim_step(step + 1, seed)                                # oracle scores (isolated)
        claim = read_claim()
        log.append({"cycle": step + 1, "delta": r["delta"], "claim": claim, "accept": ARMS[arm](claim, r["delta"])})
    return log

def mirage(log):
    acc = [r for r in log if r["accept"]]
    return sum(1 for r in acc if r["delta"] <= 0) / len(acc) if acc else 0.0

if __name__ == "__main__":
    print("=== adversarial boundary probe (run from INSIDE the agent container) ===")
    out, holds = boundary_probe()
    print(out)
    print(f"\n>>> ISOLATION HOLDS (agent cannot reach the oracle): {holds}\n")
    print("=== measurement under real container/network isolation ===")
    seeds = [1, 2, 3]
    print(f"{'arm':16}{'mirage_rate':14}{'avg_accepts':12}")
    print("-" * 42)
    for arm in ARMS:
        ms, acc = [], []
        for s in seeds:
            log = run_arm(arm, s)
            ms.append(mirage(log)); acc.append(sum(1 for r in log if r["accept"]))
        print(f"{arm:16}{statistics.mean(ms):<14.2f}{statistics.mean(acc):<12.1f}")
    print("-" * 42)
    print("in-band-self accepts no-op cycles (mirage high); out-of-band gates on the")
    print("oracle the agent provably cannot reach (mirage ~ 0).")
