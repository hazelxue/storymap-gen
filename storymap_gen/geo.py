"""Geometry helpers and the in-memory data store used by claim checks."""
from __future__ import annotations

import math
import os
import re
from pathlib import Path

import geopandas as gpd
import numpy as np

# Default definition of "the grid" for NYC-style numbered streets and avenues.
GRID_NAME = re.compile(r"^\d+ (AVE|ST|RD|PL|DR|LN)$")
GRID_NAME_PATTERN = GRID_NAME.pattern

ACRE_M2 = 4046.8564224
SQFT_M2 = 0.09290304


class DataError(ValueError):
    """A data file is missing, unreadable, too large, or has no usable coordinates."""


def max_data_bytes() -> int:
    return int(float(os.environ.get("STORYMAP_MAX_DATA_MB", "200")) * 1024 * 1024)


def read_layer(path: str | Path, name: str = "layer") -> gpd.GeoDataFrame:
    """Read one GIS file defensively: size cap, clear errors, and a CRS (assumed WGS84 only if the numbers look like lon/lat)."""
    p = Path(path)
    if not p.is_file():
        raise DataError(f"data file for '{name}' does not exist")
    if p.stat().st_size > max_data_bytes():
        raise DataError(f"data file for '{name}' is larger than {max_data_bytes() // (1024 * 1024)} MB "
                        "(raise STORYMAP_MAX_DATA_MB to allow it)")
    try:
        gdf = gpd.read_file(p)
    except Exception as e:  # the readers raise many different types for corrupt input
        raise DataError(f"could not read the data file for '{name}' ({type(e).__name__})") from None
    if len(gdf) == 0:
        return gdf
    if gdf.crs is None or gdf.crs.is_geographic:
        xmin, ymin, xmax, ymax = (float(v) for v in gdf.total_bounds)
        if not all(math.isfinite(v) for v in (xmin, ymin, xmax, ymax)):
            raise DataError(f"'{name}' has no usable geometry")
        if not (-180 <= xmin <= xmax <= 180 and -90 <= ymin <= ymax <= 90):
            raise DataError(f"'{name}' has coordinates outside the longitude/latitude range; it may be in a projected "
                            "coordinate system without a declaration (GeoJSON defaults to WGS84)")
        if gdf.crs is None:
            gdf = gdf.set_crs(4326)
    return gdf


class DataStore:
    """Named GeoDataFrames (WGS84 or any CRS) that claims are checked against."""

    def __init__(self, layers: dict[str, gpd.GeoDataFrame]):
        self.layers = dict(layers)

    @classmethod
    def from_files(cls, paths: dict[str, str]) -> DataStore:
        return cls({name: read_layer(p, name) for name, p in paths.items()})

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
