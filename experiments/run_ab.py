"""A/B: ask-first (preferences known up front) vs direct generation (preferences hidden, revise on feedback).

Each chain ends when the spec is ACCEPTED (valid + preferences met + quality gate passed) or after MAX_ROUNDS.
Hidden preferences are simulated by fixed personas, so this measures the value of eliciting preferences
before generating, not human satisfaction.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm import call_claude, extract_json  # noqa: E402
from storymap_gen.compiler import compile_story  # noqa: E402
from storymap_gen.evaluate import evaluate  # noqa: E402
from storymap_gen.spec import StorySpec  # noqa: E402
from storymap_gen.verify import DataStore, verify_claims  # noqa: E402

MAX_ROUNDS = 4
RUNS = Path(__file__).resolve().parent / "runs"
STORE = DataStore.from_files({"streets_centerline": str(ROOT / "fixtures/streets_centerline.geojson")})

PERSONAS = {
    "course-report": dict(purpose="course report", sections=3, max_words=25, template="docked-sidecar", min_claims=2),
    "public-explainer": dict(purpose="public explainer", sections=5, max_words=45, template="longform", min_claims=1),
}

BASE = """Create a StorySpec (JSON) for an ArcGIS StoryMap.

## Topic
Jackson Heights (Queens, NYC): how its street system and its open space system shaped each other.

## Output schema (JSON only, no prose, no code fences)
{"title": str, "subtitle": str, "byline": "Author One; Author Two",
 "template": "docked-sidecar" | "floating-sidecar" | "longform",
 "sources": [{"key": str, "text": str}],
 "claims": [ {"kind": "street_orientation", "id": str, "text": str, "street": str, "relation": "diagonal_to_grid" | "parallel_to_grid"},
             {"kind": "layer_stat", "id": str, "text": str, "layer": "streets_centerline", "field": str|null,
              "agg": "count"|"min"|"max"|"mean"|"sum", "expected": number, "tolerance": number} ],
 "sections": [{"title": str, "intro": str, "cites": [source keys], "claim_ids": [claim ids],
               "slides": [{"title": str, "body": str, "map": {"item_id": str, "caption": str}, "claim_ids": [claim ids]}]}]}

## Data available
Layer streets_centerline: 1,001 street segments. Fields: full_stree (street name, e.g. "BROADWAY", "NORTHERN BLVD",
"34 AVE", "37 AVE", "ROOSEVELT AVE", "82 ST", "JUNCTION BLVD"), st_width (feet; min 6, max 70, mean 32.6).
Web maps: 81186482d5dd48ca9882593adadd6bfe (Hard System Layers), 865dbb45a3b3455590652560174b9ed9 (Street & Open Space Profile).
Sources (use these keys exactly): nyc-lpc = "NYC Landmarks Preservation Commission, Jackson Heights Historic District Designation Report";
city-lore = "City Lore"; nyc-opendata = "NYC Open Data, Building Footprints and Street Centerline"; nyc-parks = "NYC Parks Properties".

## Rules
- Every factual statement about the street data (a count, a width statistic, whether a street is diagonal or parallel to the
  neighbourhood grid) must be a claim, referenced from claim_ids of the slide or section that states it.
- Every section cites at least one source, and every source above is cited at least once.
"""


def grounded_facts() -> str:
    from storymap_gen.verify import orientation_facts
    return orientation_facts(STORE, ["BROADWAY", "NORTHERN BLVD", "JUNCTION BLVD", "ROOSEVELT AVE", "34 AVE", "37 AVE", "82 ST"])


def prompt_initial(arm: str, persona: dict) -> str:
    if arm == "direct":
        return BASE + "\nMake a good story."
    facts = grounded_facts() if arm == "ask-first+grounded" else ""
    return BASE + facts + (f"\n## Scoping answers from the user\n- Purpose: {persona['purpose']}\n- Sections: exactly {persona['sections']}\n"
                   f"- Slide body text: at most {persona['max_words']} words each\n- Layout (template): {persona['template']}\n"
                   f"- At least {persona['min_claims']} verifiable data claim(s)\n")


def assess(raw: str, persona: dict) -> tuple[bool, list[str], dict]:
    """Return (accepted, problems, details)."""
    try:
        spec = StorySpec.model_validate(extract_json(raw))
    except Exception as e:
        return False, [f"the JSON was invalid: {str(e)[:300]}"], {"valid": False}
    problems: list[str] = []
    words = [len(sl.body.split()) for s in spec.sections for sl in s.slides]
    if len(spec.sections) != persona["sections"]:
        problems.append(f"you wrote {len(spec.sections)} sections but I wanted exactly {persona['sections']}")
    if not words or max(words) > persona["max_words"]:
        problems.append(f"your longest slide body is {max(words, default=0)} words but I wanted at most {persona['max_words']}")
    if spec.template != persona["template"]:
        problems.append(f"you used the '{spec.template}' layout but I wanted '{persona['template']}'")
    if len(spec.claims) < persona["min_claims"]:
        problems.append(f"I wanted at least {persona['min_claims']} verifiable claim(s)")
    results = verify_claims(spec, STORE)
    report = evaluate(spec, compile_story(spec), results)
    for r in results:
        if r.status != "PASS":
            problems.append(f"claim '{r.id}' is wrong against the data: {r.detail}")
    if report["graph_issues"]:
        problems.append("graph issues: " + "; ".join(report["graph_issues"][:3]))
    if report["citation"]["uncited_sources"]:
        problems.append(f"sources never cited: {report['citation']['uncited_sources']}")
    if report["citation"]["out_of_range_markers"]:
        problems.append("citation markers out of range")
    details = {"valid": True, "n_sections": len(spec.sections), "max_words": max(words, default=0),
               "template": spec.template, "n_claims": len(spec.claims),
               "claims_pass": report["claims"]["pass"], "claims_total": report["claims"]["total"]}
    return not problems, problems, details


def run_chain(arm: str, persona_name: str, trial: int) -> dict:
    persona = PERSONAS[persona_name]
    prompt = prompt_initial(arm, persona)
    rounds, last_raw = [], ""
    for r in range(1, MAX_ROUNDS + 1):
        c = call_claude(prompt)
        ok, problems, details = assess(c.text, persona) if c.ok else (False, [f"call failed: {c.text}"], {"valid": False})
        rounds.append({"round": r, "input_tokens": c.input_tokens, "output_tokens": c.output_tokens, "cost_usd": c.cost_usd,
                       "accepted": ok, "problems": problems, "details": details, "raw": c.text})
        last_raw = c.text
        if ok or not c.ok:
            break
        prompt = (BASE + "\n## Your previous spec\n" + last_raw + "\n\n## My feedback\nNot accepted. Problems:\n- "
                  + "\n- ".join(problems) + "\nReturn the corrected full spec JSON only.")
    return {"arm": arm, "persona": persona_name, "trial": trial, "accepted": rounds[-1]["accepted"], "rounds": rounds}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--out", default="ab_results.json")
    ap.add_argument("--arms", nargs="+", default=["direct", "ask-first"])
    args = ap.parse_args()
    RUNS.mkdir(exist_ok=True)
    jobs = [(arm, p, t) for t in range(args.trials) for p in PERSONAS for arm in args.arms]
    t0 = time.time()
    with ThreadPoolExecutor(args.workers) as ex:
        results = list(ex.map(lambda j: run_chain(*j), jobs))
    (RUNS / args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(results)} chains in {time.time() - t0:.0f}s -> {RUNS / args.out}")


if __name__ == "__main__":
    main()
