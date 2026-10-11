"""Structured story spec: the only thing an LLM is asked to produce.

The spec is validated here (references, ids, citations, sizes) before anything is compiled to StoryMaps JSON, so
malformed or oversized model output fails early and cheaply. All free-text fields and lists have upper bounds: the spec
may be produced by a model that has read untrusted text, so it is treated as untrusted input.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .claims import AreaClaim, Claim, LengthClaim, OrientationClaim, ProximityClaim, StatClaim  # noqa: F401  (re-exported)

Template = Literal["docked-sidecar", "floating-sidecar", "longform"]

KEY = r"^[\w.-]{1,64}$"
ITEM_ID = r"^[0-9a-f]{32}$"
HTTPS_URL = r"^https://[^\s]{1,2000}$"
MAX_TOTAL_TEXT = 500_000  # characters across the whole spec
WEB_MERCATOR_LIMIT = 20_037_508.35


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=KEY, description="Stable id used in cites, e.g. 'nyc-lpc'")
    text: str = Field(max_length=1000)


class MapRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    """A map for a slide: an existing Web Map (``item_id``) or a Web Map generated from data layers (``layers``)."""

    item_id: Optional[str] = Field(default=None, pattern=ITEM_ID, description="Existing ArcGIS Web Map item id")
    layers: list[str] = Field(default_factory=list, max_length=12,
                              description="Names of data layers to draw in a generated Web Map (instead of item_id)")
    filters: dict[str, str] = Field(
        default_factory=dict, max_length=12,
        description="Per layer, the same safe filter a claim uses (for example {\"schools\": \"amenity == 'school'\"}), so the map shows "
                    "exactly the features the text counts")
    caption: str = Field(default="", max_length=500)
    extent: Optional[tuple[float, float, float, float]] = Field(
        default=None, description="xmin, ymin, xmax, ymax in Web Mercator (EPSG:3857)")

    @model_validator(mode="after")
    def _one_source(self) -> MapRef:
        if (self.item_id is None) == (not self.layers):
            raise ValueError("a map needs exactly one of item_id or layers")
        if self.filters and self.item_id is not None:
            raise ValueError("filters only apply to maps generated from layers")
        for name, expr in self.filters.items():
            if name not in self.layers:
                raise ValueError(f"filter for '{name}', which is not one of the map's layers")
            if len(expr) > 300:
                raise ValueError("a map filter is longer than 300 characters")
        if self.extent is not None:
            xmin, ymin, xmax, ymax = self.extent
            if not all(math.isfinite(v) and abs(v) <= WEB_MERCATOR_LIMIT for v in self.extent) or xmin >= xmax or ymin >= ymax:
                raise ValueError("extent must be finite Web Mercator coordinates with xmin < xmax and ymin < ymax")
        return self

    @property
    def key(self) -> str:
        """Stable identifier of this map inside a story (the item id, or a name derived from the layers)."""
        if self.item_id:
            return self.item_id
        base = "gen-" + "-".join(self.layers)
        if self.filters:
            digest = hashlib.sha1(json.dumps(self.filters, sort_keys=True).encode(), usedforsecurity=False).hexdigest()[:8]
            return f"{base}-f{digest}"
        return base


class Slide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=300)
    body: str = Field(max_length=5000)
    map: Optional[MapRef] = Field(default=None, description="Required for the sidecar layouts")
    claim_ids: list[str] = Field(default_factory=list, max_length=100)


# ---- content blocks: what a section is made of -----------------------------------------------------------------
# Text may use **bold**, *italic* and [label](https://link). Numbers in text must be backed by a claim (see grounding.py).
class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_ids: list[str] = Field(default_factory=list, max_length=100, description="Claims that back the numbers in this block")


class HeadingBlock(_Block):
    type: Literal["heading"] = "heading"
    level: Literal[3, 4] = 3
    text: str = Field(max_length=300)


class ParagraphBlock(_Block):
    type: Literal["paragraph"] = "paragraph"
    text: str = Field(max_length=5000)
    cites: list[str] = Field(default_factory=list, max_length=10, description="Source keys; shown as [n] after the paragraph")


class BulletsBlock(_Block):
    type: Literal["bullets"] = "bullets"
    items: list[str] = Field(min_length=1, max_length=20)
    ordered: bool = False

    @model_validator(mode="after")
    def _item_length(self) -> BulletsBlock:
        if any(len(i) > 800 for i in self.items):
            raise ValueError("a list item is longer than 800 characters")
        return self


class QuoteBlock(_Block):
    type: Literal["quote"] = "quote"
    text: str = Field(max_length=1000)
    attribution: str = Field(default="", max_length=200)


class MapBlock(_Block):
    type: Literal["map"] = "map"
    map: MapRef


class SwipeBlock(_Block):
    """Two sides of one generated map with a slider between: ``left`` layers show only on the left, ``right`` layers only on
    the right, and every other layer of the map (a street base, say) shows on both sides."""

    type: Literal["swipe"] = "swipe"
    map: MapRef
    left: list[str] = Field(min_length=1, max_length=12)
    right: list[str] = Field(min_length=1, max_length=12)
    left_label: str = Field(default="", max_length=120)
    right_label: str = Field(default="", max_length=120)
    caption: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _sides(self) -> SwipeBlock:
        if self.map.item_id is not None:
            raise ValueError("a swipe needs a map generated from layers")
        for name in [*self.left, *self.right]:
            if name not in self.map.layers:
                raise ValueError(f"swipe side uses '{name}', which is not one of the map's layers")
        if set(self.left) & set(self.right):
            raise ValueError("a layer cannot be on both sides of a swipe")
        return self


class Figure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(max_length=40, description="The number as written, for example '91.9 km'")
    label: str = Field(max_length=160)
    claim_id: Optional[str] = Field(default=None, pattern=KEY, description="The claim that backs the number (optional)")


class FiguresBlock(_Block):
    """Two to four big numbers side by side. Each value is checked against the claims of this block."""

    type: Literal["figures"] = "figures"
    items: list[Figure] = Field(min_length=1, max_length=4)


class ImageBlock(_Block):
    type: Literal["image"] = "image"
    url: str = Field(pattern=HTTPS_URL)
    alt: str = Field(min_length=3, max_length=300, description="What the picture shows, for readers who cannot see it")
    caption: str = Field(default="", max_length=500)
    attribution: str = Field(default="", max_length=300, description="Credit and licence of the picture")


class SeparatorBlock(_Block):
    type: Literal["separator"] = "separator"


class SlidesBlock(_Block):
    """Slides laid out as the story's template says (a map beside each slide in the sidecar layouts)."""

    type: Literal["slides"] = "slides"
    slides: list[Slide] = Field(min_length=1, max_length=50)


Block = Annotated[Union[HeadingBlock, ParagraphBlock, BulletsBlock, QuoteBlock, MapBlock, SwipeBlock, FiguresBlock, ImageBlock,
                        SeparatorBlock, SlidesBlock], Field(discriminator="type")]


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=300)
    intro: str = Field(default="", max_length=10_000)
    cites: list[str] = Field(default_factory=list, max_length=50)
    image_url: Optional[str] = Field(default=None, pattern=HTTPS_URL)
    image_caption: str = Field(default="", max_length=500)
    slides: list[Slide] = Field(default_factory=list, max_length=50)
    blocks: list[Block] = Field(default_factory=list, max_length=300,
                                description="Subsection headings, paragraphs, lists, maps, swipes, key figures, images; in reading order")
    claim_ids: list[str] = Field(default_factory=list, max_length=100)

    def all_slides(self) -> list[Slide]:
        return [*self.slides, *(sl for b in self.blocks if isinstance(b, SlidesBlock) for sl in b.slides)]


@dataclass(frozen=True)
class Prose:
    """One piece of text in a story, with where it is and which claims back its numbers."""

    where: str
    text: str
    claim_ids: tuple[str, ...]
    kind: str  # intro | slide | paragraph | bullet | quote | figure | heading | caption


_LINK = re.compile(r"\[([^\]]{1,300})\]\(https://[^\s)]{1,2000}\)")
_CITE_MARK = re.compile(r"\[\d{1,3}\]")


def plain(text: str) -> str:
    """Text without markup: link labels only, no emphasis marks, no [n] citation markers (what a reader reads)."""
    return _CITE_MARK.sub("", _LINK.sub(r"\1", text)).replace("**", "").replace("*", "")


class StorySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=300)
    subtitle: str = Field(default="", max_length=500)
    byline: str = Field(default="", max_length=200)
    locale: str = Field(default="en-us", pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")
    template: Template = "docked-sidecar"
    theme: str = Field(default="slate", pattern=r"^[a-z0-9-]{1,40}$")
    depth: Literal["brief", "standard", "full"] = Field(
        default="standard", description="How full the story should be; the depth report warns where it falls short (see docs/writing-guide.md)")
    cover_image_url: Optional[str] = Field(default=None, pattern=HTTPS_URL)
    sections: list[Section] = Field(max_length=50)
    sources: list[Source] = Field(default_factory=list, max_length=200)
    claims: list[Claim] = Field(default_factory=list, max_length=500)
    allow_numbers: list[float] = Field(
        default_factory=list, max_length=200,
        description="Numbers that may appear in prose without a claim (e.g. a quoted figure from a source)")

    @model_validator(mode="after")
    def _check_references(self) -> StorySpec:
        keys = [s.key for s in self.sources]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate source keys")
        claim_ids = [c.id for c in self.claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("duplicate claim ids")
        if not all(math.isfinite(v) for v in self.allow_numbers):
            raise ValueError("allow_numbers must be finite numbers")
        for sec in self.sections:
            if self.template != "longform":
                for sl in sec.all_slides():
                    if sl.map is None:
                        raise ValueError(f"slide '{sl.title}' needs a map for the '{self.template}' layout")
            cited = list(sec.cites) + [k for b in sec.blocks if isinstance(b, ParagraphBlock) for k in b.cites]
            for k in cited:
                if k not in keys:
                    raise ValueError(f"section '{sec.title}' cites unknown source '{k}'")
            ids = list(sec.claim_ids) + [i for sl in sec.all_slides() for i in sl.claim_ids]
            for b in sec.blocks:
                ids += b.claim_ids
                if isinstance(b, FiguresBlock):
                    ids += [f.claim_id for f in b.items if f.claim_id]
            for i in ids:
                if i not in claim_ids:
                    raise ValueError(f"section '{sec.title}' references unknown claim '{i}'")
        if self._total_text() > MAX_TOTAL_TEXT:
            raise ValueError(f"the spec holds more than {MAX_TOTAL_TEXT} characters of text")
        return self

    def _total_text(self) -> int:
        return (len(self.title) + len(self.subtitle) + sum(len(s.text) for s in self.sources) + sum(len(c.text) for c in self.claims)
                + sum(len(p.text) for p in self.prose()))

    def citation_number(self, key: str) -> int:
        return [s.key for s in self.sources].index(key) + 1

    def cited_keys(self) -> set[str]:
        return {k for s in self.sections for k in [*s.cites, *(k for b in s.blocks if isinstance(b, ParagraphBlock) for k in b.cites)]}

    def prose(self) -> list[Prose]:
        """Every piece of reader-facing text, in reading order, with the claims that may back its numbers."""
        out: list[Prose] = []
        for sec in self.sections:
            base = tuple(sec.claim_ids)
            where = f"section '{sec.title}'"
            out.append(Prose(where, sec.title, (), "heading"))
            if sec.intro:
                out.append(Prose(f"{where} intro", sec.intro, base, "intro"))
            if sec.image_caption:
                out.append(Prose(f"{where} image", sec.image_caption, base, "caption"))
            for sl in sec.slides:
                out += _slide_prose(sl, base)
            for b in sec.blocks:
                ids = base + tuple(b.claim_ids)
                if isinstance(b, HeadingBlock):
                    out.append(Prose(f"{where} heading '{b.text[:40]}'", b.text, (), "heading"))
                elif isinstance(b, ParagraphBlock):
                    out.append(Prose(f"{where} paragraph '{b.text[:40]}...'", b.text, ids, "paragraph"))
                elif isinstance(b, BulletsBlock):
                    out += [Prose(f"{where} list item '{i[:40]}...'", i, ids, "bullet") for i in b.items]
                elif isinstance(b, QuoteBlock):
                    out.append(Prose(f"{where} quote", b.text, ids, "quote"))
                elif isinstance(b, MapBlock):
                    out.append(Prose(f"{where} map caption", b.map.caption, ids, "caption"))
                elif isinstance(b, SwipeBlock):
                    for label, text in (("caption", b.caption), ("left label", b.left_label), ("right label", b.right_label)):
                        out.append(Prose(f"{where} swipe {label}", text, ids, "caption"))
                elif isinstance(b, FiguresBlock):
                    for f in b.items:
                        fids = ids + ((f.claim_id,) if f.claim_id else ())
                        out.append(Prose(f"{where} figure '{f.label[:30]}'", f.value, fids, "figure"))
                        out.append(Prose(f"{where} figure label '{f.label[:30]}'", f.label, fids, "caption"))
                elif isinstance(b, ImageBlock):
                    out += [Prose(f"{where} image caption", b.caption, ids, "caption"), Prose(f"{where} image alt text", b.alt, ids, "caption")]
                elif isinstance(b, SlidesBlock):
                    for sl in b.slides:
                        out += _slide_prose(sl, ids)
        return [p for p in out if p.text]


def _slide_prose(sl: Slide, base: tuple[str, ...]) -> list[Prose]:
    ids = base + tuple(sl.claim_ids)
    out = [Prose(f"slide '{sl.title}'", sl.body, ids, "slide"), Prose(f"slide '{sl.title}' title", sl.title, (), "heading")]
    if sl.map is not None and sl.map.caption:
        out.append(Prose(f"slide '{sl.title}' map caption", sl.map.caption, ids, "caption"))
    return out
