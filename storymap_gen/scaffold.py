"""Turn a region, a topic and (optionally) an outline into the skeleton of a spec.

The skeleton is deliberately unfinished: every text field that a person or a model must write contains ``TODO``, and the
quality gate refuses to compile or publish a story that still contains placeholder text.

Three frameworks give the skeleton its shape (docs/writing-guide.md explains them):

* ``outline``  the user's own outline: top-level lines become sections, indented lines become subsections;
* ``systems``  context, hard systems (mobility, built form, water), soft systems (services, parks, environment),
               interactions, proposal; the shape of the best public student district StoryMaps;
* ``essay``    a thesis-driven story: why this place, how we looked, what the data shows, two layers compared, what we learned;
* ``brief``    four short sections with one map slide each.

Every subsection follows the pattern the studied stories share: a finding backed by a number, a map placed next to it,
and what it means for this place.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .sources.geocode import Region
from .sources.manifest import Manifest

DEFAULT_SECTIONS = ["Introduction", "The area at a glance", "What the data shows", "Conclusion"]
_BULLET = re.compile(r"^\s*(?:(?:#{1,6}|[-*+]|\d{1,2}[.)])\s+)?(\S.*?)\s*$")
MAX_SECTIONS = 12
MAX_SUBSECTIONS = 10
FRAMEWORKS = ("auto", "outline", "systems", "essay", "brief")

# words in a title -> the data layers that can illustrate it
THEME_WORDS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"\b(?:transit|subway|metro|train|rail|station|bus|mobility|transport|commut)", re.I), ["transit", "bus_stops", "streets"]),
    (re.compile(r"\b(?:bike|bicycl|cycl)", re.I), ["bike", "streets"]),
    (re.compile(r"\b(?:park|green|open space|recreation|playground|tree)", re.I), ["parks"]),
    (re.compile(r"\b(?:school|educat|student|child)", re.I), ["schools"]),
    (re.compile(r"\b(?:health|hospital|clinic|care)", re.I), ["health"]),
    (re.compile(r"\b(?:water|flood|coast|river|climate|resilien|storm)", re.I), ["water", "parks"]),
    (re.compile(r"\b(?:build|housing|density|height|zoning|land use|landuse|infrastructure|form)", re.I), ["buildings", "landuse"]),
    (re.compile(r"\b(?:street|road|walk|pedestrian|grid|traffic|car)", re.I), ["streets"]),
]


def parse_outline(text: str) -> list[str]:
    """Section titles from an outline (headings, bullets, numbers or plain lines; one level)."""
    return [title for title, _ in parse_outline_tree(text)]


def parse_outline_tree(text: str) -> list[tuple[str, list[str]]]:
    """Sections with their subsections: unindented lines are sections, indented lines belong to the section above."""
    tree: list[tuple[str, list[str]]] = []
    for line in text.splitlines()[:300]:
        m = _BULLET.match(line)
        if not m:
            continue
        lead = line[: len(line) - len(line.lstrip(" \t"))]
        title = re.sub(r"[*_`]", "", m.group(1))[:300]
        if len(lead.expandtabs(4)) < 2:
            tree.append((title, []))
        elif tree and len(tree[-1][1]) < MAX_SUBSECTIONS:
            tree[-1][1].append(title)
    return tree[:MAX_SECTIONS]


def layers_for(title: str, available: list[str], limit: int = 3) -> list[str]:
    """Data layers that fit a title (by keyword), restricted to the layers that exist; the first layers if nothing matches."""
    wanted: list[str] = []
    for pattern, layers in THEME_WORDS:
        if pattern.search(title):
            wanted += [n for n in layers if n in available and n not in wanted]
    return (wanted or available)[:limit]


@dataclass
class Sub:
    title: str
    prompt: str
    layers: list[str] = field(default_factory=list)   # layers that illustrate it; empty = none needed
    needs: str = ""                                   # data the user must bring when no layer exists
    pick: int = 0                                     # take this many layers from the data, round-robin (no fixed theme)


@dataclass
class Sec:
    title: str
    intro: str
    subs: list[Sub]


SYSTEMS: list[Sec] = [
    Sec("Introduction", "Say what this place is, who lives and works here, and the one question the story answers.", [
        Sub("Where it is", "Describe the boundaries and what surrounds the area, with the size of the study area.", ["boundary", "streets"]),
        Sub("Who and what is here", "Population, economy and daily life.", [], "demographic or economic data (census, a city open-data portal)"),
        Sub("Why this place", "State the thesis: what is distinctive here and what the rest of the story will show."),
    ]),
    Sec("Hard systems", "The physical networks that carry people, goods and water. For each: how the place supports the system, "
        "how it strains it, and what is unusual here.", [
        Sub("Mobility", "Streets, bike lanes and transit: how people move in and out, and where access is thin.",
            ["streets", "bike", "transit", "bus_stops"]),
        Sub("Built form and infrastructure", "Buildings and land use: how dense, how mixed, what that asks of the utilities.",
            ["buildings", "landuse"]),
        Sub("Water", "Waterways, shorelines and flood exposure.", ["water"]),
        Sub("Waste", "How waste is collected and treated.", [], "waste or sanitation data (city open data)"),
    ]),
    Sec("Soft systems", "The social and cultural life that uses the hard systems.", [
        Sub("Services and community", "Schools and health care: who can reach them, and how far.", ["schools", "health"]),
        Sub("Culture and public life", "Public spaces, heritage and cultural institutions.", [], "culture or heritage data"),
        Sub("Parks and open space", "How much green space there is, where it is, and who is near it.", ["parks"]),
        Sub("Environment and resilience", "Heat, flooding, tree cover; how well the place can absorb a shock.", ["water", "parks"]),
    ]),
    Sec("Interactions", "Where hard and soft systems meet, and what emerges from it that neither shows alone.", [
        Sub("Where the systems meet", "Compare two layers side by side and say what the comparison reveals.", ["parks", "streets"]),
        Sub("The pattern that emerges", "Name the pattern in one sentence and the evidence for it."),
    ]),
    Sec("Proposal", "What should change, who would do it, and what could go wrong.", [
        Sub("What to change", "Two to four concrete moves, each tied to a finding above."),
        Sub("Risks and open questions", "What the data cannot say, and what to check next."),
    ]),
]

ESSAY: list[Sec] = [
    Sec("Why this place", "The question and why this place is the right one to ask it.", [
        Sub("The question", "Say what the story asks and why a reader should care."),
        Sub("The place", "Where it is, how big it is, what it is known for.", ["boundary", "streets"]),
    ]),
    Sec("How we looked", "The data used, where it comes from, and what it can and cannot show.", [
        Sub("Data and method", "Name each data source and what was measured; state one limit honestly."),
    ]),
    Sec("What the data shows", "Two or three findings, each with a number, a map and what it means.", [
        Sub("Finding one", "The strongest finding, with its measured number.", pick=1),
        Sub("Finding two", "A second finding that adds to or complicates the first.", pick=1),
        Sub("Finding three", "A third finding, ideally about access or inequality of access.", pick=1),
    ]),
    Sec("Two layers compared", "Put two layers side by side and let the reader slide between them.", [
        Sub("What changes between the two", "Describe what the reader sees when moving the slider.", ["parks", "streets"]),
    ]),
    Sec("What we learned", "The answer to the question, the limits of the evidence, and the next step.", [
        Sub("Answer and limits", "Answer in two sentences, then say what the data does not show."),
    ]),
]


def _paragraph(prompt: str, kind: str) -> dict[str, Any]:
    if kind == "evidence":
        return {"type": "paragraph", "text": f"TODO (finding): {prompt} Use a number that a claim backs (40-100 words)."}
    return {"type": "paragraph", "text": "TODO (meaning): how this place supports or strains the system, and what is distinctive "
                                         "about it here (40-100 words)."}


def _map_block(region: Region, layers: list[str]) -> dict[str, Any]:
    return {"type": "map", "map": {"layers": layers, "caption": "TODO: one sentence on what the reader should look at in this map."}}


def _subsection(region: Region, title: str, prompt: str, layers: list[str], needs: str = "") -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{"type": "heading", "level": 3, "text": title}]
    if needs and not layers:
        blocks.append({"type": "paragraph", "text": f"TODO: this part needs {needs}. Add it (--data name=path) and write it from the data, "
                                                    f"or delete this subsection. {prompt}"})
        return blocks
    blocks.append(_paragraph(prompt, "evidence"))
    if layers:
        blocks.append(_map_block(region, layers))
    blocks.append(_paragraph(prompt, "meaning"))
    return blocks


def _section_from(region: Region, sec: Sec, available: list[str], spare: list[str], used: list[int]) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for sub in sec.subs:
        layers = [n for n in sub.layers if n in available]
        if sub.pick and spare:
            layers = [spare[(used[0] + k) % len(spare)] for k in range(sub.pick)]
            used[0] += sub.pick
        if (sub.layers or sub.pick) and not layers and not sub.needs:
            continue  # nothing illustrates it and it needs no outside data: leave it out
        blocks += _subsection(region, sub.title, sub.prompt, layers, sub.needs)
    return {"title": sec.title, "intro": f"TODO: two or three sentences that set up '{sec.title}'. {sec.intro}", "cites": [], "blocks": blocks}


def _tour_slides(available: list[str]) -> dict[str, Any] | None:
    layers = [n for n in available if n != "boundary"]
    if not layers:
        return None
    return {"type": "slides", "slides": [
        {"title": f"TODO: slide title ({n})", "body": "TODO: one short paragraph, using only numbers backed by a claim.",
         "map": {"layers": [n], "caption": "TODO: what to look at."}} for n in layers[:3]]}


def _swipe_blocks(available: list[str]) -> list[dict[str, Any]]:
    pair = [n for n in available if n != "boundary"][:2]
    if len(pair) < 2:
        return []
    return [{"type": "swipe", "map": {"layers": pair}, "left": [pair[0]], "right": [pair[1]],
             "left_label": f"Left: {pair[0]}", "right_label": f"Right: {pair[1]}",
             "caption": "TODO: what changes when the reader moves the slider."}]


def scaffold_spec(region: Region, topic: str, manifest: Manifest | None = None, outline: str | None = None,
                  template: str = "longform", layers: list[str] | None = None, framework: str = "auto") -> dict[str, Any]:
    if framework not in FRAMEWORKS:
        raise ValueError(f"unknown framework '{framework}' (choose from {', '.join(FRAMEWORKS)})")
    available = layers or ([rec.name for rec in manifest.layers] if manifest else [])
    drawn = [n for n in available if n != "boundary"]
    tree = parse_outline_tree(outline) if outline else []
    if outline and outline.strip() and not tree:
        raise ValueError("no sections found in the outline: put one section per line (bullets, numbers or plain lines), subsections indented")
    mode = framework if framework != "auto" else ("outline" if tree else "essay")
    if mode == "outline" and not tree:
        raise ValueError("the 'outline' framework needs an outline with at least one line")
    sections: list[dict[str, Any]] = []
    depth = "standard"
    if mode == "outline":
        for title, subs in tree:
            blocks: list[dict[str, Any]] = []
            for sub in subs or [title]:
                blocks += _subsection(region, sub, f"What the data shows about '{sub}'.", layers_for(sub + " " + title, drawn))
            sections.append({"title": title, "intro": f"TODO: two or three sentences that set up '{title}'.", "cites": [], "blocks": blocks})
    elif mode in ("systems", "essay"):
        depth = "full" if mode == "systems" else "standard"
        used = [0]
        for template_sec in SYSTEMS if mode == "systems" else ESSAY:
            built = _section_from(region, template_sec, available, drawn, used)
            if built["blocks"]:
                sections.append(built)
        for section in sections:
            if section["title"] in ("Interactions", "Two layers compared"):
                section["blocks"] = [b for b in section["blocks"] if b.get("type") != "map"] + _swipe_blocks(drawn)
    else:  # brief
        depth = "brief"
        for i, title in enumerate(DEFAULT_SECTIONS):
            brief: dict[str, Any] = {"title": title, "intro": "TODO: two or three sentences that set up this section.", "cites": [],
                                     "blocks": []}
            if i > 0 and drawn:
                brief["blocks"] = [{"type": "slides", "slides": [{
                    "title": f"TODO: slide title for '{title}'", "body": "TODO: one short paragraph, using only numbers backed by a claim.",
                    "map": {"layers": drawn[:4], "caption": f"{region.name}: {', '.join(drawn[:4])}"}}]}]
            sections.append(brief)
    spec: dict[str, Any] = {
        "title": topic[:300] or region.name,
        "subtitle": region.name[:500],
        "byline": "",
        "template": template if drawn or template == "longform" else "longform",
        "depth": depth,
        "sections": sections,
        "sources": manifest.sources() if manifest else [],
        "claims": [],
    }
    if mode != "outline" and mode != "brief" and template != "longform":
        tour = _tour_slides(available)
        target = next((s for s in sections if s["title"] in ("What the data shows", "Interactions")), sections[-1] if sections else None)
        if tour and target is not None:
            target["blocks"].append(tour)
    if spec["sources"] and sections:
        keys = [s["key"] for s in spec["sources"]]
        for section in sections:
            section["cites"] = keys[:3]
    return spec
