"""Public ArcGIS feature services and GeoJSON links.

These take a URL chosen by the user, so every request goes through ``net`` (https only, public hosts only, no redirects,
size-capped). The MCP server does not expose these functions; only the command line does.
"""
from __future__ import annotations

import io
import json
import re
from typing import Any

import geopandas as gpd

from .. import net
from .geocode import Region

_SERVICE = re.compile(r"^https://[^?#\s]+/(FeatureServer|MapServer)/\d+/?$")
PAGE = 1000


def _check_service_url(url: str) -> str:
    if not _SERVICE.match(url):
        raise ValueError("expected the URL of a single layer, ending in /FeatureServer/<n> or /MapServer/<n>")
    return net.assert_public_https_url(url.rstrip("/"))


def fetch_service_layer(url: str, region: Region | None = None, where: str = "1=1", max_features: int = 50_000) -> gpd.GeoDataFrame:
    """Download a layer of a public ArcGIS feature service (optionally limited to a region) as WGS84 GeoJSON."""
    base = _check_service_url(url)
    if len(where) > 300 or re.search(r"[;\x00-\x1f]", where):
        raise ValueError("the where clause is too long or contains forbidden characters")
    params: dict[str, Any] = {"where": where, "outFields": "*", "outSR": 4326, "f": "geojson", "returnGeometry": "true",
                              "resultRecordCount": PAGE}
    if region is not None:
        west, south, east, north = region.bbox
        params.update({"geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                       "spatialRel": "esriSpatialRelIntersects"})
    features: list[dict[str, Any]] = []
    offset = 0
    while len(features) < max_features:
        body = net.request_bytes("GET", base + "/query", params={**params, "resultOffset": offset}, max_bytes=50 * 1024 * 1024)
        try:
            page = json.loads(body)
        except json.JSONDecodeError:
            raise net.NetworkError("the service returned something that is not JSON") from None
        if isinstance(page, dict) and "error" in page:
            raise net.NetworkError("the service reported an error for this query")
        got = page.get("features") if isinstance(page, dict) else None
        if not isinstance(got, list):
            raise net.NetworkError("the service returned an unexpected document")
        features += got
        if not got or not (page.get("exceededTransferLimit") or len(got) >= PAGE):
            break
        offset += len(got)
    if len(features) > max_features:
        features = features[:max_features]
    if not features:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=4326)
    return gpd.GeoDataFrame.from_features(features, crs=4326)


def fetch_geojson_url(url: str) -> gpd.GeoDataFrame:
    """Download a GeoJSON file from a public https URL."""
    if not re.search(r"\.(geo)?json$", url.split("?")[0], re.IGNORECASE):
        raise ValueError("expected a URL ending in .geojson or .json")
    gdf = gpd.read_file(io.BytesIO(net.request_bytes("GET", url)))
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    return gdf
