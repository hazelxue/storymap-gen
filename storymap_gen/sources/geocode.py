"""Place name to bounding box and boundary, using OpenStreetMap's Nominatim (respecting its usage policy: identifying
User-Agent, at most one request per second, results cached).

The boundary matters: data fetched for a bounding box also contains features that stick out of it (a nature reserve that
covers a third of a city intersects every neighbourhood next to it). Clipping to the real boundary keeps lengths, areas
and counts about the place the story is about.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import warnings
from dataclasses import asdict, dataclass
from typing import Any

import geopandas as gpd
from shapely import make_valid
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry

from .. import net
from . import cache

NOMINATIM = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HOSTS = ["nominatim.openstreetmap.org"]
ATTRIBUTION = "(c) OpenStreetMap contributors (ODbL 1.0), geocoded with Nominatim"
CACHE_TTL = 30 * 24 * 3600
MAX_BOUNDARY_BYTES = 300_000
_last_request = 0.0


def _polygonal(geom: BaseGeometry) -> BaseGeometry | None:
    """Only the polygon parts of a geometry (repairing it first), or None."""
    geom = make_valid(geom)
    parts = [g for g in (geom.geoms if hasattr(geom, "geoms") else [geom]) if g.geom_type in ("Polygon", "MultiPolygon")]
    if not parts:
        return None
    from shapely.ops import unary_union
    out = unary_union(parts)
    return None if out.is_empty else out


def clean_boundary(geojson: Any, limit: int = MAX_BOUNDARY_BYTES) -> dict[str, Any] | None:
    """A simplified, valid Polygon/MultiPolygon GeoJSON geometry of at most ``limit`` bytes, or None if it cannot be used."""
    if not isinstance(geojson, dict) or geojson.get("type") not in ("Polygon", "MultiPolygon"):
        return None
    try:
        geom = _polygonal(shape(geojson))
    except Exception:  # noqa: BLE001 - a malformed boundary is simply not used
        return None
    if geom is None:
        return None
    tolerance = 0.00005
    for _ in range(8):
        data = json.loads(json.dumps(mapping(geom)))
        if len(json.dumps(data)) <= limit:
            minx, miny, maxx, maxy = geom.bounds
            if all(math.isfinite(v) for v in (minx, miny, maxx, maxy)) and -180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90:
                return data
            return None
        simplified = _polygonal(geom.simplify(tolerance, preserve_topology=True))
        if simplified is None:
            return None
        geom, tolerance = simplified, tolerance * 2
    return None


STRAY_PART_DEGREES = 0.15   # about 15 km: a piece of a boundary this far from its main body is a mapping mistake, not part of the place


def drop_stray_parts(boundary: dict[str, Any]) -> tuple[dict[str, Any], float]:
    """Remove parts of a MultiPolygon boundary that lie far from its largest part, and say how much area (in square degrees, scaled to
    roughly km2 at mid latitudes) was removed. OpenStreetMap boundary relations sometimes contain a stray piece hundreds of kilometres away,
    and one such piece would turn a district into a 90,000 km2 'area'."""
    geom = shape(boundary)
    if geom.geom_type != "MultiPolygon":
        return boundary, 0.0
    parts = sorted(geom.geoms, key=lambda g: g.area, reverse=True)
    main = parts[0]
    kept = [p for p in parts if p.distance(main) <= STRAY_PART_DEGREES]
    dropped = [p for p in parts if p not in kept]
    if not dropped:
        return boundary, 0.0
    from shapely.geometry import MultiPolygon
    out = kept[0] if len(kept) == 1 else MultiPolygon(kept)
    km2 = sum(p.area for p in dropped) * 111.32 * 110.57 * math.cos(math.radians(main.centroid.y))
    return mapping(out), km2


@dataclass(frozen=True)
class Region:
    name: str
    bbox: tuple[float, float, float, float]  # west, south, east, north (degrees)
    display_name: str = ""
    osm_type: str = ""
    osm_id: int = 0
    attribution: str = ATTRIBUTION
    boundary: dict[str, Any] | None = None   # Polygon/MultiPolygon GeoJSON in WGS84, when the geocoder provided one

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any], check_area: bool = True) -> Region:
        """Rebuild a saved region. ``check_area=False`` skips the download-size limit (for commands that only read data already on disk)."""
        west, south, east, north = (float(v) for v in d["bbox"])
        return region_from_bbox(west, south, east, north, str(d.get("name", "region")), display_name=str(d.get("display_name", "")),
                                osm_type=str(d.get("osm_type", "")), osm_id=int(d.get("osm_id", 0) or 0),
                                boundary=clean_boundary(d.get("boundary"), MAX_BOUNDARY_BYTES * 2), check_area=check_area)

    @property
    def area_km2(self) -> float:
        """Area of the bounding box (this is what limits the size of a download)."""
        west, south, east, north = self.bbox
        mid = math.radians((south + north) / 2)
        return abs(east - west) * 111.32 * math.cos(mid) * abs(north - south) * 110.57

    @property
    def study_area_km2(self) -> float:
        """Area of the boundary if there is one, otherwise of the bounding box."""
        if self.boundary is None:
            return self.area_km2
        series = gpd.GeoSeries([shape(self.boundary)], crs=4326)
        return float(series.to_crs(series.estimate_utm_crs()).area.iloc[0] / 1e6)

    def boundary_frame(self) -> gpd.GeoDataFrame | None:
        """The study area as a one-feature layer (what density and coverage claims measure against), or None without a boundary."""
        if self.boundary is None:
            return None
        return gpd.GeoDataFrame({"name": [self.name]}, geometry=[shape(self.boundary)], crs=4326)

    @property
    def overpass_bbox(self) -> str:
        west, south, east, north = self.bbox
        return f"{south:.6f},{west:.6f},{north:.6f},{east:.6f}"

    def clip_shape(self, mode: str) -> BaseGeometry | None:
        """The shape that data is clipped to: 'boundary' (falls back to the box), 'bbox', or 'none'."""
        if mode == "none":
            return None
        if mode == "boundary" and self.boundary is not None:
            return shape(self.boundary)
        return box(*self.bbox)


def max_area_km2() -> float:
    return float(os.environ.get("STORYMAP_MAX_AREA_KM2", "150"))


def region_from_bbox(west: float, south: float, east: float, north: float, name: str = "region", check_area: bool = True,
                     **extra: Any) -> Region:
    vals = (west, south, east, north)
    if not all(math.isfinite(v) for v in vals):
        raise ValueError("the bounding box must be finite numbers")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("the bounding box must satisfy -180 <= west < east <= 180 and -90 <= south < north <= 90")
    region = Region(name=name[:200], bbox=vals, **extra)
    if check_area and region.area_km2 > max_area_km2():
        raise ValueError(f"the area is {region.area_km2:.0f} km2, above the limit of {max_area_km2():.0f} km2; "
                         "choose a neighbourhood or district instead of a whole city (STORYMAP_MAX_AREA_KM2 raises the limit)")
    return region


def _fetch(query: str) -> bytes:
    global _last_request
    key = "nominatim2:" + query.lower()
    hit = cache.get(key, CACHE_TTL)
    if hit is not None:
        return hit
    wait = 1.1 - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    body = net.request_bytes("GET", NOMINATIM, params={"q": query, "format": "jsonv2", "limit": 1, "addressdetails": 0,
                                                       "polygon_geojson": 1, "polygon_threshold": 0.0003},
                             allowed_hosts=NOMINATIM_HOSTS, max_bytes=5 << 20)
    cache.put(key, body)
    return body


def geocode(query: str) -> Region:
    """Resolve a place name (for example 'Park Slope, Brooklyn') to a Region."""
    query = query.strip()
    if not 2 <= len(query) <= 200 or re.search(r"[\x00-\x1f]", query):
        raise ValueError("the place name must be 2-200 characters without control characters")
    try:
        results = json.loads(_fetch(query))
    except json.JSONDecodeError:
        raise net.NetworkError("the geocoder returned an unreadable response") from None
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise ValueError(f"no place found for '{query}'; try a more specific name or pass --bbox")
    top = results[0]
    try:
        south, north, west, east = (float(v) for v in top["boundingbox"])
    except (KeyError, TypeError, ValueError):
        raise net.NetworkError("the geocoder result has no usable bounding box") from None
    boundary = clean_boundary(top.get("geojson"))
    if boundary is not None:
        boundary, stray_km2 = drop_stray_parts(boundary)
        if stray_km2:
            warnings.warn(f"the boundary of '{query}' had parts far from the main body ({stray_km2:.0f} km2); they were dropped", stacklevel=2)
            west, south, east, north = shape(boundary).bounds
    return region_from_bbox(west, south, east, north, query, display_name=str(top.get("display_name", ""))[:300],
                            osm_type=str(top.get("osm_type", "")), osm_id=int(top.get("osm_id", 0) or 0), boundary=boundary)


def geocode_many(queries: list[str], name: str | None = None) -> Region:
    """Resolve several places (for example the districts of a city) to one Region: the union of their boundaries.

    Every place needs a boundary; the combined bounding box must fit the area limit like any other region."""
    if not 1 <= len(queries) <= 12:
        raise ValueError("give between one and twelve place names")
    if len(queries) == 1 and name is None:
        return geocode(queries[0])
    parts = [geocode(q) for q in queries]
    if any(p.boundary is None for p in parts):
        missing = [p.name for p in parts if p.boundary is None]
        raise ValueError(f"no boundary was found for: {', '.join(missing)}; a combined area needs a boundary for each place")
    from shapely.ops import unary_union
    union = unary_union([shape(p.boundary) for p in parts if p.boundary])
    west = min(p.bbox[0] for p in parts)
    south = min(p.bbox[1] for p in parts)
    east = max(p.bbox[2] for p in parts)
    north = max(p.bbox[3] for p in parts)
    return region_from_bbox(west, south, east, north, name or ", ".join(queries),
                            display_name="; ".join(p.display_name for p in parts)[:300], boundary=clean_boundary(mapping(union)))
