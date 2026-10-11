"""Every example in examples/ is a promise in the docs; this pins what each one does."""
import json

import pytest

from storymap_gen.__main__ import main
from storymap_gen.api import build, load_store
from storymap_gen.evaluate import check_graph
from tests.conftest import FIXTURES, ROOT

JH = {"streets_centerline": FIXTURES / "streets_centerline.geojson"}
JH3 = {**JH, "parks_": FIXTURES / "parks.geojson", "subway_stations_": FIXTURES / "subway_stations.geojson"}
GRACIA = {n: FIXTURES / "gracia" / f"{n}.geojson" for n in ("streets", "parks", "transit", "schools")}
GRACIA_ALL = {n: FIXTURES / "gracia" / f"{n}.geojson" for n in ("boundary", "streets", "parks", "transit", "schools", "bike", "bus_stops", "health", "landuse")}
TIANJIN = {n: FIXTURES / "tianjin" / f"{n}.geojson" for n in ("boundary", "streets", "transit", "bus_stops", "bike", "schools", "health", "water", "subareas", "subareas_stats")}
EIX = {"eixample": FIXTURES / "eixample_streets.geojson"}

PASSING = {"jackson_heights": JH, "jackson_heights_open_space": JH3, "barcelona_eixample": EIX, "gracia_barcelona": GRACIA, "gracia_barcelona_full": GRACIA_ALL, "tianjin_transport": TIANJIN}


def test_every_example_is_covered_here(example_specs):
    assert set(example_specs) == set(PASSING) | {"jackson_heights_broken"}


@pytest.mark.parametrize("name", sorted(PASSING))
def test_passing_examples_pass_the_whole_gate(name, example_specs):
    result = build(example_specs[name], load_store(PASSING[name], root=ROOT))
    assert result.passed, result.report
    assert check_graph(result.story) == [] and all(r.status == "PASS" for r in result.results)


def test_the_broken_example_fails_for_exactly_the_documented_reasons(example_specs):
    result = build(example_specs["jackson_heights_broken"], load_store(JH, root=ROOT))
    assert {r.id: r.status for r in result.results} == {"northern-diagonal": "FAIL", "broadway-diagonal": "ERROR", "avg-width": "PASS"}
    assert result.report["unbacked_numbers"] and not result.passed and result.story is None


def test_the_region_example_uses_generated_maps_and_the_manifest_sources(example_specs, tmp_path, capsys):
    out = tmp_path / "s.json"
    code = main(["run", str(ROOT / "examples" / "gracia_barcelona.json"), "--manifest", str(FIXTURES / "gracia" / "manifest.json"),
                 "--out", str(out)])
    assert code == 0
    story = json.loads(out.read_text(encoding="utf-8"))
    maps = [n for n in story["nodes"].values() if n["type"] == "webmap"]
    assert len(maps) == 5 and all(m["data"]["map"].startswith("r-") for m in maps)       # one per slide; ids are placeholders until publishing
    manifest = json.loads((FIXTURES / "gracia" / "manifest.json").read_text(encoding="utf-8"))
    assert example_specs["gracia_barcelona"]["sources"][0]["key"] == "osm" and manifest["layers"][0]["source"] == "osm"


def test_clipping_made_the_region_data_describe_the_district_not_the_download_box():
    """Regression: unclipped OpenStreetMap parks for this district summed to 21,717 acres (a nature reserve covering a third of the city)."""
    store = load_store({"parks": GRACIA["parks"]}, root=ROOT)
    from storymap_gen.claims import AreaClaim
    area = AreaClaim(id="a", text="t", layer="parks", expected=134.0, tolerance=1).check(store)
    assert area.status == "PASS" and area.measured < 200


def test_the_full_example_is_a_rich_story_with_nothing_unbacked_and_no_thin_spots(example_specs):
    result = build(example_specs["gracia_barcelona_full"], load_store(GRACIA_ALL, root=ROOT))
    assert result.passed and result.report["unbacked_numbers"] == [] and result.report["map_problems"] == []
    depth = result.report["depth"]
    assert depth["warnings"] == [], depth["warnings"]
    assert depth["words"] >= 1500 and depth["sections"] >= 5 and depth["subsections"] >= 10 and depth["maps"] >= 7
    assert depth["swipes"] == 2 and depth["figures"] == 2 and depth["images"] == 2 and depth["claims"] >= 40
    types = {n["type"] for n in result.story["nodes"].values()}
    assert {"swipe", "large-number", "grid", "image", "storycover"} <= types
    assert any(n["type"] == "text" and n["data"]["type"] == "bullet-list" for n in result.story["nodes"].values())
    cover = next(n for n in result.story["nodes"].values() if n["type"] == "storycover")
    assert cover["data"]["type"] == "full" and cover["children"]
    assert all(r.status == "PASS" for r in result.results)


def test_the_tianjin_example_works_without_any_hand_tuning_for_the_place(example_specs):
    """A non-Latin-script city, six districts as one study area, a transport story: written from `findings`, passing the whole gate."""
    result = build(example_specs["tianjin_transport"], load_store(TIANJIN, root=ROOT))
    assert result.passed and result.report["unbacked_numbers"] == [] and result.report["map_problems"] == []
    assert result.report["depth"]["warnings"] == [] and result.report["depth"]["words"] >= 1500
    assert all(r.status == "PASS" for r in result.results) and len(result.results) >= 40
