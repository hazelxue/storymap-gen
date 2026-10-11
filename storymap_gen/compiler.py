"""Compile a StorySpec into the ArcGIS StoryMaps item-data JSON (node graph).

Target schema was reverse-engineered from published stories (fixtures/jackson_heights_story.json, and the structure
of public student stories studied for docs/case-study-patterns.md): {root, nodes, resources}.
Three templates share the same section skeleton and differ only in how slides (text + map) are laid out; the content
blocks of a section (headings, paragraphs, lists, maps, swipes, key figures, images) are the same in all three.
"""
from __future__ import annotations

import hashlib
import re
import string
from collections.abc import Mapping

from .spec import (
    BulletsBlock,
    FiguresBlock,
    HeadingBlock,
    ImageBlock,
    MapBlock,
    MapRef,
    ParagraphBlock,
    QuoteBlock,
    Section,
    SeparatorBlock,
    Slide,
    SlidesBlock,
    StorySpec,
    SwipeBlock,
)
from .webmap import layer_id, layer_title

_ALPHABET = string.ascii_letters + string.digits
WEB_MERCATOR = {"wkid": 102100, "latestWkid": 3857}

_LINK = re.compile(r"\[([^\]]{1,300})\]\((https://[^\s)]{1,2000})\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])")


def esc(text: str) -> str:
    """StoryMaps text nodes hold HTML with &apos;/&quot; entities."""
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )


def inline(text: str) -> str:
    """Escaped text with the small markup spec text allows: **bold**, *italic*, [label](https://link).

    Everything is escaped first, so the only tags that can appear are the ones produced here (links are https only).
    """
    out = esc(text)
    out = _LINK.sub(lambda m: f'<a href="{m.group(2)}" rel="noopener noreferrer" target="_blank">{m.group(1)}</a>', out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    return _ITALIC.sub(r"<em>\1</em>", out)


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
        # no "wide" size: in a longform story it pushed sub-headings out of line with the paragraphs under them
        data = {"type": kind, "text": text if kind in ("numbered-list", "bullet-list") else esc(text), **extra}
        return self.node("text", data)

    def rich(self, kind: str, text: str, **extra) -> str:
        return self.node("text", {"type": kind, "text": inline(text), **extra})

    def _map_resource(self, m: MapRef) -> str:
        item_id = m.item_id or self.map_ids.get(m.key) or placeholder_item_id(m.key)
        rid = f"r-{item_id}"
        res: dict[str, object] = {"itemId": item_id, "itemType": "Web Map", "type": "default"}
        if m.extent:
            xmin, ymin, xmax, ymax = m.extent
            res["extent"] = {"spatialReference": WEB_MERCATOR, "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}
        self.resource("webmap", res, rid=rid)
        return rid

    def webmap(self, m: MapRef, caption: str | None = None, layer_visibility: dict[str, bool] | None = None) -> str:
        rid = self._map_resource(m)
        data: dict = {"caption": m.caption if caption is None else caption, "map": rid, "isShowingLegend": True,
                      "legendPinned": True, "timeSlider": False}
        if m.extent:
            xmin, ymin, xmax, ymax = m.extent
            data["extent"] = {"spatialReference": WEB_MERCATOR, "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}
            data["viewPlacement"] = "extent"
        if layer_visibility is not None:
            data["mapLayers"] = [{"id": layer_id(n), "title": layer_title(n), "visible": v}
                                 for n, v in layer_visibility.items()]
        return self.node("webmap", data)

    def swipe(self, b: SwipeBlock) -> str:
        names = list(b.map.layers)
        left = self.webmap(b.map, b.left_label, {n: n in b.left for n in names})
        right = self.webmap(b.map, b.right_label, {n: n in b.right for n in names})
        return self.node("swipe", {"contents": {"0": left, "1": right}, "legend": [left, right], "legendPinned": True,
                                   "caption": b.caption}, {"size": "wide"})

    def figures(self, b: FiguresBlock) -> str:
        n = len(b.items)
        cells = [self.node("large-number", {"title": f.value, "description": f.label, "alignment": "center",
                                            "descriptionPosition": "bottom"}) for f in b.items]
        items = [{"colStart": i, "colEnd": i + 1, "rowStart": 0, "rowEnd": 1} for i in range(n)]
        return self.node("grid", {"cols": n, "rows": 1, "items": items}, None, cells)

    def image(self, url: str, caption: str = "", alt: str = "", attribution: str = "", config: dict | None = None) -> str:
        rid = self.resource("image", {"src": url, "provider": "uri"})
        data: dict = {"image": rid}
        if caption:
            data["caption"] = inline(caption)
        if alt:
            data["alt"] = alt
        if attribution:
            data["attribution"] = attribution
        return self.node("image", data, config)


# ---- slide layouts (the three templates) ---------------------------------
def _slides_sidecar(g: _Graph, slides: list[Slide], subtype: str) -> list[str]:
    slide_ids = []
    for sl in slides:
        panel = g.node("immersive-narrative-panel", {"panelStyle": "themed"},
                       children=[g.text("h3", sl.title), g.rich("paragraph", sl.body)])
        slide_ids.append(g.node("immersive-slide", {"transition": "fade"},
                                children=[panel, g.webmap(sl.map)]))  # type: ignore[arg-type]
    return [g.node("immersive", {"type": "sidecar", "subtype": subtype,
                                 "narrativePanelPosition": "start", "narrativePanelSize": "small"},
                   children=slide_ids)]


def _slides_longform(g: _Graph, slides: list[Slide]) -> list[str]:
    out: list[str] = []
    for sl in slides:
        out += [g.text("h3", sl.title), g.rich("paragraph", sl.body)]
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


def _cite_marks(spec: StorySpec, keys: list[str]) -> str:
    return "".join(f"[{spec.citation_number(k)}]" for k in keys)


def _block(g: _Graph, spec: StorySpec, b) -> list[str]:
    if isinstance(b, HeadingBlock):
        return [g.text("h3" if b.level == 3 else "h4", b.text)]
    if isinstance(b, ParagraphBlock):
        return [g.rich("paragraph", b.text + (" " + _cite_marks(spec, b.cites) if b.cites else ""))]
    if isinstance(b, BulletsBlock):
        items = "".join(f"<li>{inline(i)}</li>" for i in b.items)
        return [g.text("numbered-list" if b.ordered else "bullet-list", items)]
    if isinstance(b, QuoteBlock):
        return [g.rich("quote", f"*{b.text}*" + (f" {chr(8212)} {b.attribution}" if b.attribution else ""))]
    if isinstance(b, MapBlock):
        return [g.webmap(b.map)]
    if isinstance(b, SwipeBlock):
        return [g.swipe(b)]
    if isinstance(b, FiguresBlock):
        return [g.figures(b)]
    if isinstance(b, ImageBlock):
        return [g.image(b.url, b.caption, b.alt, b.attribution)]
    if isinstance(b, SeparatorBlock):
        return [g.node("separator")]
    if isinstance(b, SlidesBlock):
        return _layout_slides(g, spec, b.slides)
    raise TypeError(f"unknown block {type(b).__name__}")  # pragma: no cover - the schema is a closed union


def _section(g: _Graph, spec: StorySpec, sec: Section) -> tuple[str, list[str]]:
    heading = g.text("h2", sec.title)
    blocks = [heading]
    if sec.intro:
        blocks.append(g.rich("paragraph", sec.intro))
    if sec.image_url:
        rid = g.resource("image", {"src": sec.image_url, "provider": "uri"})
        blocks.append(g.node("image", {"image": rid, "caption": sec.image_caption},
                             {"size": "float", "floatAlignment": "start"}))
    if sec.cites:
        blocks.append(g.text("paragraph", f"Sources: {_cite_marks(spec, sec.cites)}", textSize="small"))
    blocks += _layout_slides(g, spec, sec.slides)
    for b in sec.blocks:
        blocks += _block(g, spec, b)
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
                   {"type": "full" if cover_children else "minimal", "title": spec.title, "summary": spec.subtitle,
                    "byline": spec.byline, "titlePanelVerticalPosition": "top", "titlePanelHorizontalPosition": "start",
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
        items = "".join(f"<li>{inline(s.text)}</li>" for s in spec.sources)
        body += [ref_head, g.text("numbered-list", items)]

    nav = g.node("navigation", {"links": [{"nodeId": n} for n in nav_targets]}, {"isHidden": False})
    credits = g.node("credits", None, {"isHidden": True},
                     [g.node("attribution", {"content": "", "attribution": ""})])

    root = g.node("story", {"storyTheme": theme_rid},
                  {"coverDate": "first-published", "isSocialIconsHidden": False,
                   "shouldHideSimilarItems": True, "storyLocale": spec.locale},
                  [cover, nav, *body, credits])
    return {"root": root, "nodes": g.nodes, "resources": g.resources}
