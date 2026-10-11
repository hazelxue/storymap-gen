"""Blind LLM-judge pass over the accepted specs of both arms (real model calls via claude -p).

Checks that ask-first did not buy cheaper tokens at the cost of a worse story. Judge and generator are the
same model family, so absolute scores are optimistic; only the arm comparison is meaningful.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

from analyze import perm_test  # noqa: E402
from llm import call_claude, extract_json  # noqa: E402

from storymap_gen.evaluate import RUBRIC, judge  # noqa: E402
from storymap_gen.spec import StorySpec  # noqa: E402

RUNS = Path(__file__).resolve().parent / "runs"
JUDGE_SYSTEM = "You are a strict, impartial editor scoring a story draft. Reply with the requested JSON only."


def judge_one(item: dict) -> dict:
    spec = StorySpec.model_validate(extract_json(item["raw"]))
    usage = {"in": 0, "out": 0, "cost": 0.0}

    def call(prompt: str) -> str:
        c = call_claude(prompt, system=JUDGE_SYSTEM)
        usage["in"] += c.input_tokens
        usage["out"] += c.output_tokens
        usage["cost"] += c.cost_usd
        return c.text

    try:
        r = judge(spec, call)
    except Exception as e:  # keep the run going; record the failure
        return {**{k: item[k] for k in ("arm", "persona", "trial")}, "error": str(e)[:200]}
    return {**{k: item[k] for k in ("arm", "persona", "trial")}, **r, "judge_cost": usage["cost"]}


def main() -> None:
    chains = json.loads((RUNS / "ab_results.json").read_text(encoding="utf-8"))
    items = [{"arm": c["arm"], "persona": c["persona"], "trial": c["trial"], "raw": c["rounds"][-1]["raw"]}
             for c in chains if c["accepted"]]
    random.Random(0).shuffle(items)  # the judge never sees the arm; order is shuffled
    with ThreadPoolExecutor(5) as ex:
        res = list(ex.map(judge_one, items))
    (RUNS / "judge_results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    ok = [r for r in res if "error" not in r]
    print(f"judged {len(ok)}/{len(res)} specs; total judge cost ${sum(r['judge_cost'] for r in ok):.2f}")
    for arm in ("direct", "ask-first"):
        rs = [r for r in ok if r["arm"] == arm]
        print(arm, f"n={len(rs)}", f"mean={np.mean([r['mean'] for r in rs]):.2f}",
              {k: round(float(np.mean([r["scores"][k] for r in rs])), 2) for k in RUBRIC})
    a = np.array([r["mean"] for r in ok if r["arm"] == "direct"])
    b = np.array([r["mean"] for r in ok if r["arm"] == "ask-first"])
    print(f"permutation p (mean score, direct vs ask-first): {perm_test(a, b):.3f}")


if __name__ == "__main__":
    main()
