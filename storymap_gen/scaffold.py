"""Turn a region, a topic and (optionally) an outline into the skeleton of a spec.

The skeleton is deliberately unfinished: every text field that a person or a model must write contains ``TODO``, and the
quality gate refuses to compile or publish a story that still contains placeholder text.
"""
from __future__ import annotations

import re
from typing import Any

from .sources.geocode import Region
from .sources.manifest import Manifest

DEFAULT_SECTIONS = ["Introduction", "The area at a glance", "What the data shows", "Conclusion"]
_BULLET = re.compile(r"^\s*(?:#{1,6}|[-*+]|\d{1,2}[.)])\s+(.+?)\s*$")
MAX_SECTIONS = 12


def parse_outline(text: str) -> list[str]:
    """Section titles from a markdown outline: headings, bullets or numbered lines (one level)."""
    titles = []
    for line in text.splitlines()[:200]:
        m = _BULLET.match(line)
        lead = line[: len(line) - len(line.lstrip(" \t"))]
        if m and len(lead.expandtabs(4)) < 2:  # any indentation marks a sub-point, not a section
            titles.append(re.sub(r"[*_`]", "", m.group(1))[:300])
    return titles[:MAX_SECTIONS]


def scaffold_spec(region: Region, topic: str, manifest: Manifest | None = None, outline: str | None = None,
                  template: str = "docked-sidecar", layers: list[str] | None = None) -> dict[str, Any]:
    names = layers or ([rec.name for rec in manifest.layers] if manifest else [])
    titles = (parse_outline(outline) if outline else []) or DEFAULT_SECTIONS
    sections: list[dict[str, Any]] = []
    for i, title in enumerate(titles):
        sec: dict[str, Any] = {"title": title, "intro": "TODO: two or three sentences that set up this section.", "cites": []}
        if i > 0 and names:
            sec["slides"] = [{
                "title": f"TODO: slide title for '{title}'",
                "body": "TODO: one short paragraph, using only numbers backed by a claim.",
                "map": {"layers": names[:4], "caption": f"{region.name}: {', '.join(names[:4])}"},
            }]
        sections.append(sec)
    spec: dict[str, Any] = {
        "title": topic[:300] or region.name,
        "subtitle": region.name[:500],
        "byline": "",
        "template": template if names else "longform",
        "sections": sections,
        "sources": manifest.sources() if manifest else [],
        "claims": [],
    }
    if spec["sources"] and sections:
        keys = [s["key"] for s in spec["sources"]]
        for sec in sections:
            sec["cites"] = keys[:3]
    return spec
