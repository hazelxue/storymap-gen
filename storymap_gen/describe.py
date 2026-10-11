"""Profile data layers: what is in them, how big they are, and which claim types fit.

The profile is meant to be read by a model (or a person) that has never seen the data, right before it writes claims.
Everything here is computed from the files; nothing is guessed.
"""
from __future__ import annotations

import math
from typing import Any

import geopandas as gpd
import pandas as pd

from .geo import ACRE_M2, DataStore, project

TOP_VALUES = 6
MAX_FIELDS = 40


def _field_profile(series: pd.Series) -> dict[str, Any]:
    nonnull = int(series.notna().sum())
    out: dict[str, Any] = {"dtype": str(series.dtype), "non_null": nonnull, "of": int(len(series))}
    if nonnull == 0:
        return out
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        vals = series.dropna().astype(float)
        out.update({"min": round(float(vals.min()), 4), "max": round(float(vals.max()), 4), "mean": round(float(vals.mean()), 4)})
    else:
        counts = series.dropna().astype(str).value_counts()
        out["unique"] = int(len(counts))
        out["top"] = [f"{str(k)[:40]} ({int(v)})" for k, v in counts.head(TOP_VALUES).items()]
    return out


def profile_layer(name: str, gdf: gpd.GeoDataFrame) -> dict[str, Any]:
    kinds = gdf.geometry.geom_type.value_counts().to_dict() if len(gdf) else {}
    prof: dict[str, Any] = {"layer": name, "features": int(len(gdf)), "geometry": {str(k): int(v) for k, v in kinds.items()}}
    if len(gdf) and gdf.geometry.notna().any():
        xmin, ymin, xmax, ymax = (round(float(v), 5) for v in gdf.total_bounds)
        if all(math.isfinite(v) for v in (xmin, ymin, xmax, ymax)):
            prof["bounds_lonlat"] = [xmin, ymin, xmax, ymax]
        proj = project(gdf)
        lines = proj.geometry[proj.geometry.geom_type.isin(["LineString", "MultiLineString"])]
        polys = proj.geometry[proj.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        if len(lines):
            prof["total_length_km"] = round(float(lines.length.sum()) / 1000, 3)
        if len(polys):
            prof["total_area_acres_dissolved"] = round(float(polys.make_valid().union_all().area) / ACRE_M2, 3)
    cols = [c for c in gdf.columns if c != gdf.geometry.name]
    prof["fields"] = {str(c): _field_profile(gdf[c]) for c in cols[:MAX_FIELDS]}
    if len(cols) > MAX_FIELDS:
        prof["fields_omitted"] = len(cols) - MAX_FIELDS
    prof["claim_types"] = _claim_hints(kinds)
    return prof


def _claim_hints(kinds: dict[Any, Any]) -> list[str]:
    hints = ["layer_stat (count, min, max, mean, sum of a numeric field, optionally filtered with 'where')"]
    names = set(map(str, kinds))
    if names & {"Polygon", "MultiPolygon"}:
        hints.append("layer_area (total polygon area; overlaps are merged)")
    if names & {"LineString", "MultiLineString"}:
        hints += ["layer_length (total length)", "street_orientation (needs a name field; diagonal or parallel to the grid)"]
    hints.append("within_distance (share of this layer within D metres of another layer)")
    return hints


def profile_store(store: DataStore) -> list[dict[str, Any]]:
    return [profile_layer(name, gdf) for name, gdf in sorted(store.layers.items())]


def render_markdown(profiles: list[dict[str, Any]]) -> str:
    lines = ["# Data profile", "", "Use only these layers and fields in claims. Numbers below are measured from the files.", ""]
    for p in profiles:
        lines.append(f"## {p['layer']}  ({p['features']} features)")
        lines.append(f"- geometry: {', '.join(f'{k} x{v}' for k, v in p['geometry'].items()) or 'none'}")
        if "bounds_lonlat" in p:
            lines.append(f"- bounds (lon/lat): {p['bounds_lonlat']}")
        if "total_length_km" in p:
            lines.append(f"- total length: {p['total_length_km']} km")
        if "total_area_acres_dissolved" in p:
            lines.append(f"- total area (overlaps merged): {p['total_area_acres_dissolved']} acres")
        lines.append("- fields:")
        for fname, f in p["fields"].items():
            detail = f"{f['non_null']}/{f['of']} filled"
            if "min" in f:
                detail += f", range {f['min']} to {f['max']}, mean {f['mean']}"
            if "top" in f:
                detail += f", {f['unique']} distinct, top: {', '.join(f['top'][:4])}"
            lines.append(f"  - `{fname}` ({f['dtype']}): {detail}")
        lines.append(f"- claim types that fit: {'; '.join(p['claim_types'])}")
        lines.append("")
    return "\n".join(lines)
