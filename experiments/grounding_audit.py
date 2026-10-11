"""Audit the recorded A/B specs with the text-to-claim binding check (no model calls).

The 24 accepted specs passed the OLD quality gate (claims verified, citations consistent). How many would fail the
NEW gate because their prose contains numbers that no verified claim backs?
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm import extract_json  # noqa: E402

from storymap_gen.grounding import unbacked_numbers  # noqa: E402
from storymap_gen.spec import StorySpec  # noqa: E402

RUNS = Path(__file__).resolve().parent / "runs"


def main() -> None:
    chains = json.loads((RUNS / "ab_results.json").read_text(encoding="utf-8"))
    chains += json.loads((RUNS / "ablation_grounded.json").read_text(encoding="utf-8"))
    rows = []
    for c in chains:
        if not c["accepted"]:
            continue
        spec = StorySpec.model_validate(extract_json(c["rounds"][-1]["raw"]))
        bad = unbacked_numbers(spec)
        rows.append({"arm": c["arm"], "persona": c["persona"], "trial": c["trial"], "n_unbacked": len(bad),
                     "examples": [f"{u.text} ({u.location})" for u in bad[:3]]})
    (RUNS / "grounding_audit.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    for arm in ("direct", "ask-first", "ask-first+grounded"):
        rs = [r for r in rows if r["arm"] == arm]
        failing = sum(r["n_unbacked"] > 0 for r in rs)
        total = sum(r["n_unbacked"] for r in rs)
        print(f"{arm:20s} specs={len(rs):2d}  would fail the new gate: {failing:2d}/{len(rs)}  unbacked numbers: {total}")
    common = Counter(e.split(" (")[0] for r in rows for e in r["examples"])
    print("most common unbacked numbers:", common.most_common(8))


if __name__ == "__main__":
    main()
