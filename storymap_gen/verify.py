"""Claim grounding: check statements in the story against the underlying GIS data.

Motivating bug: a story marked Northern Boulevard as the street that "breaks the
grid". Measuring segment bearings showed it is parallel to the avenues; Broadway
is the genuinely diagonal street. This module turns that manual check into a
repeatable test that runs before a story is compiled or published.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

import geopandas as gpd
import numpy as np

from .spec import OrientationClaim, StatClaim, StorySpec

GRID_NAME = re.compile(r"^\d+ (AVE|ST|RD|PL|DR|LN)$")


@dataclass
class ClaimResult:
    id: str
    status: str  # PASS | FAIL | ERROR
    detail: str
    measured: Optional[float] = None


class DataStore:
    def __init__(self, layers: dict[str, gpd.GeoDataFrame]):
        self.layers = {k: v for k, v in layers.items()}

    @classmethod
    def from_files(cls, paths: dict[str, str]) -> "DataStore":
        return cls({name: gpd.read_file(p) for name, p in paths.items()})

    def get(self, layer: str) -> gpd.GeoDataFrame:
        if layer not in self.layers:
            raise KeyError(f"layer '{layer}' not loaded")
        return self.layers[layer]


def _segments(gdf: gpd.GeoDataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Per-vertex-pair (length, bearing in [0,180)) for every line part, in metres."""
    projected = gdf.to_crs(gdf.estimate_utm_crs())
    lengths, bearings = [], []
    for geom in projected.geometry:
        if geom is None or geom.is_empty:
            continue
        parts = geom.geoms if hasattr(geom, "geoms") else [geom]
        for part in parts:
            xy = np.asarray(part.coords)
            d = np.diff(xy[:, :2], axis=0)
            lengths += list(np.hypot(d[:, 0], d[:, 1]))
            bearings += list(np.degrees(np.arctan2(d[:, 0], d[:, 1])) % 180)
    return np.array(lengths), np.array(bearings)


def grid_axis(lengths: np.ndarray, bearings: np.ndarray) -> float:
    """Dominant grid direction in [0,90). Uses 4x angle so perpendicular axes reinforce."""
    a = np.radians(bearings * 4)
    theta4 = math.atan2((lengths * np.sin(a)).sum(), (lengths * np.cos(a)).sum())
    return (math.degrees(theta4) / 4) % 90


def deviation_from_grid(bearings: np.ndarray, axis: float) -> np.ndarray:
    """Angular distance (0..45 deg) from the nearest grid axis."""
    return np.abs(((bearings - axis + 45) % 90) - 45)


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    cum = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cum, cum[-1] / 2)])


def _check_orientation(c: OrientationClaim, store: DataStore) -> ClaimResult:
    gdf = store.get(c.layer)
    names = gdf[c.name_field].fillna("").str.upper().str.strip()
    grid = gdf[names.str.match(GRID_NAME)]
    target = gdf[names == c.street.upper().strip()]
    if grid.empty:
        return ClaimResult(c.id, "ERROR", "no numbered streets/avenues found to define the grid")
    if target.empty:
        return ClaimResult(c.id, "ERROR", f"street '{c.street}' not found in layer '{c.layer}'")
    gl, gb = _segments(grid)
    axis = grid_axis(gl, gb)
    tl, tb = _segments(target)
    dev = _weighted_median(deviation_from_grid(tb, axis), tl)
    is_diag = dev > c.tolerance_deg
    ok = is_diag if c.relation == "diagonal_to_grid" else not is_diag
    detail = (f"{c.street}: median deviation from grid axis = {dev:.1f} deg "
              f"(grid axis {axis:.1f} deg, tolerance {c.tolerance_deg} deg) -> "
              f"{'diagonal' if is_diag else 'parallel'}; claim says {c.relation}")
    return ClaimResult(c.id, "PASS" if ok else "FAIL", detail, round(dev, 2))


def _check_stat(c: StatClaim, store: DataStore) -> ClaimResult:
    gdf = store.get(c.layer)
    if c.where:
        gdf = gdf.query(c.where)
    if c.agg == "count":
        measured = float(len(gdf))
    else:
        if c.field is None or c.field not in gdf:
            return ClaimResult(c.id, "ERROR", f"field '{c.field}' missing in '{c.layer}'")
        measured = float(getattr(gdf[c.field], c.agg)())
    ok = abs(measured - c.expected) <= c.tolerance
    return ClaimResult(c.id, "PASS" if ok else "FAIL",
                       f"{c.agg}({c.field or '*'}) = {measured:.4g}, claimed {c.expected} +/- {c.tolerance}",
                       round(measured, 4))


def verify_claims(spec: StorySpec, store: DataStore) -> list[ClaimResult]:
    out = []
    for c in spec.claims:
        try:
            out.append(_check_orientation(c, store) if isinstance(c, OrientationClaim)
                       else _check_stat(c, store))
        except Exception as e:  # a broken claim must not hide the others
            out.append(ClaimResult(c.id, "ERROR", f"{type(e).__name__}: {e}"))
    return out


def orientation_facts(store: DataStore, streets: list[str], layer: str = "streets_centerline",
                      name_field: str = "full_stree", tolerance_deg: float = 15.0) -> str:
    """Measured street-vs-grid facts, to be given to the writer BEFORE it drafts (verify-first)."""
    gdf = store.get(layer)
    names = gdf[name_field].fillna("").str.upper().str.strip()
    gl, gb = _segments(gdf[names.str.match(GRID_NAME)])
    axis = grid_axis(gl, gb)
    rows = []
    for st in streets:
        tl, tb = _segments(gdf[names == st.upper().strip()])
        rows.append(f"{st.upper()}: {_weighted_median(deviation_from_grid(tb, axis), tl):.1f} deg")
    return ("\n## Measured facts (from the data, trustworthy)\n"
            "Median deviation of each street from the neighbourhood grid axis: " + "; ".join(rows) +
            f". A street is 'diagonal_to_grid' only if its deviation is above {tolerance_deg:g} deg, "
            "otherwise 'parallel_to_grid'.\n")
