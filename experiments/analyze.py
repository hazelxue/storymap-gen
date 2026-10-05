"""Summarise the A/B run: acceptance, rounds, tokens (raw and overhead-adjusted), cost, claim accuracy."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

RUNS = Path(__file__).resolve().parent / "runs"
# Fixed harness context of every `claude -p` call (cache read 32,725 + cache creation 2,255 + 2), measured with a
# trivial prompt; identical for both arms, so subtracting it isolates task tokens.
OVERHEAD = 34_982


def chain_stats(c: dict) -> dict:
    rs = c["rounds"]
    first = rs[0]
    d = first["details"]
    return {
        "arm": c["arm"], "persona": c["persona"], "accepted": c["accepted"], "rounds": len(rs),
        "first_shot": first["accepted"],
        "raw_tokens": sum(r["input_tokens"] + r["output_tokens"] for r in rs),
        "task_tokens": sum(max(0, r["input_tokens"] - OVERHEAD) + r["output_tokens"] for r in rs),
        "output_tokens": sum(r["output_tokens"] for r in rs),
        "cost": sum(r["cost_usd"] for r in rs),
        "claims_pass": d.get("claims_pass", 0), "claims_total": d.get("claims_total", 0),
        "problems": [p.split(" ")[0] + " " + p.split(" ")[1] if p else "" for r in rs[:1] for p in r["problems"]],
        "raw_problems": first["problems"],
    }


def perm_test(a: np.ndarray, b: np.ndarray, n: int = 20000, seed: int = 0) -> float:
    """Two-sided permutation test on the difference of means."""
    rng = np.random.default_rng(seed)
    obs = abs(a.mean() - b.mean())
    pool = np.concatenate([a, b])
    cnt = 0
    for _ in range(n):
        rng.shuffle(pool)
        cnt += abs(pool[: len(a)].mean() - pool[len(a):].mean()) >= obs
    return (cnt + 1) / (n + 1)


def boot_ci(a: np.ndarray, b: np.ndarray, n: int = 10000, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    d = [rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean() for _ in range(n)]
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def categorize(problem: str) -> str:
    for key, name in [("sections", "section count"), ("longest slide", "text length"), ("layout", "layout"),
                      ("verifiable", "too few claims"), ("claim '", "claim wrong vs data"),
                      ("never cited", "uncited source"), ("invalid", "invalid JSON/spec")]:
        if key in problem:
            return name
    return "other"


def summarise(path: Path) -> dict:
    chains = [chain_stats(c) for c in json.loads(path.read_text(encoding="utf-8"))]
    out: dict = {"n_chains": len(chains), "arms": {}}
    for arm in ("direct", "ask-first"):
        cs = [c for c in chains if c["arm"] == arm]
        passed = sum(c["claims_pass"] for c in cs)
        total = sum(c["claims_total"] for c in cs)
        out["arms"][arm] = {
            "n": len(cs),
            "accepted_within_4": sum(c["accepted"] for c in cs) / len(cs),
            "first_shot_accepted": sum(c["first_shot"] for c in cs) / len(cs),
            "mean_rounds": float(np.mean([c["rounds"] for c in cs])),
            "mean_cost_usd": float(np.mean([c["cost"] for c in cs])),
            "mean_raw_tokens": float(np.mean([c["raw_tokens"] for c in cs])),
            "mean_task_tokens": float(np.mean([c["task_tokens"] for c in cs])),
            "mean_output_tokens": float(np.mean([c["output_tokens"] for c in cs])),
            "sd_task_tokens": float(np.std([c["task_tokens"] for c in cs], ddof=1)),
            "round1_claim_accuracy": passed / total if total else None,
            "round1_claims_total": total,
            "round1_problem_counts": dict(Counter(categorize(p) for c in cs for p in c["raw_problems"])),
        }
    d = [c for c in chains if c["arm"] == "direct"]
    a = [c for c in chains if c["arm"] == "ask-first"]
    for key in ("cost", "task_tokens", "rounds"):
        x, y = np.array([c[key] for c in d], float), np.array([c[key] for c in a], float)
        lo, hi = boot_ci(x, y)
        out[f"diff_{key}"] = {"direct_minus_ask": float(x.mean() - y.mean()), "ci95": [lo, hi], "perm_p": perm_test(x, y),
                              "ratio": float(x.mean() / y.mean())}
    out["by_persona"] = {
        p: {arm: {"mean_cost_usd": float(np.mean([c["cost"] for c in chains if c["arm"] == arm and c["persona"] == p])),
                  "mean_rounds": float(np.mean([c["rounds"] for c in chains if c["arm"] == arm and c["persona"] == p]))}
            for arm in ("direct", "ask-first")}
        for p in sorted({c["persona"] for c in chains})}
    return out


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "ab_results.json"
    res = summarise(RUNS / name)
    (RUNS / "ab_summary.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))


def extra(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    pref_keys = ("sections", "longest slide", "layout", "verifiable")
    out: dict = {}
    for arm in ("direct", "ask-first"):
        cs = [c for c in raw if c["arm"] == arm]
        r1 = [c["rounds"][0] for c in cs]
        out[arm] = {
            "rounds_distribution": dict(Counter(len(c["rounds"]) for c in cs)),
            "chains_with_preference_problem_r1": sum(any(k in p for p in r["problems"] for k in pref_keys) for r in r1),
            "chains_with_data_claim_problem_r1": sum(any("claim '" in p for p in r["problems"]) for r in r1),
            "chains_with_ONLY_data_problem_r1": sum(all("claim '" in p for p in r["problems"]) and bool(r["problems"]) for r in r1),
            "mean_claims_per_spec_r1": float(np.mean([r["details"].get("claims_total", 0) for r in r1])),
            "tokens_in_rejected_rounds_share": float(
                sum(r["output_tokens"] for c in cs for r in c["rounds"] if not r["accepted"])
                / sum(r["output_tokens"] for c in cs for r in c["rounds"])),
        }
    wrong = Counter()
    for c in raw:
        for p in c["rounds"][0]["problems"]:
            if p.startswith("claim '"):
                wrong[p.split("details")[0].split(": ")[1].split(":")[0] + " | " + p.split("claim says ")[-1]] += 1
    out["wrong_claims_round1 (street | what the model claimed)"] = dict(wrong.most_common(8))
    return out


if __name__ == "__main__":
    print(json.dumps(extra(RUNS / (sys.argv[1] if len(sys.argv) > 1 else "ab_results.json")), indent=1))
