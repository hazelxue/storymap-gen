"""From a place and a topic to a published draft, offline: webmap generation, profiling, scaffolding, login, publishing."""
import copy
import json
import re
import time

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon

from storymap_gen import api, auth, publish
from storymap_gen.__main__ import main
from storymap_gen.compiler import compile_story, placeholder_item_id
from storymap_gen.describe import profile_layer, profile_store, render_markdown
from storymap_gen.evaluate import check_graph
from storymap_gen.geo import DataStore
from storymap_gen.scaffold import parse_outline, scaffold_spec
from storymap_gen.sources import overpass
from storymap_gen.sources.geocode import region_from_bbox
from storymap_gen.spec import StorySpec
from storymap_gen.webmap import build_webmap, mercator_to_lonlat
from tests.conftest import FIXTURES


def frame(name):
    return gpd.read_file(FIXTURES / name)


# ---- web map generation ------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def nyc_layers():
    return {"streets": frame("streets_centerline.geojson"), "parks": frame("parks.geojson"), "transit": frame("subway_stations.geojson")}


def ring_area(ring):
    return sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(len(ring) - 1)) / 2


def test_webmap_has_the_structure_the_map_viewer_expects(nyc_layers):
    wm, box = build_webmap(nyc_layers, roles={"streets": "line", "parks": "park", "transit": "transit"})
    assert set(wm) >= {"operationalLayers", "baseMap", "spatialReference", "version", "initialState"}
    types = [lay["featureCollection"]["layers"][0]["featureSet"]["geometryType"] for lay in wm["operationalLayers"]]
    assert types == ["esriGeometryPolygon", "esriGeometryPolyline", "esriGeometryPoint"]       # drawn bottom to top
    for layer in wm["operationalLayers"]:
        assert layer["layerType"] == "ArcGISFeatureLayer" and layer["visibility"] is True
        fc = layer["featureCollection"]["layers"][0]
        assert fc["layerDefinition"]["objectIdField"] == "OBJECTID" and fc["layerDefinition"]["drawingInfo"]["renderer"]["symbol"]
        ids = [f["attributes"]["OBJECTID"] for f in fc["featureSet"]["features"]]
        assert ids == sorted(set(ids)) and fc["popupInfo"]["fieldInfos"]
    xmin, ymin, xmax, ymax = box
    assert xmin < xmax and ymin < ymax
    lon0, lat0, lon1, lat1 = mercator_to_lonlat(box)
    assert -73.91 < lon0 < -73.89 and 40.74 < lat0 < 40.75 and lon1 > lon0 and lat1 > lat0


def test_polygon_rings_follow_the_esri_orientation_convention(nyc_layers):
    wm, _ = build_webmap({"parks": nyc_layers["parks"]})
    for f in wm["operationalLayers"][0]["featureCollection"]["layers"][0]["featureSet"]["features"]:
        rings = f["geometry"]["rings"]
        assert ring_area(rings[0]) < 0                       # exterior ring is clockwise


def test_holes_are_counter_clockwise():
    donut = Polygon([(2.0, 41.0), (2.01, 41.0), (2.01, 41.01), (2.0, 41.01)], [[(2.003, 41.003), (2.007, 41.003), (2.007, 41.007), (2.003, 41.007)]])
    wm, _ = build_webmap({"d": gpd.GeoDataFrame({"name": ["x"]}, geometry=[donut], crs=4326)})
    rings = wm["operationalLayers"][0]["featureCollection"]["layers"][0]["featureSet"]["features"][0]["geometry"]["rings"]
    assert len(rings) == 2 and ring_area(rings[0]) < 0 < ring_area(rings[1])


def test_attributes_are_clean_json():
    gdf = gpd.GeoDataFrame({"n": [1.5, float("nan")], "s": ["a" * 400, None], "b": [True, False], "i": [1, 2]},
                           geometry=[Point(2.1, 41.3), Point(2.2, 41.4)], crs=4326)
    wm, _ = build_webmap({"p": gdf})
    feats = wm["operationalLayers"][0]["featureCollection"]["layers"][0]["featureSet"]["features"]
    assert json.loads(json.dumps(feats))                      # strict JSON: no NaN
    assert feats[1]["attributes"]["n"] is None and feats[0]["attributes"]["s"] == "a" * 255 and feats[0]["attributes"]["b"] == "true"


def test_mixed_and_empty_inputs():
    mixed = gpd.GeoDataFrame({"name": ["a", "b", "c"]}, geometry=[Point(2.1, 41.3), LineString([(2.1, 41.3), (2.2, 41.4)]), None], crs=4326)
    wm, _ = build_webmap({"m": mixed})
    assert len(wm["operationalLayers"]) == 1                  # one geometry type per layer; unusable rows are skipped
    with pytest.raises(ValueError, match="drawable"):
        build_webmap({"e": gpd.GeoDataFrame({"name": []}, geometry=gpd.GeoSeries([], crs=4326))})


def test_size_limits_protect_the_item(nyc_layers, monkeypatch):
    monkeypatch.setenv("STORYMAP_MAX_WEBMAP_MB", "0.01")
    with pytest.raises(ValueError, match="larger than"):
        build_webmap(nyc_layers)
    monkeypatch.undo()
    many = gpd.GeoDataFrame({"i": range(20_001)}, geometry=[Point(2.1 + i * 1e-6, 41.3) for i in range(20_001)], crs=4326)
    with pytest.raises(ValueError, match="at most"):
        build_webmap({"many": many})


def test_webmap_item_is_private_and_carries_the_extent(nyc_layers):
    wm, box = build_webmap(nyc_layers)
    p = publish.build_webmap_item_params(wm, "T", mercator_to_lonlat(box))
    assert p["access"] == "private" and p["type"] == "Web Map" and len(p["extent"].split(",")) == 4 and json.loads(p["text"]) == wm
    assert "token" not in p


# ---- profiling ----------------------------------------------------------------------------------------------------
def test_profile_reports_measured_numbers_and_fitting_claim_types(nyc_layers):
    p = profile_layer("streets", nyc_layers["streets"])
    assert p["features"] == 1001 and 98 < p["total_length_km"] < 99.5 and p["fields"]["st_width"]["min"] == 6
    assert any("street_orientation" in h for h in p["claim_types"]) and any("layer_length" in h for h in p["claim_types"])
    parks = profile_layer("parks", nyc_layers["parks"])
    assert parks["total_area_acres_dissolved"] == pytest.approx(24.67, abs=0.05) and any("layer_area" in h for h in parks["claim_types"])


def test_profile_markdown_and_degenerate_layers():
    store = DataStore({"empty": gpd.GeoDataFrame({"a": []}, geometry=gpd.GeoSeries([], crs=4326)),
                       "nulls": gpd.GeoDataFrame({"a": [1, 2]}, geometry=[None, None], crs=4326),
                       "pts": gpd.GeoDataFrame({"a": ["x", None]}, geometry=[Point(2.1, 41.3), Point(2.2, 41.4)], crs=4326)})
    md = render_markdown(profile_store(store))
    assert "## empty  (0 features)" in md and "## pts  (2 features)" in md and "1/2 filled" in md


# ---- scaffolding ----------------------------------------------------------------------------------------------------
OUTLINE = """# Title ignored? no, a heading is a section
## Housing and streets
- Where parks are
   - a sub point that must not become a section
1. Transit access
2) Conclusions
plain text line
"""


def test_outline_parsing():
    titles = parse_outline(OUTLINE)
    assert titles[1:] == ["Housing and streets", "Where parks are", "Transit access", "Conclusions", "plain text line"]   # plain lines count too
    assert "sub point" not in " ".join(titles)
    assert len(parse_outline("\n".join(f"- s{i}" for i in range(100)))) == 12


def test_scaffold_is_a_valid_but_unfinished_spec():
    region = region_from_bbox(2.15, 41.38, 2.19, 41.40, "Eixample")
    spec = scaffold_spec(region, "Streets and green space", None, None, "longform", framework="brief")
    parsed = StorySpec.model_validate(spec)
    assert len(parsed.sections) == 4 and "TODO" in spec["sections"][0]["intro"]
    assert scaffold_spec(region, "t", None, None, "docked-sidecar")["template"] == "longform"   # no layers -> no maps -> longform


def test_the_gate_refuses_an_unfinished_scaffold(jh_store):
    region = region_from_bbox(-73.9, 40.74, -73.87, 40.77, "JH")
    spec = StorySpec.model_validate(scaffold_spec(region, "t", None, None, "longform"))
    r = api.build(spec, jh_store)
    assert not r.passed and r.story is None and r.report["placeholders"]


# ---- login ---------------------------------------------------------------------------------------------------------
def test_token_parsing_accepts_urls_fragments_and_raw_tokens():
    url = "https://localhost:8765/storymap-gen-callback#access_token=abcDEF123.456-gh_i&expires_in=1800&username=x&ssl=true"
    assert auth.parse_token(url) == ("abcDEF123.456-gh_i", 1800)
    assert auth.parse_token("access_token=zzz999&expires_in=60") == ("zzz999", 60)
    assert auth.parse_token("  raw-token-1234567890  ") == ("raw-token-1234567890", None)
    for bad in ("", "a b", "access_token=", "x" * 5000):
        with pytest.raises(ValueError):
            auth.parse_token(bad)


def test_authorize_url_is_built_only_for_arcgis_portals():
    assert auth.authorize_url("abcdEFGH12345678").startswith("https://www.arcgis.com/sharing/rest/oauth2/authorize?client_id=abcdEFGH12345678&")
    with pytest.raises(ValueError):
        auth.authorize_url("abcdEFGH12345678", portal="https://evil.com/sharing/rest")
    for bad in ("", "short", "has space 12345", "x" * 100, "a;b&c=d12345"):
        with pytest.raises(ValueError):
            auth.authorize_url(bad)


def test_saved_tokens_expire_are_portal_bound_and_can_be_deleted():
    auth.save_token("tok-1234567890", "hazel", auth.DEFAULT_PORTAL, 3600)
    assert auth.load_token()["username"] == "hazel"
    assert auth.load_token("https://other.maps.arcgis.com/sharing/rest") is None            # bound to its portal
    assert auth.load_token("https://evil.com/sharing/rest") is None
    auth.save_token("tok-1234567890", "hazel", auth.DEFAULT_PORTAL, 10)                       # about to expire
    assert auth.load_token() is None
    auth.save_token("tok-1234567890", "hazel", auth.DEFAULT_PORTAL, 3600)
    assert auth.delete_token() is True and auth.load_token() is None and auth.delete_token() is False


def test_corrupt_token_files_are_ignored():
    auth.token_file().parent.mkdir(parents=True, exist_ok=True)
    for junk in ("", "not json", "[]", '{"token": "x"}', '{"token": "has space", "username": "u", "portal": "https://www.arcgis.com/sharing/rest", "expires_at": 9e12}'):
        auth.token_file().write_text(junk, encoding="utf-8")
        assert auth.load_token() is None


def test_credential_resolution_order(monkeypatch):
    with pytest.raises(ValueError, match="not signed in"):
        auth.resolve_credentials()
    monkeypatch.setenv("ARCGIS_TOKEN", "env-token-1234567890")
    with pytest.raises(ValueError, match="--username"):
        auth.resolve_credentials()
    assert auth.resolve_credentials(username="bob") == ("env-token-1234567890", "bob")
    monkeypatch.delenv("ARCGIS_TOKEN")
    auth.save_token("saved-token-123456", "hazel", auth.DEFAULT_PORTAL, 3600)
    assert auth.resolve_credentials() == ("saved-token-123456", "hazel")


def test_verify_token_uses_the_validated_portal(monkeypatch):
    seen = {}
    monkeypatch.setattr(auth, "_post", lambda url, **k: seen.update(url=url, data=k["data"]) or {"username": "hazel", "fullName": "H"})
    assert auth.verify_token("tok-1234567890")["username"] == "hazel" and seen["url"].startswith("https://www.arcgis.com/sharing/rest/community")
    monkeypatch.setattr(auth, "_post", lambda *a, **k: {})
    with pytest.raises(RuntimeError, match="did not recognise"):
        auth.verify_token("tok-1234567890")
    with pytest.raises(ValueError):
        auth.verify_token("tok-1234567890", portal="https://evil.com/sharing/rest")


def test_login_needs_a_client_id_and_a_terminal(capsys, monkeypatch):
    assert main(["login"]) == 2 and "client id" in capsys.readouterr().err.lower()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert main(["login", "--manual", "--client-id", "abcdEFGH12345678", "--no-browser"]) == 2
    out = capsys.readouterr()
    assert "oauth2/authorize" in out.out and "interactive terminal" in out.err


def test_login_flow_reads_a_hidden_prompt_and_saves_the_token(capsys, monkeypatch):
    import storymap_gen.__main__ as cli
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "https://localhost:8765/x#access_token=tok1234567890&expires_in=1800")
    monkeypatch.setattr(cli.auth, "verify_token", lambda token, portal: {"username": "hazel", "fullName": ""})
    assert main(["login", "--manual", "--client-id", "abcdEFGH12345678", "--no-browser"]) == 0
    printed = capsys.readouterr().out
    assert "Signed in as hazel" in printed and "tok1234567890" not in printed        # the token is never echoed
    assert auth.load_token()["username"] == "hazel"
    assert main(["logout"]) == 0 and auth.load_token() is None


# ---- the whole journey, offline ----------------------------------------------------------------------------------
@pytest.fixture
def fake_osm(monkeypatch):
    source = {"streets": frame("streets_centerline.geojson"), "parks": frame("parks.geojson"), "transit": frame("subway_stations.geojson")}

    def fetch(name, region, use_cache=True, clip="boundary", sleep=None):
        if name == "water":
            raise overpass.net.NetworkError("all Overpass servers are busy")
        if name not in source:
            raise ValueError(f"unknown layer '{name}'")
        return source[name]

    monkeypatch.setattr(overpass, "fetch_layer", fetch)


def run(capsys, *argv):
    code = main(list(map(str, argv)))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_place_and_topic_to_private_draft(tmp_path, capsys, fake_osm, monkeypatch):
    region, data, spec_path, story = tmp_path / "region.json", tmp_path / "data", tmp_path / "spec.json", tmp_path / "story.json"
    code, out, _ = run(capsys, "region", "Jackson Heights test", "--bbox", -73.90, 40.74, -73.87, 40.77, "--out", region)
    assert code == 0 and "km2" in out and region.exists()

    code, out, err = run(capsys, "fetch", "--region", region, "--layers", "streets", "parks", "transit", "water", "--out", data)
    assert code == 1 and "FAILED water" in err and "streets: 1001 features" in out         # one failed layer does not lose the rest
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    assert [rec["name"] for rec in manifest["layers"]] == ["streets", "parks", "transit"] and manifest["layers"][0]["source"] == "osm"

    code, out, _ = run(capsys, "describe", "--manifest", data / "manifest.json")
    assert code == 0 and "## streets  (1001 features)" in out and "layer_length" in out

    outline = tmp_path / "outline.md"
    outline.write_text("# Streets and green space\n- The street grid\n- Parks\n- Conclusion\n", encoding="utf-8")
    code, out, _ = run(capsys, "init", "--region", region, "--topic", "Streets and parks in Jackson Heights", "--manifest",
                       data / "manifest.json", "--outline", outline, "--out", spec_path)
    assert code == 0 and "TODO" in spec_path.read_text(encoding="utf-8")
    assert run(capsys, "init", "--region", region, "--topic", "x", "--out", spec_path)[0] == 2        # never overwrites silently

    code, out, err = run(capsys, "run", spec_path, "--manifest", data / "manifest.json", "--out", story)
    assert code == 1 and "PLACEHOLDER" in out and not story.exists()                                 # unfinished text cannot be compiled

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["byline"] = "Author"
    spec["claims"] = [{"kind": "layer_stat", "id": "n-streets", "text": "1,001 street segments", "layer": "streets", "agg": "count", "expected": 1001},
                      {"kind": "layer_area", "id": "park-area", "text": "park area", "layer": "parks", "unit": "acres", "expected": 24.67, "tolerance": 0.05}]
    for sec in spec["sections"]:
        sec["intro"] = "This section sets up the question."
        sec["claim_ids"] = ["n-streets", "park-area"]
        for b in sec["blocks"]:
            if b["type"] == "paragraph":
                b["text"] = "The area has 1,001 street segments and 24.7 acres of parkland."
            elif b["type"] == "map":
                b["map"]["caption"] = "Streets and parks."
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    code, out, err = run(capsys, "run", spec_path, "--manifest", data / "manifest.json", "--out", story)
    assert code == 0 and "all backed" in out and "Quality gate passed" in out, out + err
    compiled = json.loads(story.read_text(encoding="utf-8"))
    assert check_graph(compiled) == [] and placeholder_item_id("gen-streets-parks-transit") in json.dumps(compiled)

    created = {"maps": [], "stories": []}
    monkeypatch.setattr(api, "publish_web_map", lambda wm, title, user, token, ext, summary="", sharing="": created["maps"].append((title, wm)) or "a" * 32)
    monkeypatch.setattr(api, "publish_draft", lambda story_json, title, user, token, summary="", sharing="", pictures=(): created["stories"].append(story_json) or "b" * 32)
    monkeypatch.setenv("ARCGIS_TOKEN", "env-token-1234567890")
    code, out, err = run(capsys, "publish-spec", spec_path, "--manifest", data / "manifest.json", "--username", "hazel", "--title", "My story")
    assert code == 0 and "stories/" + "b" * 32 + "/edit" in out and "private" in out.lower(), out + err
    assert len(created["maps"]) == 4 and len(created["stories"]) == 1                               # one Web Map per distinct set of layers
    final = json.dumps(created["stories"][0])
    assert "a" * 32 in final and "placeholder" not in final and not re.search(r"r-[0-9a-f]{32}", final.replace("a" * 32, ""))  # real ids replaced the placeholders
    titles = {tuple(lay["title"] for lay in wm["operationalLayers"]) for _, wm in created["maps"]}
    assert ("Streets",) in titles and ("Parks and open space",) in titles and ("Parks and open space", "Streets") in titles


def test_publish_spec_refuses_before_creating_anything_when_the_gate_fails(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(api, "publish_web_map", lambda *a, **k: pytest.fail("created a map"))
    monkeypatch.setattr(api, "publish_draft", lambda *a, **k: pytest.fail("created a draft"))
    monkeypatch.setenv("ARCGIS_TOKEN", "env-token-1234567890")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"title": "t", "template": "longform", "sections": [{"title": "s", "intro": "TODO"}]}), encoding="utf-8")
    assert run(capsys, "publish-spec", spec, "--username", "hazel")[0] == 1


def test_only_generated_maps_are_created_and_existing_ones_are_left_alone(jh_store, monkeypatch):
    spec = StorySpec.model_validate({"title": "t", "template": "docked-sidecar", "sections": [{"title": "s", "slides": [
        {"title": "a", "body": "b", "map": {"item_id": "c" * 32}},
        {"title": "c", "body": "d", "map": {"layers": ["parks_"], "caption": "x"}},
        {"title": "e", "body": "f", "map": {"layers": ["parks_"], "caption": "y"}}]}]})
    made = []
    monkeypatch.setattr(api, "publish_web_map", lambda wm, title, *a, **k: made.append(title) or "d" * 32)
    monkeypatch.setattr(api, "publish_draft", lambda story, *a, **k: "e" * 32)
    result = api.publish_spec(spec, jh_store, title="t", username="u", token="tok1234567890", portal=auth.DEFAULT_PORTAL)
    assert len(made) == 1 and set(result.map_ids) == {"gen-parks_"} and result.story_id == "e" * 32


def test_publish_spec_reports_missing_layers(jh_store):
    spec = StorySpec.model_validate({"title": "t", "template": "docked-sidecar", "sections": [{"title": "s", "slides": [
        {"title": "a", "body": "b", "map": {"layers": ["nope"]}}]}]})
    with pytest.raises(ValueError, match="not loaded"):
        api.publish_spec(spec, jh_store, title="t", username="u", token="tok1234567890", portal=auth.DEFAULT_PORTAL)


def test_a_map_needs_exactly_one_source():
    for bad in ({}, {"item_id": "a" * 32, "layers": ["x"]}, {"layers": ["x"] * 13}):
        with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
            StorySpec.model_validate({"title": "t", "template": "longform", "sections": [{"title": "s", "slides": [
                {"title": "a", "body": "b", "map": bad}]}]})


def test_compiled_generated_maps_pass_the_graph_check_for_every_layout(jh_store):
    for template in ("docked-sidecar", "floating-sidecar", "longform"):
        spec = StorySpec.model_validate({"title": "t", "template": template, "sections": [{"title": "s", "slides": [
            {"title": "a", "body": "b", "map": {"layers": ["parks_", "subway_stations_"], "extent": [-8230000, 4970000, -8225000, 4975000]}}]}]})
        assert check_graph(compile_story(spec, {"gen-parks_-subway_stations_": "f" * 32})) == []


def test_webmap_generation_scales(nyc_layers):
    t0 = time.perf_counter()
    build_webmap(nyc_layers)
    assert time.perf_counter() - t0 < 15


def test_unused_helpers_keep_a_stable_surface():
    assert copy.deepcopy(overpass.LAYERS)["streets"].role == "line"


# ---- maps show exactly what the text counts (found in the first live check: 55 points drawn for "40 schools") ---------
def _map_spec(layers, filters=None):
    m = {"layers": layers, "caption": "c"}
    if filters is not None:
        m["filters"] = filters
    return StorySpec.model_validate({"title": "t", "template": "docked-sidecar", "sections": [{"title": "s", "slides": [
        {"title": "a", "body": "b", "map": m}]}]})


def test_map_filters_change_the_map_key_and_are_validated():
    plain, filtered = _map_spec(["parks_"]), _map_spec(["parks_"], {"parks_": "name.notna()"})
    assert plain.sections[0].slides[0].map.key != filtered.sections[0].slides[0].map.key
    for bad in ({"other": "x == 1"}, {"parks_": "x" * 301}):
        with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
            _map_spec(["parks_"], bad)
    with pytest.raises(Exception):  # noqa: B017
        StorySpec.model_validate({"title": "t", "template": "longform", "sections": [{"title": "s", "slides": [
            {"title": "a", "body": "b", "map": {"item_id": "a" * 32, "filters": {"x": "y == 1"}}}]}]})


def test_the_map_contains_only_the_filtered_features(jh_store, monkeypatch):
    seen = {}
    monkeypatch.setattr(api, "publish_web_map", lambda wm, title, *a, **k: seen.update(wm=wm) or "d" * 32)
    monkeypatch.setattr(api, "publish_draft", lambda *a, **k: "e" * 32)
    spec = _map_spec(["parks_"], {"parks_": "name.notna()"})
    api.publish_spec(spec, jh_store, title="t", username="u", token="tok1234567890", portal=auth.DEFAULT_PORTAL)
    features = seen["wm"]["operationalLayers"][0]["featureCollection"]["layers"][0]["featureSet"]["features"]
    assert len(features) == 15 and all(f["attributes"]["name"] for f in features)          # 15 named parks, not all 38


def test_the_gate_catches_map_problems_before_publishing(jh_store):
    assert api.map_problems(_map_spec(["parks_"], {"parks_": "name.notna()"}), jh_store) == []
    assert "not loaded" in api.map_problems(_map_spec(["nope"]), jh_store)[0]
    assert "no features after its filter" in api.map_problems(_map_spec(["parks_"], {"parks_": "name == 'No Such Park'"}), jh_store)[0]
    assert "not valid" in api.map_problems(_map_spec(["parks_"], {"parks_": "__import__('os')"}), jh_store)[0]
    result = api.build(_map_spec(["parks_"], {"parks_": "name == 'No Such Park'"}), jh_store)
    assert not result.passed and result.story is None and result.report["map_problems"]


def test_large_integer_attributes_do_not_get_a_32_bit_field():
    """OSM ids reach 10^10; an Esri 32-bit integer field made such features silently invisible in the live map."""
    import geopandas as gpd
    from shapely.geometry import Point

    from storymap_gen.webmap import build_webmap
    gdf = gpd.GeoDataFrame({"osm_id": [5308271838, 12], "small": [1, 2]}, geometry=[Point(2.15, 41.4), Point(2.16, 41.41)], crs=4326)
    wm, _ = build_webmap({"t": gdf}, roles={"t": "transit"})
    fields = {f["name"]: f["type"] for f in wm["operationalLayers"][0]["featureCollection"]["layers"][0]["layerDefinition"]["fields"]}
    assert fields["osm_id"] == "esriFieldTypeDouble"
    assert fields["small"] == "esriFieldTypeInteger"


def test_headings_are_not_forced_wide():
    import json
    from pathlib import Path

    from storymap_gen.compiler import compile_story
    from storymap_gen.spec import StorySpec
    spec = StorySpec.model_validate({**json.loads((Path(__file__).parent.parent / "examples" / "gracia_barcelona.json").read_text("utf-8")),
                                     "template": "longform"})
    story = compile_story(spec)
    assert not [n for n in story["nodes"].values() if n.get("type") == "text" and n.get("config", {}).get("size") == "wide"]
