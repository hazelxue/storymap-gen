"""The data-acquisition layer, tested offline: a fake network, plus a real Overpass response recorded in fixtures/."""
import json
from pathlib import Path

import pytest

from storymap_gen import net
from storymap_gen.sources import arcgis, cache, geocode, overpass
from storymap_gen.sources.manifest import LayerRecord, Manifest, new_manifest, read_manifest, write_manifest
from tests.conftest import FIXTURES

PUBLIC_IP = "93.184.216.34"


def resolve_to(monkeypatch, ip):
    monkeypatch.setattr(net.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 443))])


class FakeResp:
    def __init__(self, status=200, body=b"{}", headers=None, chunk=1 << 16):
        self.status_code, self._body, self.headers, self._chunk = status, body, headers or {}, chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size=0):
        for i in range(0, len(self._body), self._chunk):
            yield self._body[i:i + self._chunk]


# ---- net: SSRF and resource limits ---------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "http://example.com/x", "ftp://example.com/x", "https://user:pw@example.com/x", "https://example.com:8443/x", "https:///x",
    "https://" + "a" * 2100 + ".com", "file:///etc/passwd", "javascript:alert(1)",
])
def test_non_https_or_odd_urls_are_refused(url, monkeypatch):
    resolve_to(monkeypatch, PUBLIC_IP)
    with pytest.raises(net.NetworkError):
        net.assert_public_https_url(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "172.16.0.1", "169.254.169.254", "::1", "fe80::1", "0.0.0.0", "224.0.0.1", "239.255.255.250", "::ffff:10.0.0.1", "::ffff:127.0.0.1", "::ffff:169.254.169.254",
                                "100.64.0.1", "240.0.0.1", "ff02::1"])
def test_hosts_that_resolve_to_private_addresses_are_refused(ip, monkeypatch):
    resolve_to(monkeypatch, ip)
    with pytest.raises(net.NetworkError, match="non-public"):
        net.assert_public_https_url("https://innocent-looking.example/x")


def test_public_hosts_and_allow_lists(monkeypatch):
    resolve_to(monkeypatch, PUBLIC_IP)
    assert net.assert_public_https_url("https://example.com/x")
    assert net.assert_public_https_url("https://api.example.com/x", allowed_hosts=["example.com"])
    with pytest.raises(net.NetworkError, match="allowed list"):
        net.assert_public_https_url("https://evil.com/x", allowed_hosts=["example.com"])
    with pytest.raises(net.NetworkError, match="allowed list"):
        net.assert_public_https_url("https://notexample.com/x", allowed_hosts=["example.com"])


def test_unresolvable_host_is_a_clean_error(monkeypatch):
    def boom(*a, **k):
        raise net.socket.gaierror("nope")
    monkeypatch.setattr(net.socket, "getaddrinfo", boom)
    with pytest.raises(net.NetworkError, match="resolve"):
        net.assert_public_https_url("https://does-not-exist.example/x")


def test_request_refuses_redirects_errors_and_oversized_bodies(monkeypatch):
    resolve_to(monkeypatch, PUBLIC_IP)
    seen = {}

    def fake(method, url, **kw):
        seen.update(kw)
        return fake.resp

    monkeypatch.setattr(net.requests, "request", fake)
    for status, text in [(301, "redirect"), (307, "redirect"), (404, "HTTP 404"), (500, "HTTP 500")]:
        fake.resp = FakeResp(status)
        with pytest.raises(net.NetworkError, match=text):
            net.request_bytes("GET", "https://example.com/x")
    assert seen["allow_redirects"] is False and seen["timeout"] == net.DEFAULT_TIMEOUT and seen["headers"]["User-Agent"].startswith("storymap-gen")
    fake.resp = FakeResp(200, b"x" * 10, {"Content-Length": "999999999"})
    with pytest.raises(net.NetworkError, match="larger"):
        net.request_bytes("GET", "https://example.com/x", max_bytes=1000)
    fake.resp = FakeResp(200, b"x" * 5000, {}, chunk=100)          # no Content-Length: the stream itself is capped
    with pytest.raises(net.NetworkError, match="larger"):
        net.request_bytes("GET", "https://example.com/x", max_bytes=1000)
    fake.resp = FakeResp(200, b"ok")
    assert net.request_bytes("GET", "https://example.com/x") == b"ok"


def test_network_exceptions_become_network_errors(monkeypatch):
    resolve_to(monkeypatch, PUBLIC_IP)

    def boom(*a, **k):
        raise net.requests.exceptions.ConnectTimeout("secret details https://x?token=SECRET")
    monkeypatch.setattr(net.requests, "request", boom)
    with pytest.raises(net.NetworkError) as e:
        net.request_bytes("GET", "https://example.com/x")
    assert "SECRET" not in str(e.value) and "ConnectTimeout" in str(e.value)


# ---- geocoding ---------------------------------------------------------------------------------------------------
NOMINATIM_HIT = json.dumps([{"display_name": "Park Slope, Brooklyn, New York, USA", "osm_type": "relation", "osm_id": 12345,
                             "boundingbox": ["40.6580", "40.6830", "-73.9930", "-73.9700"]}]).encode()


def test_geocode_reads_nominatim_bounding_box_in_the_right_order(monkeypatch):
    monkeypatch.setattr(geocode, "_fetch", lambda q: NOMINATIM_HIT)
    r = geocode.geocode("Park Slope, Brooklyn")
    assert r.bbox == (-73.993, 40.658, -73.970, 40.683) and r.osm_id == 12345 and 3 < r.area_km2 < 6
    assert r.overpass_bbox == "40.658000,-73.993000,40.683000,-73.970000"       # south,west,north,east
    assert "OpenStreetMap" in r.attribution


@pytest.mark.parametrize("body, error", [(b"[]", "no place found"), (b"not json", net.NetworkError), (b"{}", "no place found"),
                                         (json.dumps([{"display_name": "x"}]).encode(), net.NetworkError)])
def test_geocode_failures_are_clean(monkeypatch, body, error):
    monkeypatch.setattr(geocode, "_fetch", lambda q: body)
    with pytest.raises((ValueError, net.NetworkError), match=error if isinstance(error, str) else None):
        geocode.geocode("Nowhere")


@pytest.mark.parametrize("q", ["", "a", "x" * 201, "new\nline", "tab\there"])
def test_geocode_rejects_bad_queries_before_any_request(monkeypatch, q):
    monkeypatch.setattr(geocode, "_fetch", lambda q: pytest.fail("a request was made"))
    with pytest.raises(ValueError):
        geocode.geocode(q)


@pytest.mark.parametrize("box", [(2, 41, 1, 42), (-181, 0, 0, 1), (0, 91, 1, 92), (float("nan"), 0, 1, 1), (0, 0, 0, 1)])
def test_invalid_boxes_are_refused(box):
    with pytest.raises(ValueError):
        geocode.region_from_bbox(*box)


def test_a_whole_country_is_refused_with_advice():
    with pytest.raises(ValueError, match="neighbourhood or district"):
        geocode.region_from_bbox(-74.3, 40.4, -73.6, 41.0, "New York City")


def test_geocoder_results_are_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(net, "request_bytes", lambda *a, **k: calls.append(1) or NOMINATIM_HIT)
    monkeypatch.setattr(geocode.time, "sleep", lambda s: None)
    geocode.geocode("Park Slope, Brooklyn")
    geocode.geocode("park slope, brooklyn")
    assert len(calls) == 1


def test_region_round_trips_through_json():
    r = geocode.region_from_bbox(2.15, 41.38, 2.19, 41.40, "Eixample", display_name="d", osm_type="relation", osm_id=7)
    assert geocode.Region.from_dict(json.loads(json.dumps(r.to_dict()))) == r


# ---- overpass: parsing a real recorded response -----------------------------------------------------------------
REGION = geocode.region_from_bbox(-73.980, 40.655, -73.962, 40.672, "Prospect Park area")
PROSPECT = json.loads((FIXTURES / "overpass_parks_prospect_park.json").read_text(encoding="utf-8"))


def test_relations_become_polygons_with_the_right_area():
    gdf = overpass._to_frame(PROSPECT, overpass.LAYERS["parks"])
    big = gdf[gdf["name"] == "Prospect Park"].iloc[0]
    assert big.geometry.geom_type == "MultiPolygon" and big["osm_type"] == "relation"
    acres = gdf.to_crs(gdf.estimate_utm_crs()).geometry.area.sum() / 4046.8564224
    assert 450 < acres < 500 and len(gdf) == 4 and set(gdf.columns) >= {"name", "leisure", "geometry"}


def test_wrong_geometry_types_are_dropped_and_duplicates_collapse():
    data = {"elements": [dict(PROSPECT["elements"][0]), dict(PROSPECT["elements"][0])]}
    assert len(overpass._to_frame(data, overpass.LAYERS["parks"])) == 1
    assert len(overpass._to_frame(data, overpass.LAYERS["streets"])) == 0       # polygons are not streets


def test_colon_tags_map_to_plain_column_names():
    el = {"type": "way", "id": 1, "tags": {"highway": "residential", "cycleway:left": "lane", "name": "A St"},
          "geometry": [{"lat": 41.0, "lon": 2.0}, {"lat": 41.001, "lon": 2.001}]}
    gdf = overpass._to_frame({"elements": [el]}, overpass.LAYERS["bike"])
    assert gdf.iloc[0]["cycleway_left"] == "lane" and gdf.iloc[0]["name"] == "A St"


def test_polygons_can_be_reduced_to_points():
    gdf = overpass._to_frame(PROSPECT, overpass.LAYERS["schools"].__class__(
        **{**overpass.LAYERS["schools"].__dict__, "keep_tags": ("amenity",)}))
    assert set(gdf.geometry.geom_type) == {"Point"} and len(gdf) == 4


def test_oversized_results_are_refused(monkeypatch):
    monkeypatch.setattr(overpass, "MAX_ELEMENTS", 2)
    with pytest.raises(net.NetworkError, match="more than 2 elements"):
        overpass._to_frame({"elements": [{}, {}, {}]}, overpass.LAYERS["parks"])


def test_unknown_layers_and_oversized_areas_are_refused():
    with pytest.raises(ValueError, match="unknown layer"):
        overpass.fetch_layer("nuclear_plants", REGION)
    big = geocode.region_from_bbox(-74.05, 40.70, -73.90, 40.80, "big")
    with pytest.raises(ValueError, match="limited to areas up to"):
        overpass.fetch_layer("buildings", big)


def test_query_is_well_formed():
    q = overpass.build_query(overpass.LAYERS["parks"], REGION)
    assert q.startswith("[out:json][timeout:90]") and "40.655000,-73.980000,40.672000,-73.962000" in q and q.rstrip().endswith("out geom;")
    assert "{" not in q.replace("{bb}", "").split("\n", 1)[1]                      # every placeholder was filled


# ---- overpass: a flaky public service ---------------------------------------------------------------------------
@pytest.fixture
def mirrors(monkeypatch):
    monkeypatch.setenv("STORYMAP_OVERPASS_MIRRORS", "https://a.example/api,https://b.example/api")
    resolve_to(monkeypatch, PUBLIC_IP)


def script(monkeypatch, answers):
    calls, sleeps = [], []

    def fake(method, url, **kw):
        calls.append(url)
        a = answers[min(len(calls) - 1, len(answers) - 1)]
        return a if isinstance(a, FakeResp) else FakeResp(200, json.dumps(a).encode())

    monkeypatch.setattr(net.requests, "request", fake)
    return calls, sleeps


def test_next_mirror_is_tried_when_the_first_is_overloaded(mirrors, monkeypatch):
    calls, _ = script(monkeypatch, [FakeResp(504), PROSPECT])
    gdf = overpass.fetch_layer("parks", REGION, use_cache=False, sleep=lambda s: None)
    assert [c.split("/")[2] for c in calls] == ["a.example", "b.example"] and len(gdf) == 4


def test_partial_results_are_never_used(mirrors, monkeypatch):
    partial = {"remark": "runtime error: Query timed out in \"query\" at line 3 after 90 seconds.", "elements": PROSPECT["elements"][:1]}
    calls, _ = script(monkeypatch, [partial])
    sleeps = []
    with pytest.raises(net.NetworkError, match="partial result"):
        overpass.fetch_layer("parks", REGION, use_cache=False, sleep=sleeps.append)
    assert len(calls) == 2 * (len(overpass.BACKOFF) + 1) and sleeps == list(overpass.BACKOFF)


def test_garbage_from_every_server_gives_actionable_advice(mirrors, monkeypatch):
    script(monkeypatch, [FakeResp(200, b"<html>busy</html>"), FakeResp(200, b'{"elements": "no"}')])
    with pytest.raises(net.NetworkError, match="--data"):
        overpass.fetch_layer("parks", REGION, use_cache=False, sleep=lambda s: None)


def test_results_are_cached_between_runs(mirrors, monkeypatch):
    calls, _ = script(monkeypatch, [PROSPECT])
    overpass.fetch_layer("parks", REGION, sleep=lambda s: None)
    overpass.fetch_layer("parks", REGION, sleep=lambda s: None)
    assert len(calls) == 1
    overpass.fetch_layer("parks", REGION, use_cache=False, sleep=lambda s: None)
    assert len(calls) == 2


def test_cache_directory_is_isolated_for_tests():
    assert "state" in str(cache.cache_dir())


def test_mirror_hosts_must_be_public(monkeypatch):
    monkeypatch.setenv("STORYMAP_OVERPASS_MIRRORS", "https://internal.example/api")
    resolve_to(monkeypatch, "10.0.0.1")
    with pytest.raises(net.NetworkError):
        overpass.fetch_layer("parks", REGION, use_cache=False, sleep=lambda s: None)


# ---- ArcGIS services and GeoJSON links ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", ["http://x.example/arcgis/rest/services/a/FeatureServer/0", "https://x.example/a/FeatureServer",
                                 "https://x.example/a/FeatureServer/0?f=json", "https://x.example/a/FeatureServer/0/query",
                                 "https://x.example/a/FeatureServer/zero", "file:///x/FeatureServer/0", "https://x.example/a/Other/0"])
def test_only_single_layer_urls_are_accepted(url, monkeypatch):
    resolve_to(monkeypatch, PUBLIC_IP)
    with pytest.raises(ValueError):
        arcgis.fetch_service_layer(url)


def test_internal_services_are_refused(monkeypatch):
    resolve_to(monkeypatch, "192.168.0.7")
    with pytest.raises(net.NetworkError):
        arcgis.fetch_service_layer("https://intranet.example/arcgis/rest/services/a/FeatureServer/0")


@pytest.mark.parametrize("where", ["1=1; DROP TABLE x", "x" * 301, "a\x00b"])
def test_dangerous_where_clauses_are_refused(where, monkeypatch):
    resolve_to(monkeypatch, PUBLIC_IP)
    with pytest.raises(ValueError):
        arcgis.fetch_service_layer("https://x.example/a/FeatureServer/0", where=where)


def test_pagination_collects_every_page_and_caps_the_total(monkeypatch):
    resolve_to(monkeypatch, PUBLIC_IP)
    pages = [{"features": [{"type": "Feature", "properties": {"i": i}, "geometry": {"type": "Point", "coordinates": [2.1, 41.3]}}
                           for i in range(start, start + 1000)], "exceededTransferLimit": True} for start in (0, 1000)]
    pages.append({"features": [{"type": "Feature", "properties": {"i": 2000}, "geometry": {"type": "Point", "coordinates": [2.1, 41.3]}}]})
    seen = []

    def fake(method, url, **kw):
        seen.append(kw["params"]["resultOffset"])
        return json.dumps(pages[len(seen) - 1]).encode()

    monkeypatch.setattr(net, "request_bytes", fake)
    gdf = arcgis.fetch_service_layer("https://x.example/a/FeatureServer/0", REGION)
    assert len(gdf) == 2001 and seen == [0, 1000, 2000]
    seen.clear()
    assert len(arcgis.fetch_service_layer("https://x.example/a/FeatureServer/0", max_features=1500)) == 1500


def test_service_errors_and_garbage_are_clean(monkeypatch):
    resolve_to(monkeypatch, PUBLIC_IP)
    for body in (b'{"error": {"code": 400}}', b"not json", b'{"features": "x"}'):
        monkeypatch.setattr(net, "request_bytes", lambda *a, _b=body, **k: _b)
        with pytest.raises(net.NetworkError):
            arcgis.fetch_service_layer("https://x.example/a/FeatureServer/0")


def test_geojson_links_must_look_like_geojson(monkeypatch):
    resolve_to(monkeypatch, PUBLIC_IP)
    with pytest.raises(ValueError):
        arcgis.fetch_geojson_url("https://x.example/data.zip")
    monkeypatch.setattr(net, "request_bytes", lambda *a, **k: (FIXTURES / "subway_stations.geojson").read_bytes())
    assert len(arcgis.fetch_geojson_url("https://x.example/stations.geojson?x=1")) == 5


# ---- manifest ---------------------------------------------------------------------------------------------------
def test_manifest_round_trip_and_source_list(tmp_path):
    m = new_manifest(REGION)
    m.layers += [LayerRecord("parks", "parks.geojson", 4, "Polygon", "osm", overpass.ATTRIBUTION, "ODbL 1.0"),
                 LayerRecord("streets", "streets.geojson", 9, "LineString", "osm", overpass.ATTRIBUTION, "ODbL 1.0"),
                 LayerRecord("x", "x.geojson", 1, "Point", "arcgis-h", "ArcGIS service at h", "see provider")]
    path = write_manifest(m, tmp_path)
    back = read_manifest(path)
    assert back.layers == m.layers and back.data_paths(tmp_path)["parks"].endswith("parks.geojson")
    assert [s["key"] for s in back.sources()] == ["osm", "arcgis-h"]


@pytest.mark.parametrize("edit", [
    lambda d: d["layers"][0].update(file="../../etc/passwd"), lambda d: d["layers"][0].update(file="a\\b.geojson"),
    lambda d: d["layers"][0].update(name="bad name!"), lambda d: d["layers"][0].update(file=".hidden"),
    lambda d: d.pop("layers"), lambda d: d["layers"][0].pop("features"),
])
def test_unsafe_or_broken_manifests_are_refused(tmp_path, edit):
    m = new_manifest(REGION)
    m.layers.append(LayerRecord("parks", "parks.geojson", 4, "Polygon", "osm", "a", "b"))
    path = write_manifest(m, tmp_path)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    edit(data)
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        read_manifest(path)


def test_manifest_type_is_exported():
    assert Manifest is not None


# ---- clipping to the study area (found with real data: a nature reserve inflated a district's park area 10x) ---------
def _square_region(boundary=True):
    from shapely.geometry import box, mapping
    b = mapping(box(2.00, 41.00, 2.02, 41.02)) if boundary else None
    return geocode.region_from_bbox(2.00, 41.00, 2.02, 41.02, "Test", boundary=json.loads(json.dumps(b)) if b else None)


def _layer_frame(kind):
    import geopandas as gpd
    from shapely.geometry import LineString, Point, Polygon
    geoms = {"parks": [Polygon([(1.9, 40.9), (2.3, 40.9), (2.3, 41.3), (1.9, 41.3)]),      # a huge reserve covering the whole box
                       Polygon([(2.005, 41.005), (2.01, 41.005), (2.01, 41.01), (2.005, 41.01)]),
                       Polygon([(2.5, 41.5), (2.6, 41.5), (2.6, 41.6), (2.5, 41.6)])],          # entirely outside
             "streets": [LineString([(1.9, 41.01), (2.1, 41.01)]), LineString([(2.5, 41.5), (2.6, 41.5)])],
             "transit": [Point(2.01, 41.01), Point(2.5, 41.5)]}[kind]
    return gpd.GeoDataFrame({"name": [f"f{i}" for i in range(len(geoms))]}, geometry=geoms, crs=4326)


def test_a_huge_polygon_is_cut_to_the_study_area():
    region = _square_region()
    gdf = overpass.clip_to_region(_layer_frame("parks"), overpass.LAYERS["parks"], region)
    area_km2 = gdf.to_crs(gdf.estimate_utm_crs()).geometry.union_all().area / 1e6      # overlaps merged, as area claims do
    assert len(gdf) == 2 and area_km2 == pytest.approx(region.area_km2, rel=0.03)         # not the 1,600 km2 of the reserve
    assert set(gdf["name"]) == {"f0", "f1"}


def test_lines_are_cut_and_points_outside_are_dropped():
    region = _square_region()
    streets = overpass.clip_to_region(_layer_frame("streets"), overpass.LAYERS["streets"], region)
    assert len(streets) == 1 and streets.total_bounds[0] >= 2.0 - 1e-9 and streets.total_bounds[2] <= 2.02 + 1e-9
    transit = overpass.clip_to_region(_layer_frame("transit"), overpass.LAYERS["transit"], region)
    assert list(transit["name"]) == ["f0"]


def test_clip_modes_and_the_fallback_to_the_box():
    gdf = _layer_frame("parks")
    parks = overpass.LAYERS["parks"]
    assert len(overpass.clip_to_region(gdf, parks, _square_region(), "none")) == 3
    no_boundary = _square_region(boundary=False)
    assert len(overpass.clip_to_region(gdf, parks, no_boundary, "boundary")) == 2          # no boundary: the box is used
    assert len(overpass.clip_to_region(gdf, parks, _square_region(), "bbox")) == 2
    with pytest.raises(ValueError, match="clip must be"):
        overpass.fetch_layer("parks", _square_region(), clip="everything")


def test_clipping_survives_invalid_geometry_and_empty_frames():
    import geopandas as gpd
    from shapely.geometry import Polygon
    bowtie = Polygon([(2.0, 41.0), (2.01, 41.01), (2.01, 41.0), (2.0, 41.01)])               # self-intersecting
    gdf = gpd.GeoDataFrame({"name": ["b"]}, geometry=[bowtie], crs=4326)
    assert len(overpass.clip_to_region(gdf, overpass.LAYERS["parks"], _square_region())) == 1
    empty = gpd.GeoDataFrame({"name": []}, geometry=gpd.GeoSeries([], crs=4326))
    assert overpass.clip_to_region(empty, overpass.LAYERS["parks"], _square_region()).empty


def test_boundaries_are_validated_repaired_and_size_limited():
    from shapely.geometry import Point as P
    from shapely.geometry import mapping
    assert geocode.clean_boundary({"type": "Point", "coordinates": [0, 0]}) is None
    assert geocode.clean_boundary("nope") is None and geocode.clean_boundary(None) is None
    assert geocode.clean_boundary({"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]})   # a bow-tie is repaired
    assert geocode.clean_boundary({"type": "Polygon", "coordinates": [[[200, 0], [201, 0], [201, 1], [200, 0]]]}) is None   # not lon/lat
    circle = mapping(P(2.0, 41.0).buffer(0.01, quad_segs=2000))                                 # ~16,000 vertices
    small = geocode.clean_boundary(circle, limit=20_000)
    assert small is not None and len(json.dumps(small)) <= 20_000


def test_region_keeps_its_boundary_through_json_and_reports_its_area():
    region = _square_region()
    again = geocode.Region.from_dict(json.loads(json.dumps(region.to_dict())))
    assert again.boundary is not None and again.study_area_km2 == pytest.approx(region.area_km2, rel=0.03)
    assert _square_region(boundary=False).study_area_km2 == _square_region(boundary=False).area_km2
