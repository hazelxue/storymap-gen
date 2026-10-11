"""Structured story spec: the only thing an LLM is asked to produce.

The spec is validated here (references, ids, citations) before anything is
compiled to StoryMaps JSON, so malformed LLM output fails early and cheaply.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from .claims import AreaClaim, Claim, OrientationClaim, ProximityClaim, StatClaim  # noqa: F401  (re-exported)

Template = Literal["docked-sidecar", "floating-sidecar", "longform"]


class Source(BaseModel):
    key: str = Field(description="Stable id used in cites, e.g. 'nyc-lpc'")
    text: str


class MapRef(BaseModel):
    item_id: str = Field(description="ArcGIS Online Web Map item id")
    caption: str = ""
    extent: Optional[tuple[float, float, float, float]] = Field(
        default=None, description="xmin, ymin, xmax, ymax in Web Mercator (EPSG:3857)"
    )


class Slide(BaseModel):
    title: str
    body: str
    map: Optional[MapRef] = Field(default=None, description="Required for the sidecar layouts")
    claim_ids: list[str] = []


class Section(BaseModel):
    title: str
    intro: str = ""
    cites: list[str] = []
    image_url: Optional[str] = None
    image_caption: str = ""
    slides: list[Slide] = []
    claim_ids: list[str] = []


class StorySpec(BaseModel):
    title: str
    subtitle: str = ""
    byline: str = ""
    locale: str = "en-us"
    template: Template = "docked-sidecar"
    theme: str = "slate"
    cover_image_url: Optional[str] = None
    sections: list[Section]
    sources: list[Source] = []
    claims: list[Claim] = []
    allow_numbers: list[float] = Field(
        default_factory=list,
        description="Numbers that may appear in prose without a claim (e.g. a quoted figure from a source)")

    @model_validator(mode="after")
    def _check_references(self) -> StorySpec:
        keys = [s.key for s in self.sources]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate source keys")
        claim_ids = [c.id for c in self.claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("duplicate claim ids")
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
        return self

    def citation_number(self, key: str) -> int:
        return [s.key for s in self.sources].index(key) + 1
