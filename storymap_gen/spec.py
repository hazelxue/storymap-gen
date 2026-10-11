"""Structured story spec: the only thing an LLM is asked to produce.

The spec is validated here (references, ids, citations, sizes) before anything is compiled to StoryMaps JSON, so
malformed or oversized model output fails early and cheaply. All free-text fields and lists have upper bounds: the spec
may be produced by a model that has read untrusted text, so it is treated as untrusted input.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from .claims import AreaClaim, Claim, LengthClaim, OrientationClaim, ProximityClaim, StatClaim  # noqa: F401  (re-exported)

Template = Literal["docked-sidecar", "floating-sidecar", "longform"]

KEY = r"^[\w.-]{1,64}$"
ITEM_ID = r"^[0-9a-f]{32}$"
HTTPS_URL = r"^https://[^\s]{1,2000}$"
MAX_TOTAL_TEXT = 500_000  # characters across the whole spec
WEB_MERCATOR_LIMIT = 20_037_508.35


class Source(BaseModel):
    key: str = Field(pattern=KEY, description="Stable id used in cites, e.g. 'nyc-lpc'")
    text: str = Field(max_length=1000)


class MapRef(BaseModel):
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
    title: str = Field(max_length=300)
    body: str = Field(max_length=5000)
    map: Optional[MapRef] = Field(default=None, description="Required for the sidecar layouts")
    claim_ids: list[str] = Field(default_factory=list, max_length=100)


class Section(BaseModel):
    title: str = Field(max_length=300)
    intro: str = Field(default="", max_length=10_000)
    cites: list[str] = Field(default_factory=list, max_length=50)
    image_url: Optional[str] = Field(default=None, pattern=HTTPS_URL)
    image_caption: str = Field(default="", max_length=500)
    slides: list[Slide] = Field(default_factory=list, max_length=50)
    claim_ids: list[str] = Field(default_factory=list, max_length=100)


class StorySpec(BaseModel):
    title: str = Field(max_length=300)
    subtitle: str = Field(default="", max_length=500)
    byline: str = Field(default="", max_length=200)
    locale: str = Field(default="en-us", pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")
    template: Template = "docked-sidecar"
    theme: str = Field(default="slate", pattern=r"^[a-z0-9-]{1,40}$")
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
                for sl in sec.slides:
                    if sl.map is None:
                        raise ValueError(f"slide '{sl.title}' needs a map for the '{self.template}' layout")
            for k in sec.cites:
                if k not in keys:
                    raise ValueError(f"section '{sec.title}' cites unknown source '{k}'")
            ids = list(sec.claim_ids) + [i for sl in sec.slides for i in sl.claim_ids]
            for i in ids:
                if i not in claim_ids:
                    raise ValueError(f"section '{sec.title}' references unknown claim '{i}'")
        if self._total_text() > MAX_TOTAL_TEXT:
            raise ValueError(f"the spec holds more than {MAX_TOTAL_TEXT} characters of text")
        return self

    def _total_text(self) -> int:
        n = len(self.title) + len(self.subtitle) + sum(len(s.text) for s in self.sources) + sum(len(c.text) for c in self.claims)
        for sec in self.sections:
            n += len(sec.title) + len(sec.intro) + sum(len(sl.title) + len(sl.body) for sl in sec.slides)
        return n

    def citation_number(self, key: str) -> int:
        return [s.key for s in self.sources].index(key) + 1
