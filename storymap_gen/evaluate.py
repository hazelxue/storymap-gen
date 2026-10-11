"""Evaluation: deterministic quality gates + an LLM-judge hook.

Deterministic metrics are the CI-style gate (cheap, reproducible). The LLM judge
covers what rules cannot (narrative coherence); it takes an injected callable so
the harness is testable offline and model-agnostic.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from .depth import depth_report
from .grounding import unbacked_numbers
from .spec import ParagraphBlock, StorySpec
from .verify import ClaimResult

PLACEHOLDER = re.compile(r"\bTODO\b|\bTBD\b|\bFIXME\b|\[\[.*?\]\]|lorem ipsum", re.IGNORECASE)
RESOURCE_KEYS = ("image", "map", "video", "thumbnail", "storyTheme")
CITE = re.compile(r"\[(\d+)\]")


def check_graph(story: dict) -> list[str]:
    """Structural integrity of a StoryMaps node graph. Empty list == valid."""
    nodes, res, root = story.get("nodes", {}), story.get("resources", {}), story.get("root")
    issues: list[str] = []
    if root not in nodes:
        return [f"root '{root}' missing from nodes"]
    seen, stack = set(), [root]
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        node = nodes[nid]
        swipe_sides = list(node.get("data", {}).get("contents", {}).values()) if node["type"] == "swipe" else []
        for ch in [*node.get("children", []), *swipe_sides]:
            if ch not in nodes:
                issues.append(f"{nid}: child '{ch}' does not exist")
            else:
                stack.append(ch)
    for nid in set(nodes) - seen:
        issues.append(f"orphan node {nid} ({nodes[nid]['type']})")
    for nid, n in nodes.items():
        for key in RESOURCE_KEYS:
            rid = n.get("data", {}).get(key)
            if isinstance(rid, str) and rid.startswith("r-") and rid not in res:
                issues.append(f"{nid}: resource '{rid}' ({key}) missing")
        if n["type"] == "navigation":
            for link in n["data"]["links"]:
                target = nodes.get(link["nodeId"])
                if target is None or target["type"] != "text" or target["data"].get("type") != "h2":
                    issues.append(f"navigation link {link['nodeId']} is not an existing h2 heading")
    return issues


def _paragraph_texts(story: dict) -> list[str]:
    return [n["data"]["text"] for n in story["nodes"].values()
            if n["type"] == "text" and n["data"].get("type") == "paragraph"]


def unfinished_text(spec: StorySpec) -> list[str]:
    """Places where the story still contains placeholder text (TODO, TBD, [[...]], lorem ipsum)."""
    found = []
    for where, text in [("title", spec.title), ("subtitle", spec.subtitle), *((p.where, p.text) for p in spec.prose())]:
        m = PLACEHOLDER.search(text)
        if m:
            found.append(f"'{m.group(0)}' in {where}")
    return found


def evaluate(spec: StorySpec, story: dict, results: list[ClaimResult]) -> dict:
    graph_issues = check_graph(story)
    n_src = len(spec.sources)
    bad_markers = sorted({int(m) for t in _paragraph_texts(story) for m in CITE.findall(t)
                          if not 1 <= int(m) <= n_src})
    cited = spec.cited_keys()
    uncited = [s.key for s in spec.sources if s.key not in cited]
    slides = [sl for s in spec.sections for sl in s.all_slides()]
    count = lambda st: sum(r.status == st for r in results)  # noqa: E731
    unbacked = [f"{u.text} in {u.location}" for u in unbacked_numbers(spec)]
    unfinished = unfinished_text(spec)
    report: dict[str, Any] = {
        "graph_issues": graph_issues,
        "unbacked_numbers": unbacked,
        "placeholders": unfinished,
        "citation": {
            "sections_with_cites": f"{sum(bool(s.cites) or any(isinstance(b, ParagraphBlock) and b.cites for b in s.blocks) for s in spec.sections)}/{len(spec.sections)}",
            "uncited_sources": uncited,
            "out_of_range_markers": bad_markers,
        },
        "claims": {"total": len(results), "pass": count("PASS"), "fail": count("FAIL"),
                   "error": count("ERROR"), "failed_ids": [r.id for r in results if r.status != "PASS"]},
        "structure": {"sections": len(spec.sections), "slides": len(slides),
                      "template": spec.template},
        "depth": depth_report(spec),
    }
    report["passed"] = not (graph_issues or bad_markers or uncited or unbacked or unfinished
                            or report["claims"]["fail"] or report["claims"]["error"])
    return report


# ---- LLM judge (injected callable; not tied to a vendor) -------------------
RUBRIC = {
    "narrative_arc": "Does the story build from context to evidence to a conclusion?",
    "text_map_consistency": "Does each slide's text describe what its map can plausibly show?",
    "claim_specificity": "Are claims concrete and attributable rather than vague?",
    "tone": "Is the tone consistent and suited to a public audience?",
    "specificity": "Does the text name real streets, parks, stations and numbers of this place instead of generalities?",
    "analysis": "Does it explain why the numbers matter (cause, comparison, consequence), not only list them?",
}


def judge_prompt(spec: StorySpec) -> str:
    body = "\n\n".join(f"{'## ' if p.kind == 'heading' else ''}{p.text}" for p in spec.prose())
    crit = "\n".join(f"- {k}: {v}" for k, v in RUBRIC.items())
    return (f"Score the story below from 1 (poor) to 5 (excellent) on each criterion.\n{crit}\n"
            f'Reply with JSON only: {{"scores": {{criterion: int}}, "notes": str}}\n\n'
            f"# {spec.title}\n{body}")


def judge(spec: StorySpec, call_llm: Callable[[str], str]) -> dict:
    raw = call_llm(judge_prompt(spec))
    data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    scores = data["scores"]
    unknown = set(scores) - set(RUBRIC)
    if unknown or not all(isinstance(v, int) and 1 <= v <= 5 for v in scores.values()):
        raise ValueError(f"judge returned invalid scores: {scores}")
    return {"scores": scores, "mean": sum(scores.values()) / len(scores), "notes": data.get("notes", "")}
