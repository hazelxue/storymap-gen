"""Claim types. Each claim knows how to check itself against a DataStore.

To add a claim type: subclass ``_Claim``, give it a unique ``kind`` literal, implement ``check`` (and
``stated_values`` if the claim backs numbers written in prose), then add the class to the ``Claim`` union
at the bottom of this file. See CONTRIBUTING.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field

from .geo import (
    ACRE_M2,
    GRID_NAME_PATTERN,
    SQFT_M2,
    DataStore,
    deviation_from_grid,
    grid_axis,
    project,
    segments,
    weighted_median,
)


@dataclass
class ClaimResult:
    id: str
    status: str  # PASS | FAIL | ERROR
    detail: str
    measured: Optional[float] = None


class _Claim(BaseModel):
    id: str
    text: str = Field(description="The sentence (or idea) in the story that this claim backs")

    def check(self, store: DataStore) -> ClaimResult:  # pragma: no cover - interface
        raise NotImplementedError

    def stated_values(self) -> list[float]:
        """Numbers this claim authorises to appear in prose (see grounding.py)."""
        return []


class OrientationClaim(_Claim):
    """'Street X is diagonal / parallel to the neighbourhood grid.'"""

    kind: Literal["street_orientation"] = "street_orientation"
    layer: str = "streets_centerline"
    name_field: str = "full_stree"
    street: str
    relation: Literal["diagonal_to_grid", "parallel_to_grid"]
    tolerance_deg: float = 15.0
    grid_regex: Optional[str] = Field(
        default=GRID_NAME_PATTERN,
        description="Street names that define the grid (default: NYC numbered streets/avenues). "
                    "null = use every segment in the layer, for cities without numbered grids.")

    def check(self, store: DataStore) -> ClaimResult:
        gdf = store.get(self.layer)
        names = gdf[self.name_field].fillna("").str.upper().str.strip()
        grid = gdf if self.grid_regex is None else gdf[names.str.match(self.grid_regex)]
        target = gdf[names == self.street.upper().strip()]
        if grid.empty:
            return ClaimResult(self.id, "ERROR", "no streets matched the grid definition (check grid_regex)")
        if target.empty:
            return ClaimResult(self.id, "ERROR", f"street '{self.street}' not found in layer '{self.layer}'")
        gl, gb = segments(grid)
        axis = grid_axis(gl, gb)
        tl, tb = segments(target)
        dev = weighted_median(deviation_from_grid(tb, axis), tl)
        is_diag = dev > self.tolerance_deg
        ok = is_diag if self.relation == "diagonal_to_grid" else not is_diag
        detail = (f"{self.street}: median deviation from grid axis = {dev:.1f} deg "
                  f"(grid axis {axis:.1f} deg, tolerance {self.tolerance_deg} deg) -> "
                  f"{'diagonal' if is_diag else 'parallel'}; claim says {self.relation}")
        return ClaimResult(self.id, "PASS" if ok else "FAIL", detail, round(dev, 2))

    def stated_values(self) -> list[float]:
        return [self.tolerance_deg]


class StatClaim(_Claim):
    """'Layer L has <agg> of field F equal to N (+/- tol).'"""

    kind: Literal["layer_stat"] = "layer_stat"
    layer: str
    field: Optional[str] = None
    agg: Literal["count", "min", "max", "mean", "sum"]
    expected: float
    tolerance: float = 0.0
    where: Optional[str] = Field(default=None, description="pandas query string")

    def check(self, store: DataStore) -> ClaimResult:
        gdf = store.get(self.layer)
        if self.where:
            gdf = gdf.query(self.where, engine="python")
        if self.agg == "count":
            measured = float(len(gdf))
        else:
            if self.field is None or self.field not in gdf:
                return ClaimResult(self.id, "ERROR", f"field '{self.field}' missing in '{self.layer}'")
            measured = float(getattr(gdf[self.field], self.agg)())
        ok = abs(measured - self.expected) <= self.tolerance
        return ClaimResult(self.id, "PASS" if ok else "FAIL",
                           f"{self.agg}({self.field or '*'}) = {measured:.4g}, claimed {self.expected} +/- {self.tolerance}",
                           round(measured, 4))

    def stated_values(self) -> list[float]:
        return [self.expected]


class AreaClaim(_Claim):
    """'The polygons of layer L (optionally filtered) cover N acres / sq ft / sq m.'"""

    kind: Literal["layer_area"] = "layer_area"
    layer: str
    where: Optional[str] = Field(default=None, description="pandas query string, e.g. \"name.notna()\"")
    unit: Literal["acres", "sq_ft", "sq_m"] = "acres"
    expected: float
    tolerance: float = 0.0

    def check(self, store: DataStore) -> ClaimResult:
        gdf = store.get(self.layer)
        if self.where:
            gdf = gdf.query(self.where, engine="python")
        if gdf.empty:
            return ClaimResult(self.id, "ERROR", "no features matched the filter")
        sq_m = float(project(gdf).area.sum())
        measured = sq_m / {"acres": ACRE_M2, "sq_ft": SQFT_M2, "sq_m": 1.0}[self.unit]
        ok = abs(measured - self.expected) <= self.tolerance
        return ClaimResult(self.id, "PASS" if ok else "FAIL",
                           f"area = {measured:.2f} {self.unit} over {len(gdf)} features, claimed {self.expected} +/- {self.tolerance}",
                           round(measured, 4))

    def stated_values(self) -> list[float]:
        return [self.expected]


class ProximityClaim(_Claim):
    """'<share> of the features in layer A lie within D metres of features in layer B.'"""

    kind: Literal["within_distance"] = "within_distance"
    layer: str
    target_layer: str
    target_name_field: Optional[str] = None
    target_names: list[str] = Field(default_factory=list, description="restrict the target layer to these names")
    max_distance_m: float
    expected_share: float = Field(ge=0, le=1)
    tolerance: float = 0.0

    def check(self, store: DataStore) -> ClaimResult:
        src = store.get(self.layer)
        crs = src.estimate_utm_crs()
        a = src.to_crs(crs)
        b = store.get(self.target_layer)
        if self.target_names:
            if self.target_name_field is None:
                return ClaimResult(self.id, "ERROR", "target_names needs target_name_field")
            wanted = {n.upper().strip() for n in self.target_names}
            b = b[b[self.target_name_field].fillna("").str.upper().str.strip().isin(wanted)]
        if a.empty or b.empty:
            return ClaimResult(self.id, "ERROR", "layer or target is empty after filtering")
        target = b.to_crs(crs).geometry.union_all()
        near = a.geometry.distance(target) <= self.max_distance_m
        share = float(near.mean())
        ok = abs(share - self.expected_share) <= self.tolerance
        return ClaimResult(self.id, "PASS" if ok else "FAIL",
                           f"{int(near.sum())}/{len(a)} features within {self.max_distance_m:g} m = {share:.2f}, "
                           f"claimed {self.expected_share} +/- {self.tolerance}", round(share, 4))

    def stated_values(self) -> list[float]:
        return [self.expected_share, round(self.expected_share * 100, 6), self.max_distance_m]


Claim = Annotated[Union[OrientationClaim, StatClaim, AreaClaim, ProximityClaim], Field(discriminator="kind")]

_DECIMALS = re.compile(r"\.(\d+)")


def backs(claim: _Claim, number: float, text: str) -> bool:
    """True if this claim authorises ``number`` (written as ``text`` in prose)."""
    m = _DECIMALS.search(text)
    rounding = 0.5 * 10 ** -(len(m.group(1)) if m else 0)
    return any(abs(number - v) <= max(getattr(claim, "tolerance", 0.0), rounding) for v in claim.stated_values())
