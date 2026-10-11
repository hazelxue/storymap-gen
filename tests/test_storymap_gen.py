import copy
import json
from pathlib import Path

import geopandas as gpd
import pytest
from pydantic import ValidationError
from shapely.geometry import LineString

from storymap_gen.__main__ import main
from storymap_gen.compiler import compile_story, esc
from storymap_gen.evaluate import check_graph, evaluate, judge
from storymap_gen.preview import render_preview
from storymap_gen.spec import OrientationClaim, StatClaim, StorySpec
from storymap_gen.verify import DataStore, verify_claims

ROOT = Path(__file__).resolve().parent.parent
REAL = json.loads((ROOT / "fixtures/jackson_heights_story.json").read_text(encoding="utf-8"))
EXAMPLE = json.loads((ROOT / "examples/jackson_heights.json").read_text(encoding="utf-8"))
TEMPLATES = ["docked-sidecar", "floating-sidecar", "longform"]


@pytest.fixture(scope="module")
def store():
    return DataStore.from_files({"streets_centerline": str(ROOT / "fixtures/streets_centerline.geojson")})


def make_spec(**over) -> StorySpec:
    d = copy.deepcopy(EXAMPLE)
    d.update(over)
    return StorySpec.model_validate(d)


# ---- spec validation ------------------------------------------------------
def test_unknown_citation_rejected():
    d = copy.deepcopy(EXAMPLE)
    d["sections"][0]["cites"] = ["nope"]
    with pytest.raises(ValidationError, match="unknown source"):
        StorySpec.model_validate(d)


def test_unknown_claim_and_duplicate_ids_rejected():
    d = copy.deepcopy(EXAMPLE)
    d["sections"][1]["slides"][0]["claim_ids"] = ["ghost"]
    with pytest.raises(ValidationError, match="unknown claim"):
        StorySpec.model_validate(d)
    d = copy.deepcopy(EXAMPLE)
    d["claims"].append(d["claims"][0])
    with pytest.raises(ValidationError, match="duplicate claim"):
        StorySpec.model_validate(d)


# ---- compiler -------------------------------------------------------------
def test_html_escaping_matches_real_story_convention():
    assert esc("city's \"grid\" & <b>") == "city&apos;s &quot;grid&quot; &amp; &lt;b&gt;"
    assert any("&apos;" in n["data"]["text"] for n in REAL["nodes"].values() if n["type"] == "text")


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_template_compiles_to_a_valid_graph(template):
    story = compile_story(make_spec(template=template))
    assert check_graph(story) == []


def test_templates_differ_only_in_slide_layout():
    kinds = {t: {n["type"] for n in compile_story(make_spec(template=t))["nodes"].values()} for t in TEMPLATES}
    assert "immersive" in kinds["docked-sidecar"] and "immersive" in kinds["floating-sidecar"]
    assert "immersive" not in kinds["longform"]
    subtypes = {t: {n["data"]["subtype"] for n in compile_story(make_spec(template=t))["nodes"].values()
                    if n["type"] == "immersive"} for t in TEMPLATES[:2]}
    assert subtypes == {"docked-sidecar": {"docked-panel"}, "floating-sidecar": {"floating-panel"}}


def test_compiled_output_conforms_to_real_schema():
    """Every node type we emit exists in a real story, and our data/config keys are a subset of the real ones."""
    real_keys: dict[str, tuple[set, set]] = {}
    for n in REAL["nodes"].values():
        d, c = real_keys.setdefault(n["type"], (set(), set()))
        d |= set(n.get("data", {}))
        c |= set(n.get("config", {}))
    for template in TEMPLATES:
        for n in compile_story(make_spec(template=template))["nodes"].values():
            assert n["type"] in real_keys, n["type"]
            dkeys, ckeys = real_keys[n["type"]]
            assert set(n.get("data", {})) <= dkeys, (n["type"], set(n.get("data", {})) - dkeys)
            assert set(n.get("config", {})) <= ckeys, (n["type"], set(n.get("config", {})) - ckeys)


def test_citation_numbers_follow_source_order_and_reference_list_present():
    story = compile_story(make_spec())
    texts = [n["data"]["text"] for n in story["nodes"].values() if n["type"] == "text"]
    assert "Sources: [1][2]" in texts            # nyc-lpc, city-lore
    assert "Sources: [4]" in texts               # nyc-parks cited only in intro -> number 4
    lst = next(t for t in texts if t.startswith("<li>"))
    assert lst.count("<li>") == len(EXAMPLE["sources"])


def test_navigation_points_at_section_headings():
    story = compile_story(make_spec())
    nav = next(n for n in story["nodes"].values() if n["type"] == "navigation")
    titles = [story["nodes"][link["nodeId"]]["data"]["text"] for link in nav["data"]["links"]]
    assert titles == [s["title"] for s in EXAMPLE["sections"]]


def test_compilation_is_deterministic():
    assert compile_story(make_spec()) == compile_story(make_spec())


# ---- claim verification ---------------------------------------------------
def orient(street, relation):
    return OrientationClaim(id="c", text="t", street=street, relation=relation)


def run_one(store, claim):
    spec = StorySpec.model_validate({**EXAMPLE, "claims": [claim.model_dump()],
                                     "sections": [{"title": "S"}]})
    return verify_claims(spec, store)[0]


def test_northern_blvd_is_parallel_not_diagonal(store):
    """Regression for the original story error."""
    assert run_one(store, orient("NORTHERN BLVD", "diagonal_to_grid")).status == "FAIL"
    assert run_one(store, orient("NORTHERN BLVD", "parallel_to_grid")).status == "PASS"


def test_broadway_is_diagonal(store):
    r = run_one(store, orient("BROADWAY", "diagonal_to_grid"))
    assert r.status == "PASS" and r.measured > 30


def test_stat_claims_against_real_layer(store):
    ok = StatClaim(id="c", text="t", layer="streets_centerline", agg="count", expected=1001)
    bad = StatClaim(id="c", text="t", layer="streets_centerline", agg="count", expected=900)
    assert run_one(store, ok).status == "PASS"
    assert run_one(store, bad).status == "FAIL"


def test_missing_street_or_field_is_error_not_pass(store):
    assert run_one(store, orient("NO SUCH ST", "diagonal_to_grid")).status == "ERROR"
    miss = StatClaim(id="c", text="t", layer="streets_centerline", field="nope", agg="mean", expected=1)
    assert run_one(store, miss).status == "ERROR"


def test_synthetic_rotated_grid_is_not_confused_by_global_rotation():
    """A grid rotated 30 degrees: grid streets are parallel to it, a street at 75 degrees is diagonal."""
    import math
    rot = math.radians(30)

    def line(x0, y0, x1, y1):
        c, s = math.cos(rot), math.sin(rot)
        f = lambda x, y: (x * c - y * s, x * s + y * c)  # noqa: E731
        return LineString([f(x0, y0), f(x1, y1)])

    names, geoms = [], []
    for i in range(1, 6):
        names += [f"{i} AVE", f"{i} ST"]
        geoms += [line(0, i * 100, 600, i * 100), line(i * 100, 0, i * 100, 600)]
    names.append("DIAG RD")
    geoms.append(line(0, 0, 400, 400))
    # place near NYC so UTM estimation works: shift in degrees
    from shapely.affinity import scale, translate
    geoms = [translate(scale(g, 1e-5, 1e-5, origin=(0, 0)), -73.88, 40.75) for g in geoms]
    gdf = gpd.GeoDataFrame({"full_stree": names}, geometry=geoms, crs=4326)
    st = DataStore({"streets_centerline": gdf})
    assert run_one(st, orient("DIAG RD", "diagonal_to_grid")).status == "PASS"
    assert run_one(st, orient("3 AVE", "parallel_to_grid")).status == "PASS"


# ---- evaluation -----------------------------------------------------------
def test_graph_checker_accepts_real_published_story():
    assert check_graph(REAL) == []


def test_graph_checker_catches_corruption():
    story = compile_story(make_spec())

    broken = copy.deepcopy(story)
    first_body = next(n for n in broken["nodes"].values() if n["type"] == "story")["children"][2]
    del broken["nodes"][first_body]
    assert any("does not exist" in i for i in check_graph(broken))

    orphan = copy.deepcopy(story)
    orphan["nodes"]["n-zzzzzz"] = {"type": "text", "data": {"type": "paragraph", "text": "x"}}
    assert any("orphan" in i for i in check_graph(orphan))

    no_res = copy.deepcopy(story)
    no_res["resources"].clear()
    assert any("missing" in i for i in check_graph(no_res))

    bad_nav = copy.deepcopy(story)
    nav = next(n for n in bad_nav["nodes"].values() if n["type"] == "navigation")
    para = next(k for k, n in bad_nav["nodes"].items() if n["type"] == "text" and n["data"]["type"] == "paragraph")
    nav["data"]["links"].append({"nodeId": para})
    assert any("not an existing h2" in i for i in check_graph(bad_nav))


def test_evaluate_gate(store):
    spec = make_spec()
    good = evaluate(spec, compile_story(spec), verify_claims(spec, store))
    assert good["passed"] and good["claims"]["pass"] == 4

    d = copy.deepcopy(EXAMPLE)
    d["claims"][0]["street"] = "NORTHERN BLVD"
    bad_spec = StorySpec.model_validate(d)
    bad = evaluate(bad_spec, compile_story(bad_spec), verify_claims(bad_spec, store))
    assert not bad["passed"] and bad["claims"]["failed_ids"] == ["broadway-diagonal"]


def test_uncited_source_fails_gate(store):
    d = copy.deepcopy(EXAMPLE)
    d["sources"].append({"key": "extra", "text": "Never cited"})
    spec = StorySpec.model_validate(d)
    rep = evaluate(spec, compile_story(spec), verify_claims(spec, store))
    assert rep["citation"]["uncited_sources"] == ["extra"] and not rep["passed"]


def test_llm_judge_with_stub_and_validation():
    spec = make_spec()
    good = judge(spec, lambda p: 'sure {"scores": {"narrative_arc": 4, "text_map_consistency": 5, '
                                 '"claim_specificity": 3, "tone": 4}, "notes": "ok"}')
    assert good["mean"] == 4.0
    with pytest.raises(ValueError):
        judge(spec, lambda p: '{"scores": {"narrative_arc": 9}}')
    with pytest.raises(ValueError):
        judge(spec, lambda p: '{"scores": {"made_up": 3}}')


# ---- preview + CLI --------------------------------------------------------
def test_preview_contains_all_three_layouts():
    html = render_preview(make_spec())
    assert all(t in html for t in TEMPLATES)


def test_cli_exit_codes(tmp_path):
    data = f"streets_centerline={ROOT / 'fixtures/streets_centerline.geojson'}"
    ok_out = tmp_path / "ok.json"
    assert main(["run", str(ROOT / "examples/jackson_heights.json"), "--data", data, "--out", str(ok_out)]) == 0
    assert check_graph(json.loads(ok_out.read_text(encoding="utf-8"))) == []

    d = copy.deepcopy(EXAMPLE)
    d["claims"][0]["street"] = "NORTHERN BLVD"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(d), encoding="utf-8")
    out = tmp_path / "bad_out.json"
    assert main(["run", str(bad), "--data", data, "--out", str(out)]) == 1
    assert not out.exists()

    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"title": "x"}), encoding="utf-8")
    assert main(["run", str(invalid)]) == 1


# ---- publish (payload only; no network) -----------------------------------
def test_publish_payload_is_private_draft_and_never_targets_existing_item():
    from storymap_gen.publish import build_item_params
    story = compile_story(make_spec())
    p = build_item_params(story, "T", ts_ms=1700000000000)
    assert p["access"] == "private" and p["type"] == "StoryMap"
    kws = p["typeKeywords"].split(",")
    assert "smstatusdraft" in kws and "smdraftresourceid:draft_1700000000000.json" in kws
    assert "smstatuspublished" not in kws
    assert json.loads(p["text"]) == story and "id" not in p and "token" not in p


def test_publish_cli_requires_env_token_and_valid_graph(tmp_path, monkeypatch):
    story = tmp_path / "s.json"
    story.write_text(json.dumps(compile_story(make_spec())), encoding="utf-8")
    monkeypatch.delenv("ARCGIS_TOKEN", raising=False)
    assert main(["publish", str(story), "--title", "t", "--username", "u"]) == 2
    monkeypatch.setenv("ARCGIS_TOKEN", "x")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"root": "n-x", "nodes": {}, "resources": {}}), encoding="utf-8")
    assert main(["publish", str(bad), "--title", "t", "--username", "u"]) == 1


def test_orientation_facts_block_matches_what_the_ablation_used(store):
    from storymap_gen.verify import orientation_facts
    block = orientation_facts(store, ["BROADWAY", "NORTHERN BLVD", "JUNCTION BLVD", "ROOSEVELT AVE", "34 AVE", "37 AVE", "82 ST"])
    assert ("BROADWAY: 37.1 deg; NORTHERN BLVD: 0.1 deg; JUNCTION BLVD: 14.5 deg; ROOSEVELT AVE: 0.2 deg; "
            "34 AVE: 0.3 deg; 37 AVE: 0.2 deg; 82 ST: 0.1 deg") in block
    assert "above 15 deg" in block
