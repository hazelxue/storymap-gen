"""Build an ArcGIS Web Map (item JSON) from data layers, with the features embedded in the map itself.

Embedding the features (a *feature collection*) means a map can be created without hosting a feature service, so a region
that has no ready-made map can still get one. Geometries are reprojected to Web Mercator, simplified, and rounded; layers and
the whole map are size-capped, because the JSON becomes the item's text.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

MAX_FEATURES_PER_LAYER = 20_000
INT32_MAX = 2**31 - 1
SPATIAL_REF = {"wkid": 102100, "latestWkid": 3857}
BASEMAP_URL = "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer"
POLY, LINE, POINT = "esriGeometryPolygon", "esriGeometryPolyline", "esriGeometryPoint"

# role -> (symbol builder arguments); colours are RGBA
STYLES: dict[str, dict[str, Any]] = {
    "line": {"color": [110, 110, 110, 255], "width": 1.0},
    "bike": {"color": [0, 120, 200, 255], "width": 2.2},
    "park": {"fill": [76, 175, 80, 150], "outline": [46, 125, 50, 255], "width": 1.0},
    "transit": {"color": [220, 40, 40, 255], "size": 9, "style": "esriSMSCircle"},
    "bus": {"color": [90, 90, 90, 255], "size": 5, "style": "esriSMSCircle"},
    "school": {"color": [30, 100, 200, 255], "size": 8, "style": "esriSMSSquare"},
    "health": {"color": [200, 30, 120, 255], "size": 9, "style": "esriSMSDiamond"},
    "building": {"fill": [170, 140, 120, 180], "outline": [120, 90, 70, 255], "width": 0.4},
    "landuse": {"fill": [200, 200, 170, 120], "outline": [150, 150, 120, 200], "width": 0.5},
    "water": {"fill": [120, 170, 230, 200], "outline": [60, 110, 190, 255], "width": 1.0, "color": [60, 110, 190, 255]},
    "boundary": {"fill": [0, 0, 0, 0], "outline": [40, 40, 40, 255], "width": 2.0, "color": [40, 40, 40, 255]},
    "default": {"fill": [150, 120, 200, 140], "outline": [90, 60, 140, 255], "width": 1.0, "color": [90, 60, 140, 255], "size": 7,
                "style": "esriSMSCircle"},
}


def layer_role(name: str) -> str:
    """Style role of a data layer (a known theme, the study-area boundary, or the plain default)."""
    from .sources.overpass import LAYERS
    return "boundary" if name == "boundary" else LAYERS[name].role if name in LAYERS else "default"


def layer_title(name: str) -> str:
    from .sources.overpass import LAYERS
    return "Study area" if name == "boundary" else LAYERS[name].title if name in LAYERS else name


def layer_id(name: str) -> str:
    """The id of a layer inside a generated Web Map; stable, so a story can refer to it (swipes) before the map exists."""
    return "layer-" + "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name)


def max_webmap_bytes() -> int:
    return int(float(os.environ.get("STORYMAP_MAX_WEBMAP_MB", "8")) * 1024 * 1024)


def _esri_geometry(geom: BaseGeometry) -> tuple[str, dict[str, Any]] | None:
    t = geom.geom_type
    if t == "Point":
        return POINT, {"x": round(geom.x, 2), "y": round(geom.y, 2), "spatialReference": SPATIAL_REF}
    if t in ("LineString", "MultiLineString"):
        parts = [geom] if t == "LineString" else list(geom.geoms)
        paths = [[[round(x, 2), round(y, 2)] for x, y in p.coords] for p in parts if len(p.coords) >= 2]
        return (LINE, {"paths": paths, "spatialReference": SPATIAL_REF}) if paths else None
    if t in ("Polygon", "MultiPolygon"):
        polys: list[Polygon] = [geom] if t == "Polygon" else list(geom.geoms)
        rings: list[list[list[float]]] = []
        for poly in polys:
            if poly.is_empty:
                continue
            poly = orient(poly, sign=-1.0)  # Esri: exterior rings clockwise, holes counter-clockwise
            for ring in [poly.exterior, *poly.interiors]:
                rings.append([[round(x, 2), round(y, 2)] for x, y in ring.coords])
        return (POLY, {"rings": rings, "spatialReference": SPATIAL_REF}) if rings else None
    return None


def _symbol(geometry_type: str, style: dict[str, Any]) -> dict[str, Any]:
    if geometry_type == POINT:
        return {"type": "esriSMS", "style": style.get("style", "esriSMSCircle"), "color": style.get("color", [90, 60, 140, 255]),
                "size": style.get("size", 7), "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": [255, 255, 255, 255], "width": 0.8}}
    if geometry_type == LINE:
        return {"type": "esriSLS", "style": "esriSLSSolid", "color": style.get("color", [110, 110, 110, 255]), "width": style.get("width", 1.0)}
    return {"type": "esriSFS", "style": "esriSFSSolid", "color": style.get("fill", [150, 120, 200, 140]),
            "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": style.get("outline", [90, 60, 140, 255]), "width": style.get("width", 1.0)}}


def _field_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "esriFieldTypeString"
    if pd.api.types.is_integer_dtype(series):
        # esriFieldTypeInteger is 32-bit: a feature with a larger value (OSM ids reach 10^10) is silently not drawn
        fits = series.dropna().abs().max() <= INT32_MAX if series.notna().any() else True
        return "esriFieldTypeInteger" if fits else "esriFieldTypeDouble"
    if pd.api.types.is_float_dtype(series):
        return "esriFieldTypeDouble"
    return "esriFieldTypeString"


def _clean(value: Any) -> Any:
    """One attribute value as JSON: numbers stay numbers, NaN becomes null, everything else is short text."""
    if value is None or value is pd.NA:
        return None
    if hasattr(value, "item"):
        value = value.item()  # numpy scalar to Python
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, int):
        return value
    return str(value)[:255]


def _layer(title: str, gdf: gpd.GeoDataFrame, role: str, simplify_m: float) -> tuple[dict[str, Any], tuple[float, float, float, float]] | None:
    merc = gdf.to_crs(3857)
    attrs = [c for c in gdf.columns if c != gdf.geometry.name][:12]
    features: list[dict[str, Any]] = []
    gtype = None
    for i, (_, row) in enumerate(merc.iterrows(), start=1):
        geom = row.geometry
        # a missing geometry is None in some pandas versions and NaN in others
        if geom is None or not isinstance(geom, BaseGeometry) or geom.is_empty:
            continue
        if simplify_m > 0 and geom.geom_type != "Point":
            geom = geom.simplify(simplify_m, preserve_topology=True)
        converted = _esri_geometry(geom)
        if converted is None:
            continue
        gtype = gtype or converted[0]
        if converted[0] != gtype:
            continue  # a layer has one geometry type
        attributes = {"OBJECTID": i, **{c: _clean(row[c]) for c in attrs}}
        features.append({"geometry": converted[1], "attributes": attributes})
    if not features or gtype is None:
        return None
    if len(features) > MAX_FEATURES_PER_LAYER:
        raise ValueError(f"layer '{title}' has {len(features)} features; a map holds at most {MAX_FEATURES_PER_LAYER} per layer. "
                         "Use a smaller area or fetch a narrower layer.")
    style = {**STYLES["default"], **STYLES.get(role, {})}
    fields = [{"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID"}] + [
        {"name": c, "type": _field_type(gdf[c]), "alias": c, **({"length": 255} if _field_type(gdf[c]) == "esriFieldTypeString" else {})}
        for c in attrs]
    display = "name" if "name" in attrs else (attrs[0] if attrs else "OBJECTID")
    xmin, ymin, xmax, ymax = (float(v) for v in merc.total_bounds)
    layer = {
        "layerDefinition": {"name": title, "geometryType": gtype, "objectIdField": "OBJECTID", "type": "Feature Layer",
                            "displayField": display, "drawingInfo": {"renderer": {"type": "simple", "symbol": _symbol(gtype, style)}},
                            "fields": fields},
        "featureSet": {"geometryType": gtype, "features": features},
        "popupInfo": {"title": "{" + display + "}", "fieldInfos": [{"fieldName": c, "label": c, "visible": True} for c in attrs[:8]]},
    }
    return layer, (xmin, ymin, xmax, ymax)


def _padded(box: tuple[float, float, float, float], pad: float = 0.06) -> tuple[float, float, float, float]:
    xmin, ymin, xmax, ymax = box
    dx, dy = max((xmax - xmin) * pad, 50.0), max((ymax - ymin) * pad, 50.0)
    return xmin - dx, ymin - dy, xmax + dx, ymax + dy


def build_webmap(layers: dict[str, gpd.GeoDataFrame], roles: dict[str, str] | None = None, titles: dict[str, str] | None = None,
                 extent: tuple[float, float, float, float] | None = None) -> tuple[dict[str, Any], tuple[float, float, float, float]]:
    """Return ``(web map JSON, extent in Web Mercator)`` for the given layers (drawn in the order polygons, lines, points)."""
    roles, titles = roles or {}, titles or {}
    order = {POLY: 0, LINE: 1, POINT: 2}
    built: list[tuple[int, str, dict[str, Any]]] = []
    boxes: list[tuple[float, float, float, float]] = []
    span = 0.0
    for gdf in layers.values():
        if gdf.empty:
            continue
        merc_bounds = gdf.to_crs(3857).total_bounds
        span = max(span, float(max(merc_bounds[2] - merc_bounds[0], merc_bounds[3] - merc_bounds[1])))
    simplify_m = 0.5 if span < 8_000 else 1.5 if span < 25_000 else 3.0
    for name, gdf in layers.items():
        res = _layer(titles.get(name, name), gdf, roles.get(name, "default"), simplify_m)
        if res is None:
            continue
        layer, box = res
        boxes.append(box)
        built.append((order[layer["featureSet"]["geometryType"]], name, layer))
    if not built:
        raise ValueError("none of the requested layers has drawable features")
    built.sort(key=lambda t: t[0])
    box = extent or _padded((min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)))
    webmap = {
        "operationalLayers": [
            {"id": layer_id(name), "title": layer["layerDefinition"]["name"], "visibility": True, "opacity": 1,
             "layerType": "ArcGISFeatureLayer", "featureCollection": {"layers": [layer], "showLegend": True}}
            for _, name, layer in built],
        "baseMap": {"title": "Light Gray Canvas", "baseMapLayers": [
            {"id": "base-0", "layerType": "ArcGISTiledMapServiceLayer", "url": BASEMAP_URL, "visibility": True, "opacity": 1,
             "title": "World Light Gray Base"}]},
        "spatialReference": SPATIAL_REF,
        "version": "2.28",
        "authoringApp": "storymap-gen",
        "authoringAppVersion": "0.4.0",
        "initialState": {"viewpoint": {"targetGeometry": {"xmin": box[0], "ymin": box[1], "xmax": box[2], "ymax": box[3],
                                                          "spatialReference": SPATIAL_REF}}},
    }
    if len(json.dumps(webmap)) > max_webmap_bytes():
        raise ValueError(f"the map would be larger than {max_webmap_bytes() // (1024 * 1024)} MB; use a smaller area or fewer layers "
                         "(STORYMAP_MAX_WEBMAP_MB raises the limit)")
    return webmap, box


def mercator_to_lonlat(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    def lon(x: float) -> float:
        return x / 20037508.34 * 180

    def lat(y: float) -> float:
        return math.degrees(2 * math.atan(math.exp(y / 20037508.34 * math.pi)) - math.pi / 2)

    return lon(box[0]), lat(box[1]), lon(box[2]), lat(box[3])
