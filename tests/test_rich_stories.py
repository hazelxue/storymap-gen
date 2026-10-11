"""Rich stories: content blocks, subsections, swipes, key figures, depth warnings, density and coverage claims, frameworks."""
import json
import re
from pathlib import Path

import pytest

from storymap_gen import auth
from storymap_gen.api import build, generated_maps, load_store, map_problems
from storymap_gen.claims import CoverageClaim, DensityClaim
from storymap_gen.compiler import compile_story, inline
from storymap_gen.depth import depth_report
from storymap_gen.evaluate import check_graph
from storymap_gen.explore import build_explorer_html
from storymap_gen.scaffold import FRAMEWORKS, layers_for, parse_outline_tree, scaffold_spec
from storymap_gen.sources.geocode import Region
from storymap_gen.sources.manifest import read_manifest
from storymap_gen.spec import StorySpec

ROOT = Path(__file__).resolve().parent.parent
GRACIA = ROOT / "fixtures" / "gracia"


@pytest.fixture(scope="module")
def gracia():
    manifest = read_manifest(GRACIA / "manifest.json")
    return manifest, load_store(manifest.data_paths(GRACIA))


def _spec(blocks, claims=None, **extra):
    base = {"title": "T", "template": "longform", "sources": [{"key": "osm", "text": "OpenStreetMap"}],
            "sections": [{"title": "S", "intro": "Intro.", "cites": ["osm"], "blocks": blocks}], "claims": claims or []}
    return StorySpec.model_validate({**base, **extra})


STATIONS = {"kind": "layer_stat", "id": "stations", "text": "6 stations", "layer": "transit", "agg": "count", "expected": 6,
            "where": "railway == 'station'"}


# ---- blocks compile to a valid graph -------------------------------------------------------------------------------
def test_every_block_kind_compiles_to_a_valid_graph():
    spec = _spec([
        {"type": "heading", "level": 3, "text": "Sub"},
        {"type": "heading", "level": 4, "text": "Sub sub"},
        {"type": "paragraph", "text": "Read the **report** at [the source](https://example.org/a) and *think*.", "cites": ["osm"]},
        {"type": "bullets", "items": ["one", "two"]},
        {"type": "bullets", "items": ["first", "second"], "ordered": True},
        {"type": "quote", "text": "A line", "attribution": "Someone"},
        {"type": "map", "map": {"layers": ["streets"], "caption": "Streets"}},
        {"type": "swipe", "map": {"layers": ["parks", "streets"]}, "left": ["parks"], "right": ["streets"], "caption": "Compare"},
        {"type": "figures", "items": [{"value": "six", "label": "stations"}, {"value": "two", "label": "things"}]},
        {"type": "image", "url": "https://example.org/a.jpg", "alt": "A street", "caption": "A street", "attribution": "Someone, CC BY"},
        {"type": "separator"},
        {"type": "slides", "slides": [{"title": "Slide", "body": "Body.", "map": {"layers": ["streets"]}}]},
    ])
    story = compile_story(spec)
    assert check_graph(story) == []
    types = [n["type"] for n in story["nodes"].values()]
    assert types.count("swipe") == 1 and types.count("large-number") == 2 and types.count("grid") == 1 and types.count("separator") == 1
    kinds = {n["data"]["type"] for n in story["nodes"].values() if n["type"] == "text"}
    assert {"h2", "h3", "h4", "paragraph", "bullet-list", "numbered-list", "quote"} <= kinds


def test_swipe_sides_are_two_views_of_one_map_with_stable_layer_ids():
    spec = _spec([{"type": "swipe", "map": {"layers": ["parks", "streets"]}, "left": ["parks"], "right": ["streets"],
                   "left_label": "Parks", "right_label": "Streets"}])
    story = compile_story(spec)
    swipe = next(n for n in story["nodes"].values() if n["type"] == "swipe")
    left, right = (story["nodes"][swipe["data"]["contents"][k]] for k in "01")
    assert left["data"]["map"] == right["data"]["map"] and len(story["resources"]) == 2      # one Web Map, plus the theme
    visible = lambda node: {layer["id"]: layer["visible"] for layer in node["data"]["mapLayers"]}  # noqa: E731
    assert visible(left) == {"layer-parks": True, "layer-streets": False}
    assert visible(right) == {"layer-parks": False, "layer-streets": True}
    assert len(generated_maps(spec)) == 1


def test_a_swipe_must_use_generated_layers_split_between_the_sides():
    for bad in ({"map": {"item_id": "a" * 32}, "left": ["x"], "right": ["y"]},
                {"map": {"layers": ["a", "b"]}, "left": ["a"], "right": ["c"]},
                {"map": {"layers": ["a", "b"]}, "left": ["a"], "right": ["a", "b"]}):
        with pytest.raises(ValueError):
            _spec([{"type": "swipe", **bad}])


def test_inline_markup_is_escaped_first_and_only_https_links_work():
    assert inline("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert "<a " not in inline("[x](http://insecure.example)") and "<a " not in inline("[x](javascript:alert(1))")
    assert inline("[a](https://e.org/q?x=1&y=2)") == '<a href="https://e.org/q?x=1&amp;y=2" rel="noopener noreferrer" target="_blank">a</a>'
    assert inline("**b** and *i*") == "<strong>b</strong> and <em>i</em>"
    assert '"' not in inline('say "hi" [x](https://e.org/"onmouseover=1)').split("href=")[0]


def test_paragraph_cites_become_numbered_markers_and_count_as_citing():
    spec = _spec([{"type": "paragraph", "text": "A fact.", "cites": ["osm"]}])
    spec.sections[0].cites = []
    assert spec.cited_keys() == {"osm"}
    para = [n for n in compile_story(spec)["nodes"].values() if n["type"] == "text" and n["data"]["type"] == "paragraph"]
    assert any(n["data"]["text"].endswith("[1]") for n in para)
    with pytest.raises(ValueError, match="unknown source"):
        _spec([{"type": "paragraph", "text": "x", "cites": ["nope"]}])


def test_unknown_claims_in_blocks_are_refused():
    with pytest.raises(ValueError, match="unknown claim"):
        _spec([{"type": "paragraph", "text": "x", "claim_ids": ["nope"]}])
    with pytest.raises(ValueError, match="unknown claim"):
        _spec([{"type": "figures", "items": [{"value": "6", "label": "x", "claim_id": "nope"}]}])


# ---- numbers are bound to claims in every kind of text -------------------------------------------------------------
def test_numbers_are_checked_in_every_block_kind(gracia):
    _, store = gracia
    spec = _spec([
        {"type": "paragraph", "text": "There are 6 stations.", "claim_ids": ["stations"]},
        {"type": "paragraph", "text": "There are 9 stations."},
        {"type": "bullets", "items": ["6 stations here", "7 stations there"], "claim_ids": ["stations"]},
        {"type": "quote", "text": "A quote with 12 in it."},
        {"type": "figures", "items": [{"value": "6", "label": "stations", "claim_id": "stations"}, {"value": "99", "label": "invented"}]},
        {"type": "map", "map": {"layers": ["streets"], "caption": "A map of 31 things"}},
    ], [STATIONS])
    result = build(spec, store)
    unbacked = " | ".join(result.report["unbacked_numbers"])
    for bad in ("9 in", "7 in", "12 in", "99 in", "31 in"):
        assert bad.split()[0] in unbacked, (bad, unbacked)
    assert unbacked.count(" in ") == 5          # and the backed ones were accepted
    assert not result.passed and result.story is None


def test_links_and_citation_markers_do_not_trip_the_number_check(gracia):
    _, store = gracia
    spec = _spec([{"type": "paragraph", "text": "See [report 2024](https://example.org/a/12345) for 6 stations [1].", "claim_ids": ["stations"]}],
                 [STATIONS], depth="brief")
    assert build(spec, store).report["unbacked_numbers"] == []


def test_placeholders_in_blocks_stop_the_gate(gracia):
    _, store = gracia
    for block in ({"type": "paragraph", "text": "TODO: write this"}, {"type": "heading", "text": "TBD"},
                  {"type": "bullets", "items": ["fine", "lorem ipsum"]}, {"type": "map", "map": {"layers": ["streets"], "caption": "TODO"}},
                  {"type": "image", "url": "https://e.org/a.png", "alt": "TODO alt"}):
        report = build(_spec([block]), store).report
        assert report["placeholders"], block


def test_map_problems_cover_map_blocks_and_swipes(gracia):
    _, store = gracia
    spec = _spec([{"type": "map", "map": {"layers": ["nope"]}},
                  {"type": "swipe", "map": {"layers": ["transit", "streets"], "filters": {"transit": "railway == 'nothing'"}},
                   "left": ["transit"], "right": ["streets"]}])
    problems = " ".join(map_problems(spec, store))
    assert "'nope' is not loaded" in problems and "no features after its filter" in problems


# ---- depth ---------------------------------------------------------------------------------------------------------
def test_depth_report_counts_and_warns():
    thin = _spec([{"type": "heading", "text": "A"}, {"type": "paragraph", "text": "Short."},
                  {"type": "map", "map": {"layers": ["streets"]}}], [STATIONS])
    report = depth_report(thin)
    assert report["subsections"] == 1 and report["maps"] == 1 and report["profile"] == "standard"
    text = " ".join(report["warnings"])
    assert "words:" in text and "has no caption" in text and "only" in text and "states no measured fact" in text
    assert "claims never attached" in text


def test_a_full_story_has_no_depth_warnings():
    para = " ".join(["word"] * 120)
    blocks = []
    for i in range(9):
        blocks += [{"type": "heading", "text": f"Sub {i}"}, {"type": "paragraph", "text": para, "claim_ids": ["stations"]},
                   {"type": "map", "map": {"layers": ["streets"], "caption": "c"}}]
    spec = _spec(blocks, [STATIONS], depth="brief")
    spec.sections = [spec.sections[0]] * 3
    assert depth_report(spec)["warnings"] == []
    spec2 = spec.model_copy(update={"depth": "full"})
    assert any("words" in w or "sections" in w for w in depth_report(spec2)["warnings"])      # one section is not 'full'


def test_depth_warnings_do_not_fail_the_gate(gracia):
    _, store = gracia
    spec = _spec([{"type": "paragraph", "text": "Six stations.", "claim_ids": ["stations"]}], [STATIONS])
    result = build(spec, store)
    assert result.report["depth"]["warnings"] and result.passed is True


# ---- new claim types -----------------------------------------------------------------------------------------------
def test_density_and_coverage_claims_measure_against_the_boundary(gracia):
    _, store = gracia
    ok = [DensityClaim(id="a", text="x", layer="transit", where="railway == 'station'", expected=1.43, tolerance=0.02),
          DensityClaim(id="b", text="x", layer="streets", measure="length_km", expected=21.98, tolerance=0.05),
          CoverageClaim(id="c", text="x", layer="parks", expected_share=0.13, tolerance=0.005),
          CoverageClaim(id="d", text="x", layer="transit", where="railway == 'station'", max_distance_m=500, expected_share=0.63, tolerance=0.01)]
    assert [c.check(store).status for c in ok] == ["PASS"] * 4
    assert DensityClaim(id="a", text="x", layer="transit", expected=5).check(store).status == "FAIL"
    assert CoverageClaim(id="c", text="x", layer="parks", expected_share=0.5).check(store).hint


def test_density_and_coverage_report_clear_errors(gracia):
    _, store = gracia
    no_boundary = type(store)({k: v for k, v in store.layers.items() if k != "boundary"})
    assert "not loaded" in DensityClaim(id="a", text="x", layer="streets", expected=1).check(no_boundary).detail
    assert "no polygons" in CoverageClaim(id="c", text="x", layer="transit", expected_share=0.1).check(store).detail
    assert "no features matched" in DensityClaim(id="a", text="x", layer="streets", where="highway == 'nothing'", expected=1).check(store).detail


def test_density_and_coverage_back_the_numbers_in_prose(gracia):
    _, store = gracia
    claims = [{"kind": "area_coverage", "id": "green", "text": "13% green", "layer": "parks", "expected_share": 0.13, "tolerance": 0.005},
              {"kind": "layer_density", "id": "dens", "text": "21.98 km of street per km2", "layer": "streets", "measure": "length_km",
               "expected": 21.98, "tolerance": 0.05}]
    spec = _spec([{"type": "paragraph", "text": "About 13% of the district is green, and there are 21.98 km of street per square kilometre.",
                   "claim_ids": ["green", "dens"]}], claims, depth="brief")
    result = build(spec, store)
    assert result.report["unbacked_numbers"] == [] and result.report["claims"]["pass"] == 2


# ---- frameworks and outlines ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("framework", [f for f in FRAMEWORKS if f != "outline"])
@pytest.mark.parametrize("template", ["longform", "docked-sidecar", "floating-sidecar"])
def test_every_framework_gives_a_valid_unfinished_spec(gracia, framework, template):
    manifest, store = gracia
    region = Region.from_dict(manifest.region)
    skeleton = scaffold_spec(region, "Topic", manifest, None, template, None, framework)
    spec = StorySpec.model_validate(skeleton)
    assert spec.sections and "TODO" in json.dumps(skeleton)
    result = build(spec, store)
    assert not result.passed and result.report["placeholders"] and result.report["graph_issues"] == []
    assert result.report["map_problems"] == []                          # the layers it picked exist and are not empty
    if framework == "systems":
        assert [s.title for s in spec.sections][:3] == ["Introduction", "Hard systems", "Soft systems"] and spec.depth == "full"


def test_the_outline_becomes_sections_and_subsections():
    text = "# Streets\n  - The grid\n  - Bike lanes\n# Green space\n- Parks near schools\n"
    assert parse_outline_tree(text) == [("Streets", ["The grid", "Bike lanes"]), ("Green space", []), ("Parks near schools", [])]
    region = Region.from_dict({"name": "X", "bbox": [2.15, 41.38, 2.19, 41.40]})
    skeleton = scaffold_spec(region, "T", None, "# Streets\n  - The grid\n  - Bike lanes\n", "longform", ["streets", "bike", "parks"])
    headings = [b["text"] for b in skeleton["sections"][0]["blocks"] if b["type"] == "heading"]
    assert headings == ["The grid", "Bike lanes"] and StorySpec.model_validate(skeleton)


def test_layers_for_matches_whole_words_only():
    avail = ["streets", "parks", "transit", "schools", "water", "bike"]
    assert layers_for("The street grid", avail) == ["streets"]                    # 'street' must not match 'tree'
    assert layers_for("Parks near schools", avail) == ["parks", "schools"]
    assert layers_for("Public transport and bikes", avail)[:2] == ["transit", "streets"]
    assert layers_for("Conclusion", avail, limit=2) == ["streets", "parks"]       # nothing matches: the first layers


def test_a_missing_outline_is_refused_for_the_outline_framework():
    region = Region.from_dict({"name": "X", "bbox": [2.15, 41.38, 2.19, 41.40]})
    with pytest.raises(ValueError, match="outline"):
        scaffold_spec(region, "T", None, None, "longform", ["streets"], "outline")
    with pytest.raises(ValueError, match="unknown framework"):
        scaffold_spec(region, "T", None, None, "longform", ["streets"], "nope")


# ---- explorer ------------------------------------------------------------------------------------------------------
def test_explorer_is_self_contained_and_data_cannot_break_out_of_it(gracia):
    import geopandas as gpd
    from shapely.geometry import Point
    _, store = gracia
    evil = gpd.GeoDataFrame({"name": ["</script><img src=x onerror=alert(1)>"]}, geometry=[Point(2.15, 41.4)], crs=4326)
    html = build_explorer_html(type(store)({**store.layers, "evil": evil}), "Title <b>")
    assert "Content-Security-Policy" in html and "default-src 'none'" in html
    assert "<img src=x" not in html and "</script><img" not in html and "\\u003c/script" in html
    assert not re.search(r"(?i)https?://(?!www\.w3\.org)", html.split('id="data"')[0])      # nothing is loaded from the network
    assert "Streets" in html and "1205" in html.replace(",", "")


# ---- sign-in helpers -----------------------------------------------------------------------------------------------
def test_portal_addresses_are_accepted_in_any_common_form():
    assert auth.normalize_portal("myorg.maps.arcgis.com") == "https://myorg.maps.arcgis.com/sharing/rest"
    assert auth.normalize_portal("https://x.maps.arcgis.com/") == "https://x.maps.arcgis.com/sharing/rest"
    assert auth.normalize_portal("https://x.maps.arcgis.com/sharing/rest") == "https://x.maps.arcgis.com/sharing/rest"
    assert auth.normalize_portal("") == auth.DEFAULT_PORTAL
    with pytest.raises(ValueError):
        auth.resolve_portal("https://evil.example.com")


def test_the_login_is_remembered_without_its_token(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYMAP_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("STORYMAP_PORTAL", raising=False)
    assert auth.status()["signed_in"] is False and auth.resolve_portal() == auth.DEFAULT_PORTAL
    auth.save_profile("abcdEFGH12345678", "https://x.maps.arcgis.com/sharing/rest")
    assert auth.resolve_portal() == "https://x.maps.arcgis.com/sharing/rest"
    assert auth.load_profile()["client_id"] == "abcdEFGH12345678"
    auth.save_token("tok" * 10, "someone", "https://x.maps.arcgis.com/sharing/rest", 3600)
    st = auth.status()
    assert st["signed_in"] and st["username"] == "someone" and 55 <= st["minutes_left"] <= 60 and "tok" * 10 not in json.dumps(st)
    (tmp_path / "profile.json").write_text("{not json", encoding="utf-8")
    assert auth.load_profile() == {}


def test_licence_versions_are_not_numbers_that_need_a_claim():
    from storymap_gen.grounding import numbers_in
    assert numbers_in("Photo by someone, CC BY 4.0, and CC BY-SA 3.0; data under ODbL 1.0.") == []
    assert [raw for raw, _ in numbers_in("Photo CC BY 4.0 shows 12 trees")] == ["12"]


def test_swipe_layers_on_neither_side_show_on_both():
    spec = _spec([{"type": "swipe", "map": {"layers": ["boundary", "streets", "parks", "transit"]}, "left": ["parks"], "right": ["transit"]}])
    story = compile_story(spec)
    swipe = next(n for n in story["nodes"].values() if n["type"] == "swipe")
    left, right = (story["nodes"][swipe["data"]["contents"][k]] for k in "01")
    visible = lambda node: {layer["id"]: layer["visible"] for layer in node["data"]["mapLayers"]}  # noqa: E731
    assert visible(left) == {"layer-boundary": True, "layer-streets": True, "layer-parks": True, "layer-transit": False}
    assert visible(right) == {"layer-boundary": True, "layer-streets": True, "layer-parks": False, "layer-transit": True}


def test_layer_share_and_new_area_units(gracia):
    from storymap_gen.claims import AreaClaim, ShareClaim
    _, store = gracia
    assert AreaClaim(id="a", text="x", layer="boundary", unit="sq_km", expected=4.18, tolerance=0.005).check(store).status == "PASS"
    assert AreaClaim(id="b", text="x", layer="parks", unit="ha", expected=54.2, tolerance=0.05).check(store).status == "PASS"
    ok = ShareClaim(id="c", text="x", layer="streets", where="highway == 'living_street'", measure="length", expected_share=0.29, tolerance=0.005)
    assert ok.check(store).status == "PASS" and ok.stated_values() == [0.29, 29.0]
    assert ShareClaim(id="c", text="x", layer="streets", where="highway == 'living_street'", measure="length", expected_share=0.5).check(store).hint
    assert ShareClaim(id="d", text="x", layer="parks", where="name.notna()", of_where="leisure == 'park'", expected_share=0.5).check(store).status in ("PASS", "FAIL")
    assert "no polygons" in ShareClaim(id="e", text="x", layer="streets", where="highway == 'living_street'", measure="area", expected_share=0.5).check(store).detail \
        or "to measure" in ShareClaim(id="e", text="x", layer="streets", where="highway == 'living_street'", measure="area", expected_share=0.5).check(store).detail
