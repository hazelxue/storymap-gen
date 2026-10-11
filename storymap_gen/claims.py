"""Claim types. Each claim knows how to check itself against a DataStore.

To add a claim type: subclass ``_Claim``, give it a unique ``kind`` literal, implement ``check`` (and
``stated_values`` if the claim backs numbers written in prose), then add the class to the ``Claim`` union
at the bottom of this file. See CONTRIBUTING.md.
"""
from __future__ import annotations

import difflib
import re
import warnings
from dataclasses import dataclass
from typing import Annotated, Literal, Optional, Union

import geopandas as gpd  # noqa: F401  (type annotations)
import pandas as pd  # noqa: F401  (type annotations)
from pydantic import BaseModel, Field, field_validator, model_validator

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
from .safe_expr import safe_filter

ID = r"^[\w.-]{1,64}$"


@dataclass
class ClaimResult:
    id: str
    status: str  # PASS | FAIL | ERROR
    detail: str
    measured: Optional[float] = None
    hint: str = ""  # what to change when the claim does not pass


class _Claim(BaseModel):
    id: str = Field(pattern=ID)
    text: str = Field(max_length=1000, description="The sentence (or idea) in the story that this claim backs")

    def check(self, store: DataStore) -> ClaimResult:  # pragma: no cover - interface
        raise NotImplementedError

    def stated_values(self) -> list[float]:
        """Numbers this claim authorises to appear in prose (see grounding.py)."""
        return []


class OrientationClaim(_Claim):
    """'Street X is diagonal / parallel to the neighbourhood grid.'"""

    kind: Literal["street_orientation"] = "street_orientation"
    layer: str = Field(default="streets_centerline", max_length=200)
    name_field: str = Field(default="full_stree", max_length=200)
    street: str = Field(max_length=200)
    relation: Literal["diagonal_to_grid", "parallel_to_grid"]
    tolerance_deg: float = Field(default=15.0, ge=0, le=45, allow_inf_nan=False)
    stated_deviation_deg: Optional[float] = Field(
        default=None, ge=0, le=45, allow_inf_nan=False, description="If the prose quotes the measured deviation (e.g. '37 degrees'), put it here to have it verified")
    deviation_tolerance_deg: float = Field(default=1.0, ge=0, le=45, allow_inf_nan=False)
    grid_regex: Optional[str] = Field(
        default=GRID_NAME_PATTERN,
        description="Street names that define the grid (default: NYC numbered streets/avenues). "
                    "null = use every segment in the layer, for cities without numbered grids.")

    @field_validator("grid_regex")
    @classmethod
    def _safe_regex(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else validate_regex(v)

    def check(self, store: DataStore) -> ClaimResult:
        gdf = store.get(self.layer)
        names = gdf[self.name_field].fillna("").str.upper().str.strip()
        grid = gdf if self.grid_regex is None else gdf[names.str.match(self.grid_regex)]
        target = gdf[names == self.street.upper().strip()]
        if grid.empty:
            return ClaimResult(self.id, "ERROR", "no streets matched the grid definition (check grid_regex)")
        if target.empty:
            close = difflib.get_close_matches(self.street.upper().strip(), sorted(set(names)), n=3, cutoff=0.6)
            hint = f"did you mean: {', '.join(close)}?" if close else "check the spelling against the layer's name field"
            return ClaimResult(self.id, "ERROR", f"street '{self.street}' not found in layer '{self.layer}'", hint=hint)
        gl, gb = segments(grid)
        axis = grid_axis(gl, gb)
        tl, tb = segments(target)
        dev = weighted_median(deviation_from_grid(tb, axis), tl)
        is_diag = dev > self.tolerance_deg
        ok = is_diag if self.relation == "diagonal_to_grid" else not is_diag
        if self.stated_deviation_deg is not None and abs(dev - self.stated_deviation_deg) > self.deviation_tolerance_deg:
            return ClaimResult(self.id, "FAIL", f"{self.street}: measured deviation {dev:.1f} deg but the story states "
                               f"{self.stated_deviation_deg} deg (+/- {self.deviation_tolerance_deg})", round(dev, 2),
                               f"change stated_deviation_deg to {dev:.1f} and fix the sentence")
        detail = (f"{self.street}: median deviation from grid axis = {dev:.1f} deg "
                  f"(grid axis {axis:.1f} deg, tolerance {self.tolerance_deg} deg) -> "
                  f"{'diagonal' if is_diag else 'parallel'}; claim says {self.relation}")
        truth = "diagonal_to_grid" if is_diag else "parallel_to_grid"
        hint = "" if ok else (f"{self.street} measures as {truth.split('_')[0]}: set relation to '{truth}', "
                              f"or choose a street that really is {self.relation.split('_')[0]}")
        return ClaimResult(self.id, "PASS" if ok else "FAIL", detail, round(dev, 2), hint)

    def stated_values(self) -> list[float]:
        return [self.tolerance_deg] + ([self.stated_deviation_deg] if self.stated_deviation_deg is not None else [])


class StatClaim(_Claim):
    """'Layer L has <agg> of field F equal to N (+/- tol).'"""

    kind: Literal["layer_stat"] = "layer_stat"
    layer: str = Field(max_length=200)
    field: Optional[str] = Field(default=None, max_length=200)
    agg: Literal["count", "nunique", "min", "max", "mean", "sum"]
    expected: float = Field(allow_inf_nan=False)
    tolerance: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    where: Optional[str] = Field(default=None, max_length=300, description="safe filter, e.g. \"name.notna() and type == 'park'\"")

    def check(self, store: DataStore) -> ClaimResult:
        gdf = store.get(self.layer)
        if self.where:
            gdf = safe_filter(gdf, self.where)
        if self.agg == "count":
            measured = float(len(gdf))
        else:
            if self.field is None or self.field not in gdf:
                return ClaimResult(self.id, "ERROR", f"field '{self.field}' missing in '{self.layer}'")
            measured = float(gdf[self.field].nunique() if self.agg == "nunique" else getattr(gdf[self.field], self.agg)())
        ok = abs(measured - self.expected) <= self.tolerance
        return ClaimResult(self.id, "PASS" if ok else "FAIL",
                           f"{self.agg}({self.field or '*'}) = {measured:.4g}, claimed {self.expected} +/- {self.tolerance}",
                           round(measured, 4), "" if ok else f"the data says {measured:.4g}: update expected and the sentence")

    def stated_values(self) -> list[float]:
        return [self.expected]


class AreaClaim(_Claim):
    """'The polygons of layer L (optionally filtered) cover N acres / sq ft / sq m.'"""

    kind: Literal["layer_area"] = "layer_area"
    layer: str = Field(max_length=200)
    where: Optional[str] = Field(default=None, max_length=300, description="safe filter, e.g. \"name.notna()\"")
    unit: Literal["acres", "sq_ft", "sq_m", "sq_km", "ha"] = "acres"
    expected: float = Field(allow_inf_nan=False)
    tolerance: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    dissolve: bool = Field(default=True, description="merge overlapping polygons first, so overlap is not counted twice")

    def check(self, store: DataStore) -> ClaimResult:
        gdf = store.get(self.layer)
        if self.where:
            gdf = safe_filter(gdf, self.where)
        if gdf.empty:
            return ClaimResult(self.id, "ERROR", "no features matched the filter")
        polys = project(gdf).geometry
        if not polys.geom_type.isin(["Polygon", "MultiPolygon"]).any():
            return ClaimResult(self.id, "ERROR", f"layer '{self.layer}' has no polygons")
        valid = polys.make_valid()
        sq_m = float(valid.union_all().area) if self.dissolve else float(valid.area.sum())
        measured = sq_m / {"acres": ACRE_M2, "sq_ft": SQFT_M2, "sq_m": 1.0, "sq_km": 1_000_000.0, "ha": 10_000.0}[self.unit]
        ok = abs(measured - self.expected) <= self.tolerance
        return ClaimResult(self.id, "PASS" if ok else "FAIL",
                           f"area = {measured:.2f} {self.unit} over {len(gdf)} features, claimed {self.expected} +/- {self.tolerance}",
                           round(measured, 4), "" if ok else f"the data says {measured:.2f} {self.unit}: update expected and the sentence")

    def stated_values(self) -> list[float]:
        return [self.expected]


class LengthClaim(_Claim):
    """'The lines of layer L (optionally filtered) are N km / miles / metres long in total.'"""

    kind: Literal["layer_length"] = "layer_length"
    layer: str = Field(max_length=200)
    where: Optional[str] = Field(default=None, max_length=300, description="safe filter, e.g. \"highway == 'cycleway'\"")
    unit: Literal["km", "mi", "m"] = "km"
    expected: float = Field(allow_inf_nan=False)
    tolerance: float = Field(default=0.0, ge=0, allow_inf_nan=False)

    def check(self, store: DataStore) -> ClaimResult:
        gdf = store.get(self.layer)
        if self.where:
            gdf = safe_filter(gdf, self.where)
        if gdf.empty:
            return ClaimResult(self.id, "ERROR", "no features matched the filter")
        lines = project(gdf).geometry
        if not lines.geom_type.isin(["LineString", "MultiLineString"]).any():
            return ClaimResult(self.id, "ERROR", f"layer '{self.layer}' has no lines")
        metres = float(lines[lines.geom_type.isin(["LineString", "MultiLineString"])].length.sum())
        measured = metres / {"km": 1000.0, "mi": 1609.344, "m": 1.0}[self.unit]
        ok = abs(measured - self.expected) <= self.tolerance
        return ClaimResult(self.id, "PASS" if ok else "FAIL",
                           f"length = {measured:.2f} {self.unit} over {len(gdf)} features, claimed {self.expected} +/- {self.tolerance}",
                           round(measured, 4), "" if ok else f"the data says {measured:.2f} {self.unit}: update expected and the sentence")

    def stated_values(self) -> list[float]:
        return [self.expected]


class ProximityClaim(_Claim):
    """'<share> of the features in layer A lie within D metres of features in layer B.'"""

    kind: Literal["within_distance"] = "within_distance"
    layer: str = Field(max_length=200)
    target_layer: str = Field(max_length=200)
    target_name_field: Optional[str] = Field(default=None, max_length=200)
    target_names: list[str] = Field(default_factory=list, max_length=50, description="restrict the target layer to these names")
    max_distance_m: float = Field(gt=0, le=100_000, allow_inf_nan=False)
    expected_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    tolerance: float = Field(default=0.0, ge=0, allow_inf_nan=False)

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
                           f"claimed {self.expected_share} +/- {self.tolerance}", round(share, 4),
                           "" if ok else f"the data says {share:.2f}: update expected_share, the distance, or the sentence")

    def stated_values(self) -> list[float]:
        return [self.expected_share, round(self.expected_share * 100, 6), self.max_distance_m]


class DensityClaim(_Claim):
    """'The study area has N features (or N km of lines) per square kilometre.'"""

    kind: Literal["layer_density"] = "layer_density"
    layer: str = Field(max_length=200)
    where: Optional[str] = Field(default=None, max_length=300, description="safe filter, e.g. \"railway == 'station'\"")
    measure: Literal["count", "length_km"] = "count"
    expected: float = Field(allow_inf_nan=False, description="features (or km of line) per km2 of the study area")
    tolerance: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    boundary_layer: str = Field(default="boundary", max_length=200, description="layer holding the study-area polygon")

    def check(self, store: DataStore) -> ClaimResult:
        area_km2, err = _study_area(store, self.boundary_layer)
        if err:
            return ClaimResult(self.id, "ERROR", err)
        gdf = store.get(self.layer)
        if self.where:
            gdf = safe_filter(gdf, self.where)
        if gdf.empty:
            return ClaimResult(self.id, "ERROR", "no features matched the filter")
        if self.measure == "count":
            total = float(len(gdf))
        else:
            lines = project(gdf).geometry
            if not lines.geom_type.isin(["LineString", "MultiLineString"]).any():
                return ClaimResult(self.id, "ERROR", f"layer '{self.layer}' has no lines")
            total = float(lines[lines.geom_type.isin(["LineString", "MultiLineString"])].length.sum()) / 1000.0
        measured = total / area_km2
        ok = abs(measured - self.expected) <= self.tolerance
        unit = "per km2" if self.measure == "count" else "km per km2"
        return ClaimResult(self.id, "PASS" if ok else "FAIL",
                           f"{total:.4g} {'features' if self.measure == 'count' else 'km'} over {area_km2:.2f} km2 = {measured:.3f} {unit}, "
                           f"claimed {self.expected} +/- {self.tolerance}", round(measured, 4),
                           "" if ok else f"the data says {measured:.3f} {unit}: update expected and the sentence")

    def stated_values(self) -> list[float]:
        return [self.expected]


class CoverageClaim(_Claim):
    """'<share> of the study area lies within D metres of the features in layer L' (D = 0 for polygons: the share they cover)."""

    kind: Literal["area_coverage"] = "area_coverage"
    layer: str = Field(max_length=200)
    where: Optional[str] = Field(default=None, max_length=300)
    max_distance_m: float = Field(default=0.0, ge=0, le=20_000, allow_inf_nan=False)
    expected_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    tolerance: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    boundary_layer: str = Field(default="boundary", max_length=200)

    def check(self, store: DataStore) -> ClaimResult:
        area_km2, err = _study_area(store, self.boundary_layer)
        if err:
            return ClaimResult(self.id, "ERROR", err)
        gdf = store.get(self.layer)
        if self.where:
            gdf = safe_filter(gdf, self.where)
        if gdf.empty:
            return ClaimResult(self.id, "ERROR", "no features matched the filter")
        crs = store.get(self.boundary_layer).estimate_utm_crs()
        study = store.get(self.boundary_layer).to_crs(crs).geometry.make_valid().union_all()
        geoms = gdf.to_crs(crs).geometry.make_valid()
        if self.max_distance_m == 0 and not geoms.geom_type.isin(["Polygon", "MultiPolygon"]).any():
            return ClaimResult(self.id, "ERROR", f"layer '{self.layer}' has no polygons; give max_distance_m to measure reach from points or lines")
        reach = geoms.buffer(self.max_distance_m).union_all() if self.max_distance_m else geoms.union_all()
        share = float(reach.intersection(study).area / study.area)
        ok = abs(share - self.expected_share) <= self.tolerance
        what = f"within {self.max_distance_m:g} m of '{self.layer}'" if self.max_distance_m else f"covered by '{self.layer}'"
        return ClaimResult(self.id, "PASS" if ok else "FAIL",
                           f"{share:.3f} of the study area ({area_km2:.2f} km2) is {what}, claimed {self.expected_share} +/- {self.tolerance}",
                           round(share, 4), "" if ok else f"the data says {share:.3f}: update expected_share, the distance, or the sentence")

    def stated_values(self) -> list[float]:
        return [self.expected_share, round(self.expected_share * 100, 6)] + ([self.max_distance_m] if self.max_distance_m else [])


def _study_area(store: DataStore, name: str) -> tuple[float, str]:
    """(area in km2, error message) of the study-area layer."""
    if name not in store.layers:
        return 0.0, f"study-area layer '{name}' is not loaded (fetch writes it as 'boundary' when the place has a boundary)"
    gdf = store.get(name)
    if gdf.empty or not gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).any():
        return 0.0, f"layer '{name}' holds no polygon"
    area = float(project(gdf).geometry.make_valid().union_all().area) / 1e6
    return (area, "") if area > 0 else (0.0, f"layer '{name}' has no area")


class ShareClaim(_Claim):
    """'<share> of the features (or length, or area) of layer L match a condition', e.g. the share of street length that is living streets,
    or the share of bus stops that lie in the northern half of the study area."""

    kind: Literal["layer_share"] = "layer_share"
    layer: str = Field(max_length=200)
    where: Optional[str] = Field(default=None, max_length=300, description="the condition that defines the part, e.g. \"highway == 'living_street'\"")
    half: Optional[Literal["north", "south", "east", "west"]] = Field(
        default=None, description="only count features whose centre lies in this half of the study area (split at the study area's centre)")
    of_where: Optional[str] = Field(default=None, max_length=300, description="restrict the whole to these features first (default: the whole layer)")
    measure: Literal["count", "length", "area"] = "count"
    expected_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    tolerance: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    boundary_layer: str = Field(default="boundary", max_length=200, description="layer holding the study-area polygon (used by `half`)")

    @model_validator(mode="after")
    def _has_a_part(self) -> ShareClaim:
        if not self.where and not self.half:
            raise ValueError("a share needs where (a condition), half, or both")
        return self

    def check(self, store: DataStore) -> ClaimResult:
        whole = store.get(self.layer)
        if self.of_where:
            whole = safe_filter(whole, self.of_where)
        if whole.empty:
            return ClaimResult(self.id, "ERROR", "no features in the whole after of_where")
        part = safe_filter(whole, self.where) if self.where else whole
        if self.half and self.layer == self.boundary_layer and self.measure == "area":
            share = _land_share(store.get(self.boundary_layer), self.half)      # how much of the land lies in that half
            ok = abs(share - self.expected_share) <= self.tolerance
            return ClaimResult(self.id, "PASS" if ok else "FAIL", f"the {self.half} half is {share:.3f} of the study area, claimed "
                               f"{self.expected_share} +/- {self.tolerance}", round(share, 4),
                               "" if ok else f"the data says {share:.3f}: update expected_share and the sentence")
        if self.half:
            area_km2, err = _study_area(store, self.boundary_layer)
            if err:
                return ClaimResult(self.id, "ERROR", err)
            part = part[_in_half(part, store.get(self.boundary_layer), self.half)]
        if self.measure == "count":
            a, b = float(len(part)), float(len(whole))
        elif self.measure == "length":
            a, b = _total(part, ("LineString", "MultiLineString")), _total(whole, ("LineString", "MultiLineString"))
        else:
            a, b = _total(part, ("Polygon", "MultiPolygon")), _total(whole, ("Polygon", "MultiPolygon"))
        if b <= 0:
            return ClaimResult(self.id, "ERROR", f"layer '{self.layer}' has no {'lines' if self.measure == 'length' else 'polygons'} to measure")
        share = a / b
        ok = abs(share - self.expected_share) <= self.tolerance
        return ClaimResult(self.id, "PASS" if ok else "FAIL",
                           f"{self.measure}: {a:.4g} of {b:.4g} = {share:.3f}, claimed {self.expected_share} +/- {self.tolerance}",
                           round(share, 4), "" if ok else f"the data says {share:.3f}: update expected_share and the sentence")

    def stated_values(self) -> list[float]:
        return [self.expected_share, round(self.expected_share * 100, 6)]


def _land_share(boundary: gpd.GeoDataFrame, half: str) -> float:
    """The share of the study area that lies in the given half (the area is split at its own centre)."""
    from shapely.geometry import box
    crs = boundary.estimate_utm_crs()
    study = boundary.to_crs(crs).geometry.make_valid().union_all()
    c = study.centroid
    minx, miny, maxx, maxy = study.bounds
    region = {"north": box(minx, c.y, maxx, maxy), "south": box(minx, miny, maxx, c.y),
              "east": box(c.x, miny, maxx, maxy), "west": box(minx, miny, c.x, maxy)}[half]
    return float(study.intersection(region).area / study.area)


def _in_half(gdf: gpd.GeoDataFrame, boundary: gpd.GeoDataFrame, half: str) -> pd.Series:
    """True for the features whose representative point lies in the given half of the study area (split at the area's centre)."""
    crs = boundary.estimate_utm_crs()
    centre = boundary.to_crs(crs).geometry.make_valid().union_all().centroid
    pts = gdf.to_crs(crs).geometry.representative_point()
    mask = {"north": pts.y > centre.y, "south": pts.y <= centre.y, "east": pts.x > centre.x, "west": pts.x <= centre.x}[half]
    return mask.set_axis(gdf.index)


def _total(gdf: gpd.GeoDataFrame, kinds: tuple[str, ...]) -> float:
    """Total length in metres (lines) or dissolved area in square metres (polygons) of the features of the given kinds."""
    if gdf.empty:
        return 0.0
    geom = project(gdf).geometry
    geom = geom[geom.geom_type.isin(kinds)]
    if geom.empty:
        return 0.0
    return float(geom.make_valid().union_all().area) if kinds[0] == "Polygon" else float(geom.length.sum())


_UNSAFE_REGEX = re.compile(r"\)[*+?{]|\(\?[=!<]|\\[1-9]|\(\?P=")
MAX_REGEX_LENGTH = 120


def validate_regex(pattern: str) -> str:
    """Refuse regexes that can run for a very long time (nested quantifiers, backreferences, lookarounds).

    The pattern comes from a model, and ``re`` has no timeout, so a pattern like ``(a+)+$`` could hang the process.
    """
    if len(pattern) > MAX_REGEX_LENGTH:
        raise ValueError(f"grid_regex is longer than {MAX_REGEX_LENGTH} characters")
    if _UNSAFE_REGEX.search(pattern):
        raise ValueError("grid_regex may not quantify a group or use backreferences or lookarounds")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # ambiguous constructs (FutureWarning) are refused rather than guessed
            re.compile(pattern)
    except (re.error, Warning) as e:
        raise ValueError(f"grid_regex is not a valid regular expression: {e}") from None
    return pattern


Claim = Annotated[Union[OrientationClaim, StatClaim, AreaClaim, LengthClaim, ProximityClaim, DensityClaim, CoverageClaim, ShareClaim],
                  Field(discriminator="kind")]

_DECIMALS = re.compile(r"\.(\d+)")


def backs(claim: _Claim, number: float, text: str) -> bool:
    """True if this claim authorises ``number`` (written as ``text`` in prose)."""
    m = _DECIMALS.search(text)
    rounding = 0.5 * 10 ** -(len(m.group(1)) if m else 0)
    return any(abs(number - v) <= max(getattr(claim, "tolerance", 0.0), rounding) for v in claim.stated_values())
