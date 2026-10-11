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
    with pytest.raises(ValueError, match="outside the allowed root"):
        resolve_data_path(outside, root=inside_root)
    monkeypatch.setenv("STORYMAP_DATA_ROOT", str(inside_root))
    with pytest.raises(ValueError, match="outside the allowed root"):
        resolve_data_path(outside)
    with pytest.raises(ValueError, match="outside the allowed root"):
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
    assert names == {"get_spec_schema", "measure_street_orientation", "verify_spec", "compile_spec", "preview_layouts"}
    assert not any("publish" in n for n in names)


def test_mcp_measure_then_verify_then_compile(tmp_path, monkeypatch, example_specs):
    monkeypatch.setenv("STORYMAP_OUT_DIR", str(tmp_path))
    data = {"streets_centerline": STREETS}

    async def go(c):
        facts = payload(await c.call_tool("measure_street_orientation", {"data": data, "streets": ["BROADWAY", "NORTHERN BLVD"]}))
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
