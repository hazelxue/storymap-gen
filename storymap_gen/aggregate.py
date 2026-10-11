"""Sub-areas and their numbers: compare the parts of a study area (districts, neighbourhoods, tracts).

``subareas_frame`` turns the places of a combined region into a polygon layer. ``aggregate`` adds, for each polygon, the measures of
the other layers: how many points fall inside, how many km of line, what share of its area a polygon layer covers, and each per km2.
The result is an ordinary polygon layer, so it can be coloured by any of its fields (a graduated map) and claims can be made about
one sub-area with a filter on its name.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

from .geo import DataStore
from .safe_expr import safe_filter
from .sources.geocode import Region

MAX_SUBAREAS = 200


def subareas_frame(region: Region) -> gpd.GeoDataFrame:
    """One polygon per place of a combined region (name, area in km2)."""
    if not region.parts:
        raise ValueError("this region has no sub-areas: make it from several places (`region \"A\" \"B\" ...`), or supply your own polygon layer")
    gdf = gpd.GeoDataFrame({"name": [p["name"] for p in region.parts]}, geometry=[shape(p["boundary"]) for p in region.parts], crs=4326)
    gdf["area_km2"] = (gdf.to_crs(gdf.estimate_utm_crs()).area / 1e6).round(3)
    return gdf


def _col(layer: str, suffix: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", layer) + suffix


def aggregate(store: DataStore, subareas: str, layers: list[str], where: Mapping[str, str] | None = None) -> gpd.GeoDataFrame:
    """The sub-area polygons with measures of the given layers added.

    Points are counted by the polygon that contains them; lines are cut at the polygon edge and measured in km; polygon layers give the
    share (percent) of the sub-area they cover. Every measure also comes per km2 (points, lines) of the sub-area. ``where`` filters a layer first
    (``{"transit": "railway == 'station'"}``) and names its columns after the layer.
    """
    areas = store.get(subareas)
    if len(areas) > MAX_SUBAREAS:
        raise ValueError(f"at most {MAX_SUBAREAS} sub-areas")
    if not areas.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError(f"layer '{subareas}' must hold polygons")
    crs = areas.estimate_utm_crs()
    polys = areas.to_crs(crs).reset_index(drop=True)
    out = areas.reset_index(drop=True).copy()
    km2 = polys.geometry.area / 1e6
    out["area_km2"] = km2.round(3)
    for name in layers:
        if name == subareas:
            continue
        gdf = store.get(name)
        if where and name in where:
            gdf = safe_filter(gdf, where[name])
        g = gdf.to_crs(crs)
        kinds = g.geometry.geom_type
        if kinds.isin(["Point", "MultiPoint"]).any() and not kinds.isin(["Polygon", "MultiPolygon", "LineString", "MultiLineString"]).any():
            pts = g.geometry.representative_point()
            counts = [int(pts.within(poly).sum()) for poly in polys.geometry]
            out[_col(name, "_count")] = counts
            out[_col(name, "_per_km2")] = [round(c / a, 3) if a else 0.0 for c, a in zip(counts, km2, strict=True)]
        elif kinds.isin(["LineString", "MultiLineString"]).all():
            km = [float(g.geometry.intersection(poly).length.sum()) / 1000 for poly in polys.geometry]
            out[_col(name, "_km")] = [round(v, 3) for v in km]
            out[_col(name, "_km_per_km2")] = [round(v / a, 3) if a else 0.0 for v, a in zip(km, km2, strict=True)]
        elif kinds.isin(["Polygon", "MultiPolygon"]).any():
            union = g.geometry.make_valid().union_all()
            share = [float(poly.intersection(union).area / poly.area * 100) if poly.area else 0.0 for poly in polys.geometry]
            out[_col(name, "_pct")] = [round(v, 2) for v in share]
        else:
            raise ValueError(f"layer '{name}' mixes geometry kinds; filter it to one kind first")
    return out


def fields_added(before: pd.DataFrame, after: pd.DataFrame) -> list[str]:
    return [c for c in after.columns if c not in before.columns]
