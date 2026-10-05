"""Structured story spec: the only thing an LLM is asked to produce.

The spec is validated here (references, ids, citations) before anything is
compiled to StoryMaps JSON, so malformed LLM output fails early and cheaply.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

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
    map: MapRef
    claim_ids: list[str] = []


class Section(BaseModel):
    title: str
    intro: str = ""
    cites: list[str] = []
    image_url: Optional[str] = None
    image_caption: str = ""
    slides: list[Slide] = []
    claim_ids: list[str] = []


class OrientationClaim(BaseModel):
    """'Street X is diagonal / parallel to the neighbourhood grid.'"""

    kind: Literal["street_orientation"] = "street_orientation"
    id: str
    text: str
    layer: str = "streets_centerline"
    name_field: str = "full_stree"
    street: str
    relation: Literal["diagonal_to_grid", "parallel_to_grid"]
    tolerance_deg: float = 15.0


class StatClaim(BaseModel):
    """'Layer L has <agg> of field F equal to N (+/- tol).'"""

    kind: Literal["layer_stat"] = "layer_stat"
    id: str
    text: str
    layer: str
    field: Optional[str] = None
    agg: Literal["count", "min", "max", "mean", "sum"]
    expected: float
    tolerance: float = 0.0
    where: Optional[str] = Field(default=None, description="pandas query string")


Claim = Annotated[Union[OrientationClaim, StatClaim], Field(discriminator="kind")]


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

    @model_validator(mode="after")
    def _check_references(self) -> "StorySpec":
        keys = [s.key for s in self.sources]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate source keys")
        claim_ids = [c.id for c in self.claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("duplicate claim ids")
        for sec in self.sections:
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
