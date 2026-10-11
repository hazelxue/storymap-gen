import asyncio
import copy
import json

import pytest

from storymap_gen.api import build, load_store, resolve_data_path
from storymap_gen.spec import StorySpec
from tests.conftest import FIXTURES

STREETS = str(FIXTURES / "streets_centerline.geojson")


# ---- data path safety ------------------------------------------------------
def test_only_gis_file_types_are_accepted(tmp_path):
    secret = tmp_path / "id_rsa"
    secret.write_text("x")
    with pytest.raises(ValueError, match="unsupported"):
        resolve_data_path(secret)
    with pytest.raises(ValueError, match="unsupported"):
        resolve_data_path(tmp_path / "notes.txt")


def test_data_root_confinement(tmp_path, monkeypatch):
    outside = tmp_path / "outside.geojson"
    outside.write_text("{}")
    inside_root = FIXTURES
    assert resolve_data_path(STREETS, root=inside_root).name == "streets_centerline.geojson"
    with pytest.raises(ValueError, match="outside the allowed data root"):
        resolve_data_path(outside, root=inside_root)
    monkeypatch.setenv("STORYMAP_DATA_ROOT", str(inside_root))
    with pytest.raises(ValueError, match="outside the allowed data root"):
        resolve_data_path(outside)
    with pytest.raises(ValueError, match="outside the allowed data root"):
        resolve_data_path(inside_root / ".." / "pyproject.toml".replace("toml", "json"))


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_data_path(tmp_path / "nope.geojson")


# ---- build -----------------------------------------------------------------
def test_build_returns_story_only_when_the_gate_passes(example_specs):
    store = load_store({"streets_centerline": STREETS})
    ok = build(example_specs["jackson_heights"], store)
    assert ok.passed and ok.story is not None

    bad = copy.deepcopy(example_specs["jackson_heights"])
    bad["claims"][0]["street"] = "NORTHERN BLVD"
    failed = build(bad, store)
    assert not failed.passed and failed.story is None


def test_build_template_override(example_specs):
    store = load_store({"streets_centerline": STREETS})
    r = build(StorySpec.model_validate(example_specs["jackson_heights"]), store, template="longform")
    assert r.report["structure"]["template"] == "longform"


# ---- MCP, over the real protocol (in-memory transport) ----------------------------
def run_mcp(coro_fn):
    from mcp import Client

    from storymap_gen.mcp_server import server

    async def go():
        async with Client(server) as client:
            return await coro_fn(client)

    return asyncio.run(go())


def payload(result):
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def test_mcp_lists_the_pipeline_tools_and_not_publish():
    async def go(c):
        return {t.name for t in (await c.list_tools()).tools}

    names = run_mcp(go)
    assert names == {"get_spec_schema", "resolve_region", "fetch_osm_layers", "describe_data", "measure_street_orientation",
                     "verify_spec", "compile_spec", "preview_layouts", "init_spec", "explore_data", "find_findings"}
    assert not any(w in n for n in names for w in ("url", "download", "arcgis", "login", "token"))   # no arbitrary-URL or credential tools
    assert not any("publish" in n for n in names)


def test_mcp_measure_then_verify_then_compile(tmp_path, monkeypatch, example_specs):
    monkeypatch.setenv("STORYMAP_OUT_DIR", str(tmp_path))
    data = {"streets_centerline": STREETS}

    async def go(c):
        facts = payload(await c.call_tool("measure_street_orientation", {"data": data, "streets": ["BROADWAY", "NORTHERN BLVD"]}))["facts"]
        verify = payload(await c.call_tool("verify_spec", {"spec": example_specs["jackson_heights"], "data": data}))
        compiled = payload(await c.call_tool("compile_spec", {"spec": example_specs["jackson_heights"], "data": data,
                                                            "out_name": "story.json", "template": "longform"}))
        return facts, verify, compiled

    facts, verify, compiled = run_mcp(go)
    assert "BROADWAY: 37.1 deg" in facts and "NORTHERN BLVD: 0.1 deg" in facts
    assert verify["passed"] and {c["status"] for c in verify["claims"]} == {"PASS"}
    assert compiled["ok"] and (tmp_path / "story.json").exists()


def test_mcp_failed_claim_blocks_output_and_bad_input_is_explained(tmp_path, monkeypatch, example_specs):
    monkeypatch.setenv("STORYMAP_OUT_DIR", str(tmp_path))
    data = {"streets_centerline": STREETS}
    bad = copy.deepcopy(example_specs["jackson_heights"])
    bad["claims"][0]["street"] = "NORTHERN BLVD"

    async def go(c):
        blocked = payload(await c.call_tool("compile_spec", {"spec": bad, "data": data, "out_name": "bad.json"}))
        invalid = payload(await c.call_tool("verify_spec", {"spec": {"title": "x"}, "data": data}))
        traversal = payload(await c.call_tool("compile_spec", {"spec": example_specs["jackson_heights"], "data": data,
                                                             "out_name": "../evil.json"}))
        return blocked, invalid, traversal

    blocked, invalid, traversal = run_mcp(go)
    assert blocked["error"] == "quality_gate_failed" and not (tmp_path / "bad.json").exists()
    assert invalid["error"] == "spec_invalid" and any("sections" in d for d in invalid["details"])
    assert "plain file name" in traversal["error"]
    assert not (tmp_path.parent / "evil.json").exists()


# ---- MCP: region, fetch and describe for any place --------------------------------------------------------------
def _call(monkeypatch, tmp_path, calls):
    monkeypatch.setenv("STORYMAP_DATA_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    async def go(c):
        return [payload(await c.call_tool(name, args)) for name, args in calls]

    return run_mcp(go)


def test_mcp_resolve_fetch_describe_for_a_place(tmp_path, monkeypatch):
    import geopandas as gpd

    from storymap_gen.sources import geocode, overpass
    monkeypatch.setattr(geocode, "_fetch", lambda q: json.dumps([{"display_name": "Somewhere", "osm_type": "relation", "osm_id": 1,
                                                                   "boundingbox": ["41.38", "41.40", "2.15", "2.19"]}]).encode())
    frames = {"streets": gpd.read_file(FIXTURES / "streets_centerline.geojson"), "parks": gpd.read_file(FIXTURES / "parks.geojson")}

    def fake_fetch(name, region, use_cache=True, clip="boundary", sleep=None):
        if name == "water":
            raise overpass.net.NetworkError("all Overpass servers are busy")
        return frames[name]

    monkeypatch.setattr(overpass, "fetch_layer", fake_fetch)
    region, fetched, described = _call(monkeypatch, tmp_path, [
        ("resolve_region", {"folder": "gracia", "place": "Somewhere"}),
        ("fetch_osm_layers", {"folder": "gracia", "layers": ["streets", "parks", "water"]}),
        ("describe_data", {"manifest": "gracia/manifest.json"}),
    ])
    assert region["ok"] and region["name"] == "Somewhere" and (tmp_path / "gracia" / "region.json").exists()
    assert fetched["ok"] and [x["name"] for x in fetched["layers"]] == ["streets", "parks"] and fetched["failed"][0]["layer"] == "water"
    assert fetched["sources"][0]["key"] == "osm" and (tmp_path / "gracia" / "streets.geojson").exists()
    assert described["ok"] and "## streets  (1001 features)" in described["markdown"]


def test_mcp_region_from_bbox_and_bad_regions(tmp_path, monkeypatch):
    ok, nothing, too_big, bad_box = _call(monkeypatch, tmp_path, [
        ("resolve_region", {"folder": "a", "bbox": [2.15, 41.38, 2.19, 41.40], "place": "Box"}),
        ("resolve_region", {"folder": "b"}),
        ("resolve_region", {"folder": "c", "bbox": [-74.3, 40.4, -73.6, 41.0]}),
        ("resolve_region", {"folder": "d", "bbox": [1, 2, 3]}),
    ])
    assert ok["ok"] and ok["has_boundary"] is False
    assert nothing["error"] == "bad_input" and "neighbourhood or district" in too_big["message"] and bad_box["error"] == "bad_input"


@pytest.mark.parametrize("folder", ["../escape", "a/b", "..", ".", "", "x" * 41, "a b", "C:/windows", "~"])
def test_mcp_folders_cannot_leave_the_data_root(tmp_path, monkeypatch, folder):
    inner = tmp_path / "root"
    inner.mkdir()
    monkeypatch.setenv("STORYMAP_DATA_ROOT", str(inner))
    monkeypatch.chdir(inner)

    async def go(c):
        return payload(await c.call_tool("resolve_region", {"folder": folder, "bbox": [2.15, 41.38, 2.19, 41.40]}))

    r = run_mcp(go)
    assert r["ok"] is False and r["error"] == "bad_input"
    assert [p.name for p in tmp_path.iterdir()] == ["root"] and list(inner.iterdir()) == []


def test_mcp_manifest_must_live_inside_the_data_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "manifest.json"
    outside.write_text("{}")
    r = _call(monkeypatch, root, [("describe_data", {"manifest": str(outside)}), ("describe_data", {"manifest": "nope.json"}),
                                  ("describe_data", {})])
    assert r[0]["error"] == "bad_input" and r[1]["error"] == "bad_input"      # outside the root / missing file
    assert r[2]["ok"] is True                                                  # no layers at all is a valid, empty profile
    assert str(tmp_path) not in json.dumps(r)


def test_mcp_fetch_limits_and_network_errors_are_structured(tmp_path, monkeypatch):
    from storymap_gen.sources import overpass
    (tmp_path / "f").mkdir()
    (tmp_path / "f" / "region.json").write_text(json.dumps(
        {"name": "x", "bbox": [2.15, 41.38, 2.19, 41.40], "display_name": "", "osm_type": "", "osm_id": 0}), encoding="utf-8")
    monkeypatch.setattr(overpass, "fetch_layer", lambda *a, **k: (_ for _ in ()).throw(overpass.net.NetworkError("all Overpass servers are busy")))
    many, down, no_region = _call(monkeypatch, tmp_path, [
        ("fetch_osm_layers", {"folder": "f", "layers": ["streets"] * 9}),
        ("fetch_osm_layers", {"folder": "f", "layers": ["streets"]}),
        ("fetch_osm_layers", {"folder": "missing", "layers": ["streets"]}),
    ])
    assert many["error"] == "bad_input" and "at most" in many["message"]
    assert down["ok"] is False and down["failed"][0]["layer"] == "streets" and down["manifest"] is None
    assert no_region["error"] == "bad_input"


def test_mcp_verify_and_compile_accept_a_manifest(tmp_path, monkeypatch, example_specs):
    import shutil

    from storymap_gen.sources.geocode import region_from_bbox
    from storymap_gen.sources.manifest import LayerRecord, new_manifest, write_manifest
    (tmp_path / "d").mkdir()
    shutil.copy(FIXTURES / "streets_centerline.geojson", tmp_path / "d" / "streets_centerline.geojson")
    m = new_manifest(region_from_bbox(-73.9, 40.74, -73.87, 40.77, "JH"))
    m.layers.append(LayerRecord("streets_centerline", "streets_centerline.geojson", 1001, "LineString", "osm", "a", "b"))
    write_manifest(m, tmp_path / "d")
    monkeypatch.setenv("STORYMAP_OUT_DIR", str(tmp_path / "out"))
    verify, compiled = _call(monkeypatch, tmp_path, [
        ("verify_spec", {"spec": example_specs["jackson_heights"], "manifest": "d/manifest.json"}),
        ("compile_spec", {"spec": example_specs["jackson_heights"], "manifest": "d/manifest.json", "out_name": "s.json"}),
    ])
    assert verify["passed"] and verify["placeholders"] == [] and compiled["ok"] and (tmp_path / "out" / "s.json").exists()


def test_mcp_init_spec_and_explore_data(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setenv("STORYMAP_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("STORYMAP_OUT_DIR", str(tmp_path / "out"))
    shutil.copytree(FIXTURES / "gracia", tmp_path / "g")
    (tmp_path / "g" / "region.json").write_text(json.dumps(json.loads((tmp_path / "g" / "manifest.json").read_text(encoding="utf-8"))["region"]),
                                                encoding="utf-8")

    async def go(c):
        skeleton = payload(await c.call_tool("init_spec", {"folder": "g", "topic": "Streets", "framework": "systems",
                                                          "manifest": "g/manifest.json"}))
        outline = payload(await c.call_tool("init_spec", {"folder": "g", "topic": "T", "outline": "# Parks\n  - Big parks\n# Streets\n"}))
        bad = payload(await c.call_tool("init_spec", {"folder": "g", "topic": "T", "framework": "nope"}))
        page = payload(await c.call_tool("explore_data", {"out_name": "x.html", "manifest": "g/manifest.json"}))
        evil = payload(await c.call_tool("explore_data", {"out_name": "../x.html", "manifest": "g/manifest.json"}))
        verify = payload(await c.call_tool("verify_spec", {"spec": skeleton["spec"], "manifest": "g/manifest.json"}))
        return skeleton, outline, bad, page, evil, verify

    skeleton, outline, bad, page, evil, verify = run_mcp(go)
    assert skeleton["ok"] and skeleton["sections"][:3] == ["Introduction", "Hard systems", "Soft systems"]
    assert outline["ok"] and outline["sections"] == ["Parks", "Streets"]
    assert bad["error"] == "bad_input" and evil["ok"] is False
    assert page["ok"] and (tmp_path / "out" / "x.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    assert verify["ok"] and not verify["passed"] and verify["placeholders"] and "depth" in verify and verify["map_problems"] == []
