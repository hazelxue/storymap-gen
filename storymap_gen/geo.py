"""Geometry helpers and the in-memory data store used by claim checks."""
from __future__ import annotations

import math
import re

import geopandas as gpd
import numpy as np

# Default definition of "the grid" for NYC-style numbered streets and avenues.
GRID_NAME = re.compile(r"^\d+ (AVE|ST|RD|PL|DR|LN)$")
GRID_NAME_PATTERN = GRID_NAME.pattern

ACRE_M2 = 4046.8564224
SQFT_M2 = 0.09290304


class DataStore:
    """Named GeoDataFrames (WGS84 or any CRS) that claims are checked against."""

    def __init__(self, layers: dict[str, gpd.GeoDataFrame]):
        self.layers = dict(layers)

    @classmethod
    def from_files(cls, paths: dict[str, str]) -> DataStore:
        return cls({name: gpd.read_file(p) for name, p in paths.items()})

    def get(self, layer: str) -> gpd.GeoDataFrame:
        if layer not in self.layers:
            raise KeyError(f"layer '{layer}' not loaded (loaded: {sorted(self.layers)})")
        return self.layers[layer]


def project(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reproject to the local UTM zone so lengths and areas are in metres."""
    return gdf.to_crs(gdf.estimate_utm_crs())


def segments(gdf: gpd.GeoDataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Per-vertex-pair (length, bearing in [0,180)) for every line part, in metres."""
    lengths: list[float] = []
    bearings: list[float] = []
    for geom in project(gdf).geometry:
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


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    cum = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cum, cum[-1] / 2)])
