"""The findings battery: measured, ranked, and every claim it hands out passes."""
import re
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from storymap_gen.api import build, load_store
from storymap_gen.claims import ShareClaim
from storymap_gen.claims import ShareClaim as _Share  # noqa: F401
from storymap_gen.findings import BASE_LAYERS, all_claims, compute_findings, render_markdown
from storymap_gen.geo import DataStore
from storymap_gen.sources.manifest import read_manifest
from storymap_gen.spec import StorySpec

ROOT = Path(__file__).resolve().parent.parent
GRACIA = ROOT / "fixtures" / "gracia"


@pytest.fixture(scope="module")
def store():
    return load_store(read_manifest(GRACIA / "manifest.json").data_paths(GRACIA))


@pytest.fixture(scope="module")
def found(store):
    return compute_findings(store)


def test_every_claim_handed_out_passes_against_the_data(store, found):
    claims = all_claims(found)
    assert len(found) >= 20 and len(claims) >= 20
    spec = StorySpec.model_validate({"title": "T", "template": "longform", "claims": claims, "sections": [{"title": "S"}]})
    result = build(spec, store)
    assert [r.status for r in result.results] == ["PASS"] * len(claims), [(r.id, r.detail) for r in result.results if r.status != "PASS"]


def test_the_sentence_says_the_number_the_claim_checks(found):
    for f in found:
        shares = [c["expected_share"] for c in f.claims if "expected_share" in c]
        said = [int(m) for m in re.findall(r"(\d+)%", f.text)]
        assert said == [round(s * 100) for s in shares], (f.text, shares)


def test_it_finds_what_a_person_would_notice_in_gracia(found):
    texts = {f.id: f.text for f in found}
    assert "holds 90% of the parks" in texts["parks-skew-north"]            # the green space is in the north
    assert "holds 76% of the transit" in texts["transit-skew-west"]         # so are the stations, in the west
    assert "82%" in texts["parks-top3"] and "Parc Güell" in texts["parks-top3"]
    assert any(f.topic == "gap" for f in found) and any(f.topic == "relation" for f in found)
    first = [f.topic for f in found][:4]
    assert "scale" not in first                                              # notable results come before bare counts


def test_layers_that_are_everywhere_do_not_make_relations_or_reach_findings(found):
    for f in found:
        if f.topic in ("relation", "reach", "gap"):
            assert f.layer not in BASE_LAYERS
            assert all(c.get("target_layer") not in BASE_LAYERS and (c["layer"] not in BASE_LAYERS or c["layer"] == "boundary") for c in f.claims)


def test_a_relation_compares_with_what_a_typical_place_would_show(found):
    rel = next(f for f in found if f.topic == "relation")
    assert len(rel.claims) == 2 and rel.claims[1]["kind"] == "area_coverage" and "typical place" in rel.text


def test_the_markdown_lists_claim_ids_and_the_study_area(store, found):
    md = render_markdown(found, store)
    assert "Study area: 4.18 km2" in md and "`parks-skew-north`" in md and md.count("\n- ") >= 20


def test_it_is_deterministic_and_respects_the_limit(store):
    a, b = compute_findings(store, limit=12), compute_findings(store, limit=12)
    assert [f.id for f in a] == [f.id for f in b] and len(a) == 12


def test_without_a_boundary_only_boundary_free_findings_appear(store):
    bare = DataStore({k: v for k, v in store.layers.items() if k != "boundary"})
    found = compute_findings(bare)
    kinds = {c["kind"] for f in found for c in f.claims}
    assert found and not kinds & {"layer_density", "area_coverage"} and not any(c.get("half") for f in found for c in f.claims)


def test_small_odd_and_empty_inputs_do_not_crash():
    square = gpd.GeoDataFrame({"name": ["a"]}, geometry=[Polygon([(0, 0), (0.02, 0), (0.02, 0.02), (0, 0.02)])], crs=4326)
    pts = gpd.GeoDataFrame({"kind": ["x", "y", "x"]}, geometry=[Point(0.005, 0.005), Point(0.01, 0.01), Point(0.015, 0.015)], crs=4326)
    empty = gpd.GeoDataFrame({"kind": []}, geometry=[], crs=4326)
    assert compute_findings(DataStore({"boundary": square, "stops": pts, "nothing": empty}))
    assert compute_findings(DataStore({"stops": pts}))
    assert compute_findings(DataStore({"boundary": square})) == []


def test_share_claim_by_half_and_land_share(store):
    land = ShareClaim(id="a", text="x", layer="boundary", half="north", measure="area", expected_share=0.51, tolerance=0.005)
    assert land.check(store).status == "PASS"
    stops = ShareClaim(id="b", text="x", layer="bus_stops", half="north", expected_share=0.63, tolerance=0.005)
    assert stops.check(store).status == "PASS" and stops.stated_values() == [0.63, 63.0]
    assert "study-area layer" in ShareClaim(id="c", text="x", layer="bus_stops", half="north", expected_share=0.6).check(
        DataStore({k: v for k, v in store.layers.items() if k != "boundary"})).detail
    with pytest.raises(ValueError, match="where"):
        ShareClaim(id="d", text="x", layer="bus_stops", expected_share=0.5)


# ---- several places as one study area ----------------------------------------------------------------------------------
def _square(x, y, d=0.02):
    from shapely.geometry import mapping
    return mapping(Polygon([(x, y), (x + d, y), (x + d, y + d), (x, y + d)]))


def test_a_stray_piece_of_a_boundary_is_dropped_and_the_rest_kept():
    from shapely.geometry import MultiPolygon, mapping, shape

    from storymap_gen.sources.geocode import drop_stray_parts
    main = shape(_square(117.2, 39.1, 0.1))
    near = shape(_square(117.31, 39.1, 0.01))      # an exclave a kilometre away stays
    far = shape(_square(113.7, 36.6, 0.05))        # a mapping mistake hundreds of km away goes
    out, km2 = drop_stray_parts(mapping(MultiPolygon([main, near, far])))
    assert shape(out).geom_type == "MultiPolygon" and len(shape(out).geoms) == 2 and km2 > 20
    one, none = drop_stray_parts(mapping(MultiPolygon([main, near])))
    assert none == 0.0 and len(shape(one).geoms) == 2
    assert drop_stray_parts(_square(0, 0)) == (_square(0, 0), 0.0)


def test_several_places_become_one_region_with_the_union_of_their_boundaries(monkeypatch):
    from shapely.geometry import shape

    from storymap_gen.sources import geocode as gc

    def fake(query):
        x = {"A": 117.0, "B": 117.02}[query]
        return gc.Region(query, (x, 39.0, x + 0.02, 39.02), display_name=query, boundary=_square(x, 39.0))

    monkeypatch.setattr(gc, "geocode", fake)
    region = gc.geocode_many(["A", "B"], "Both")
    assert region.name == "Both" and region.bbox == pytest.approx((117.0, 39.0, 117.04, 39.02))
    assert shape(region.boundary).area == pytest.approx(0.0008, rel=0.01)       # the two squares touch: one polygon
    assert gc.geocode_many(["A"]).name == "A"
    with pytest.raises(ValueError, match="between one and twelve"):
        gc.geocode_many([])
    monkeypatch.setattr(gc, "geocode", lambda q: gc.Region(q, (117.0, 39.0, 117.02, 39.02)))
    with pytest.raises(ValueError, match="needs a boundary"):
        gc.geocode_many(["A", "B"])
