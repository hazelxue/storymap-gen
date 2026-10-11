"""Compile a StorySpec into the ArcGIS StoryMaps item-data JSON (node graph).

Target schema was reverse-engineered from a real published story
(fixtures/jackson_heights_story.json): {root, nodes, resources}.
Three templates share the same section skeleton and differ only in how
slides (text + map) are laid out.
"""
from __future__ import annotations

import hashlib
import string
from collections.abc import Mapping

from .spec import MapRef, Section, Slide, StorySpec

_ALPHABET = string.ascii_letters + string.digits
WEB_MERCATOR = {"wkid": 102100, "latestWkid": 3857}


def esc(text: str) -> str:
    """StoryMaps text nodes hold HTML with &apos;/&quot; entities."""
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )


def placeholder_item_id(key: str) -> str:
    return hashlib.sha1(("placeholder:" + key).encode(), usedforsecurity=False).hexdigest()[:32]


class _Graph:
    def __init__(self, seed: str, map_ids: Mapping[str, str] | None = None):
        self.seed = seed
        self.map_ids = dict(map_ids or {})
        self.nodes: dict[str, dict] = {}
        self.resources: dict[str, dict] = {}
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        digest = hashlib.sha1(f"{self.seed}:{self._n}".encode(), usedforsecurity=False).digest()
        return f"{prefix}-" + "".join(_ALPHABET[b % len(_ALPHABET)] for b in digest[:6])

    def node(self, type_: str, data: dict | None = None, config: dict | None = None,
             children: list[str] | None = None) -> str:
        nid = self._id("n")
        node: dict = {"type": type_}
        if data is not None:
            node["data"] = data
        if config is not None:
            node["config"] = config
        if children is not None:
            node["children"] = children
        self.nodes[nid] = node
        return nid

    def resource(self, type_: str, data: dict, rid: str | None = None) -> str:
        rid = rid or self._id("r")
        self.resources[rid] = {"type": type_, "data": data}
        return rid

    # ---- block helpers -------------------------------------------------
    def text(self, kind: str, text: str, **extra) -> str:
        data = {"type": kind, "text": text if kind == "numbered-list" else esc(text), **extra}
        cfg = {"size": "wide"} if kind in ("h3", "h4") else None
        return self.node("text", data, cfg)

    def webmap(self, m: MapRef) -> str:
        item_id = m.item_id or self.map_ids.get(m.key) or placeholder_item_id(m.key)
        rid = f"r-{item_id}"
        res: dict[str, object] = {"itemId": item_id, "itemType": "Web Map", "type": "default"}
        data = {"caption": m.caption, "map": rid, "isShowingLegend": True,
                "legendPinned": True, "timeSlider": False}
        if m.extent:
            xmin, ymin, xmax, ymax = m.extent
            ext = {"spatialReference": WEB_MERCATOR, "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}
            res["extent"] = ext
            data["extent"] = ext
            data["viewPlacement"] = "extent"
        self.resource("webmap", res, rid=rid)
        return self.node("webmap", data)


# ---- slide layouts (the three templates) ---------------------------------
def _slides_sidecar(g: _Graph, slides: list[Slide], subtype: str) -> list[str]:
    slide_ids = []
    for sl in slides:
        panel = g.node("immersive-narrative-panel", {"panelStyle": "themed"},
                       children=[g.text("h3", sl.title), g.text("paragraph", sl.body)])
        slide_ids.append(g.node("immersive-slide", {"transition": "fade"},
                                children=[panel, g.webmap(sl.map)]))  # type: ignore[arg-type]
    return [g.node("immersive", {"type": "sidecar", "subtype": subtype,
                                 "narrativePanelPosition": "start", "narrativePanelSize": "small"},
                   children=slide_ids)]


def _slides_longform(g: _Graph, slides: list[Slide]) -> list[str]:
    out: list[str] = []
    for sl in slides:
        out += [g.text("h3", sl.title), g.text("paragraph", sl.body)]
        if sl.map is not None:
            out.append(g.webmap(sl.map))
    return out


def _layout_slides(g: _Graph, spec: StorySpec, slides: list[Slide]) -> list[str]:
    if not slides:
        return []
    if spec.template == "docked-sidecar":
        return _slides_sidecar(g, slides, "docked-panel")
    if spec.template == "floating-sidecar":
        return _slides_sidecar(g, slides, "floating-panel")
    return _slides_longform(g, slides)


def _section(g: _Graph, spec: StorySpec, sec: Section) -> tuple[str, list[str]]:
    heading = g.text("h2", sec.title)
    blocks = [heading]
    if sec.intro:
        blocks.append(g.text("paragraph", sec.intro))
    if sec.image_url:
        rid = g.resource("image", {"src": sec.image_url, "provider": "uri"})
        blocks.append(g.node("image", {"image": rid, "caption": sec.image_caption},
                             {"size": "float", "floatAlignment": "start"}))
    if sec.cites:
        nums = "".join(f"[{spec.citation_number(k)}]" for k in sec.cites)
        blocks.append(g.text("paragraph", f"Sources: {nums}", textSize="small"))
    blocks += _layout_slides(g, spec, sec.slides)
    return heading, blocks


def compile_story(spec: StorySpec, map_ids: Mapping[str, str] | None = None) -> dict:
    """Compile a spec to StoryMaps item data.

    ``map_ids`` maps ``MapRef.key`` to the item id of a generated Web Map. Maps that have no id yet get a deterministic
    placeholder id, which keeps the graph valid for offline checks but must be replaced before publishing.
    """
    g = _Graph(seed=spec.title, map_ids=map_ids)
    theme_rid = g.resource("story-theme", {"themeId": spec.theme, "themeBaseVariableOverrides": {}})

    cover_children = []
    if spec.cover_image_url:
        rid = g.resource("image", {"src": spec.cover_image_url, "provider": "uri"})
        cover_children.append(g.node("image", {"image": rid}))
    cover = g.node("storycover",
                   {"type": "minimal", "title": spec.title, "summary": spec.subtitle, "byline": spec.byline,
                    "titlePanelVerticalPosition": "top", "titlePanelHorizontalPosition": "start",
                    "titlePanelStyle": "gradient"},
                   {"isHidden": False}, cover_children)

    body: list[str] = []
    nav_targets: list[str] = []
    for sec in spec.sections:
        heading, blocks = _section(g, spec, sec)
        nav_targets.append(heading)
        body += blocks

    if spec.sources:
        ref_head = g.text("h2", "Data & Sources")
        items = "".join(f"<li>{esc(s.text)}</li>" for s in spec.sources)
        body += [ref_head, g.text("numbered-list", items)]

    nav = g.node("navigation", {"links": [{"nodeId": n} for n in nav_targets]}, {"isHidden": False})
    credits = g.node("credits", None, {"isHidden": True},
                     [g.node("attribution", {"content": "", "attribution": ""})])

    root = g.node("story", {"storyTheme": theme_rid},
                  {"coverDate": "first-published", "isSocialIconsHidden": False,
                   "shouldHideSimilarItems": True, "storyLocale": spec.locale},
                  [cover, nav, *body, credits])
    return {"root": root, "nodes": g.nodes, "resources": g.resources}
