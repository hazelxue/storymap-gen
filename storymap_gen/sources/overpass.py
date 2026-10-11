"""OpenStreetMap themes through the Overpass API.

``fetch_layer("parks", region)`` returns a GeoDataFrame in WGS84 with a small, stable set of columns. The client rotates
mirrors, backs off between rounds, caches results, refuses oversized queries, and **never uses a partial result**: Overpass
reports timeouts and memory problems in a ``remark`` field next to whatever it managed to collect, and silently using that
would produce wrong counts and areas.

Data (c) OpenStreetMap contributors, ODbL 1.0. Features are those that intersect the bounding box; they are not clipped to it.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import geopandas as gpd
from shapely.geometry import shape

from .. import net
from . import cache
from .geocode import Region

ATTRIBUTION = "(c) OpenStreetMap contributors (ODbL 1.0), retrieved via the Overpass API"
LICENCE = "ODbL 1.0"
DEFAULT_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
MAX_ELEMENTS = 250_000
MAXSIZE = 256 * 1024 * 1024
CACHE_TTL = 24 * 3600
BACKOFF = (3, 8, 20)

_HIGHWAYS = "motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|pedestrian"
_PARK_TAGS = "park|garden|playground|nature_reserve|dog_park"


@dataclass(frozen=True)
class LayerDef:
    name: str
    title: str
    description: str
    body: str                       # Overpass QL statements; {bb} is replaced by the bounding box
    geometry: tuple[str, ...]       # shapely geometry types to keep
    keep_tags: tuple[str, ...]
    role: str                       # drives default styling (see webmap.py)
    to_points: bool = False         # reduce polygons to a representative point
    max_area_km2: float | None = None


LAYERS: dict[str, LayerDef] = {d.name: d for d in [
    LayerDef("streets", "Streets", "Drivable and pedestrian streets",
             f'way["highway"~"^({_HIGHWAYS})$"]({{bb}});',
             ("LineString", "MultiLineString"), ("highway", "oneway", "lanes", "maxspeed", "surface", "bridge", "tunnel"), "line"),
    LayerDef("bike", "Bike infrastructure", "Cycleways and streets with bike lanes",
             'way["highway"="cycleway"]({bb});way["cycleway"]({bb});way["cycleway:left"]({bb});way["cycleway:right"]({bb});'
             'way["cycleway:both"]({bb});',
             ("LineString", "MultiLineString"), ("highway", "cycleway", "cycleway_left", "cycleway_right", "cycleway_both", "surface"), "bike"),
    LayerDef("parks", "Parks and open space", "Parks, gardens, playgrounds, nature reserves, recreation grounds",
             f'way["leisure"~"^({_PARK_TAGS})$"]({{bb}});relation["leisure"~"^({_PARK_TAGS})$"]({{bb}});'
             'way["landuse"~"^(recreation_ground|village_green)$"]({bb});relation["landuse"~"^(recreation_ground|village_green)$"]({bb});',
             ("Polygon", "MultiPolygon"), ("leisure", "landuse", "operator", "access"), "park"),
    LayerDef("transit", "Transit stations", "Rail, subway and tram stations and station entrances",
             'node["railway"~"^(station|subway_entrance|tram_stop|halt)$"]({bb});node["public_transport"="station"]({bb});',
             ("Point",), ("railway", "public_transport", "network", "operator", "station"), "transit"),
    LayerDef("bus_stops", "Bus stops", "Bus stops", 'node["highway"="bus_stop"]({bb});', ("Point",),
             ("network", "operator", "shelter"), "bus"),
    LayerDef("schools", "Schools", "Schools, kindergartens, colleges and universities",
             'nwr["amenity"~"^(school|kindergarten|college|university)$"]({bb});',
             ("Point", "Polygon", "MultiPolygon"), ("amenity", "operator", "isced_level"), "school", to_points=True),
    LayerDef("health", "Health care", "Hospitals and clinics", 'nwr["amenity"~"^(hospital|clinic)$"]({bb});',
             ("Point", "Polygon", "MultiPolygon"), ("amenity", "healthcare", "operator", "emergency"), "health", to_points=True),
    LayerDef("buildings", "Buildings", "Building footprints (small areas only)",
             'way["building"]({bb});relation["building"]({bb});', ("Polygon", "MultiPolygon"),
             ("building", "building_levels", "height", "amenity"), "building", max_area_km2=6.0),
    LayerDef("landuse", "Land use", "Land-use polygons", 'way["landuse"]({bb});relation["landuse"]({bb});',
             ("Polygon", "MultiPolygon"), ("landuse",), "landuse"),
    LayerDef("water", "Water", "Water bodies and waterways",
             'way["natural"="water"]({bb});relation["natural"="water"]({bb});way["waterway"~"^(river|stream|canal)$"]({bb});',
             ("Polygon", "MultiPolygon", "LineString", "MultiLineString"), ("natural", "waterway"), "water"),
]}


def mirrors() -> list[str]:
    raw = os.environ.get("STORYMAP_OVERPASS_MIRRORS", "")
    urls = [u.strip() for u in raw.split(",") if u.strip()] or DEFAULT_MIRRORS
    return urls


def build_query(layer: LayerDef, region: Region, timeout: int = 90) -> str:
    return f"[out:json][timeout:{timeout}][maxsize:{MAXSIZE}];\n({layer.body.format(bb=region.overpass_bbox)});\nout geom;"


def _run(query: str, use_cache: bool, sleep: Callable[[float], None]) -> dict[str, Any]:
    key = "overpass:" + query
    if use_cache:
        hit = cache.get(key, CACHE_TTL)
        if hit is not None:
            try:
                return json.loads(hit)
            except json.JSONDecodeError:
                pass
    last = "no mirror answered"
    for rnd in range(len(BACKOFF) + 1):
        for url in mirrors():
            try:
                body = net.request_bytes("POST", url, data={"data": query}, allowed_hosts=[urlsplit(url).hostname or ""],
                                         timeout=(10, 120))
                data = json.loads(body)
            except net.NetworkError as e:
                last = str(e)
                continue
            except json.JSONDecodeError:
                last = "a server returned something that is not JSON"
                continue
            remark = str(data.get("remark", "")) if isinstance(data, dict) else ""
            if "runtime error" in remark.lower() or "timed out" in remark.lower() or "out of memory" in remark.lower():
                last = f"a server returned a partial result ({remark[:120]})"
                continue
            if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
                last = "a server returned an unexpected document"
                continue
            if use_cache:
                cache.put(key, body)
            return data
        if rnd < len(BACKOFF):
            sleep(BACKOFF[rnd])
    raise net.NetworkError(
        f"all Overpass servers are busy or unreachable ({last}). Wait a few minutes and retry, choose a smaller area, "
        "or supply the layer as a local file with --data.")


def _to_frame(data: dict[str, Any], layer: LayerDef) -> gpd.GeoDataFrame:
    import osm2geojson
    if len(data["elements"]) > MAX_ELEMENTS:
        raise net.NetworkError(f"the query returned more than {MAX_ELEMENTS} elements; choose a smaller area or a narrower layer")
    features = osm2geojson.json2geojson(data)["features"]
    rows: list[dict[str, Any]] = []
    geoms = []
    seen: set[tuple[str, int]] = set()
    for f in features:
        props = f.get("properties") or {}
        ident = (str(props.get("type")), int(props.get("id", 0) or 0))
        if ident in seen:
            continue
        try:
            geom = shape(f["geometry"])
        except Exception:  # noqa: BLE001 - one malformed feature must not sink the layer
            continue
        if geom.is_empty or geom.geom_type not in layer.geometry:
            continue
        if layer.to_points and geom.geom_type != "Point":
            geom = geom.representative_point()
        seen.add(ident)
        tags = props.get("tags") or {}
        row: dict[str, Any] = {"osm_type": ident[0], "osm_id": ident[1], "name": tags.get("name")}
        for t in layer.keep_tags:
            row[t] = tags.get(t, tags.get(t.replace("_", ":", 1)))
        rows.append(row)
        geoms.append(geom)
    columns = ["osm_type", "osm_id", "name", *layer.keep_tags]
    if not rows:
        return gpd.GeoDataFrame({c: [] for c in columns}, geometry=gpd.GeoSeries([], crs=4326))
    return gpd.GeoDataFrame(rows, geometry=geoms, crs=4326)[[*columns, "geometry"]]


def _keep_types(geom: Any, allowed: tuple[str, ...]) -> Any:
    """Parts of ``geom`` whose type is allowed (clipping can turn a polygon into a collection of polygons and lines)."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in allowed:
        return geom
    if hasattr(geom, "geoms"):
        parts = [g for g in geom.geoms if g.geom_type in allowed]
        if parts:
            from shapely.ops import unary_union
            return unary_union(parts)
    return None


def clip_to_region(gdf: gpd.GeoDataFrame, layer: LayerDef, region: Region, mode: str = "boundary") -> gpd.GeoDataFrame:
    """Cut features to the study area so lengths, areas and counts describe the place and not the whole download box."""
    mask = region.clip_shape(mode)
    if mask is None or gdf.empty:
        return gdf
    fixed = gdf.copy()
    fixed["geometry"] = fixed.geometry.make_valid()
    clipped = fixed.clip(mask, keep_geom_type=False).copy()
    clipped["geometry"] = clipped.geometry.apply(lambda g: _keep_types(g, layer.geometry))
    clipped = clipped[clipped.geometry.notna() & ~clipped.geometry.is_empty]
    return clipped.reset_index(drop=True)


def fetch_layer(name: str, region: Region, *, use_cache: bool = True, clip: str = "boundary",
                sleep: Callable[[float], None] = time.sleep) -> gpd.GeoDataFrame:
    """Download one OpenStreetMap theme for a region. ``clip``: 'boundary' (default), 'bbox', or 'none'."""
    if name not in LAYERS:
        raise ValueError(f"unknown layer '{name}' (available: {', '.join(sorted(LAYERS))})")
    if clip not in ("boundary", "bbox", "none"):
        raise ValueError("clip must be 'boundary', 'bbox' or 'none'")
    layer = LAYERS[name]
    if layer.max_area_km2 and region.area_km2 > layer.max_area_km2:
        raise ValueError(f"layer '{name}' is limited to areas up to {layer.max_area_km2:g} km2 (this one is {region.area_km2:.1f} km2)")
    gdf = _to_frame(_run(build_query(layer, region), use_cache, sleep), layer)
    return clip_to_region(gdf, layer, region, clip)
