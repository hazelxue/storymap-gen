"""Robustness: hostile or broken input must produce a controlled error, never a crash, a hang, or a silent pass."""
import copy
import json
import time

import geopandas as gpd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError
from shapely.geometry import LineString, Point, Polygon

from storymap_gen.api import build
from storymap_gen.claims import Claim, ClaimResult
from storymap_gen.compiler import compile_story
from storymap_gen.evaluate import check_graph
from storymap_gen.geo import DataError, DataStore, read_layer
from storymap_gen.spec import StorySpec
from storymap_gen.verify import verify_claims

FUZZ = settings(max_examples=150, deadline=2000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])

JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=40),
    lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=10), c, max_size=4),
    max_leaves=12,
)


# ---- 1. the spec parser: garbage in, ValidationError out ----------------------------------------------------
@FUZZ
@given(JSON)
def test_arbitrary_json_never_crashes_the_spec_parser(value):
    try:
        StorySpec.model_validate(value)
    except ValidationError:
        pass


@FUZZ
@given(st.dictionaries(st.sampled_from(["title", "sections", "claims", "sources", "template", "theme", "locale", "allow_numbers",
                                        "cover_image_url", "byline", "subtitle", "extra"]), JSON, max_size=8))
def test_plausible_keys_with_garbage_values_never_crash_the_parser(value):
    try:
        StorySpec.model_validate(value)
    except ValidationError:
        pass


# ---- 2. mutation fuzzing of a valid spec through the whole pipeline ---------------------------------------------
def _paths(obj, prefix=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _paths(v, prefix + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _paths(v, prefix + (i,))
    else:
        yield prefix


def _set(root, path, value):
    for key in path[:-1]:
        root = root[key]
    root[path[-1]] = value


@FUZZ
@given(data=st.data())
def test_mutated_valid_specs_never_crash_the_pipeline(data, example_specs, jh_store):
    spec = copy.deepcopy(example_specs["jackson_heights_open_space"])
    paths = list(_paths(spec))
    for _ in range(data.draw(st.integers(1, 3))):
        _set(spec, data.draw(st.sampled_from(paths)), data.draw(JSON))
    try:
        result = build(spec, jh_store)
    except ValidationError:
        return
    # if the pipeline accepted it, everything downstream must be coherent
    assert isinstance(result.passed, bool)
    if result.story is not None:
        assert check_graph(result.story) == []


# ---- 3. claims: random parameters never raise out of verify_claims ------------------------------------------------
CLAIMS = TypeAdapter(Claim)
WEIRD = st.one_of(st.none(), st.booleans(), st.integers(-10**12, 10**12), st.floats(allow_nan=False, allow_infinity=False, width=32),
                  st.text(max_size=30), st.lists(st.text(max_size=8), max_size=4))
LAYERS = st.sampled_from(["streets_centerline", "parks_", "subway_stations_", "missing"])


def _claim_dicts():
    common = {"id": st.just("c"), "text": st.just("t")}
    return st.one_of(
        st.fixed_dictionaries({**common, "kind": st.just("street_orientation"), "layer": LAYERS, "name_field": WEIRD, "street": WEIRD,
                               "relation": st.sampled_from(["diagonal_to_grid", "parallel_to_grid", "x"]), "tolerance_deg": WEIRD,
                               "grid_regex": WEIRD}),
        st.fixed_dictionaries({**common, "kind": st.just("layer_stat"), "layer": LAYERS, "field": WEIRD,
                               "agg": st.sampled_from(["count", "min", "max", "mean", "sum", "median"]), "expected": WEIRD,
                               "tolerance": WEIRD, "where": WEIRD}),
        st.fixed_dictionaries({**common, "kind": st.just("layer_area"), "layer": LAYERS, "where": WEIRD,
                               "unit": st.sampled_from(["acres", "sq_ft", "sq_m", "km"]), "expected": WEIRD, "tolerance": WEIRD}),
        st.fixed_dictionaries({**common, "kind": st.just("within_distance"), "layer": LAYERS, "target_layer": LAYERS,
                               "target_name_field": WEIRD, "target_names": WEIRD, "max_distance_m": WEIRD, "expected_share": WEIRD}),
    )


@FUZZ
@given(claim=_claim_dicts())
def test_random_claims_are_rejected_or_checked_but_never_crash(claim, jh_store):
    try:
        parsed = CLAIMS.validate_python(claim)
    except ValidationError:
        return
    spec = StorySpec.model_validate({"title": "t", "template": "longform", "sections": [{"title": "s"}], "claims": [parsed.model_dump()]})
    results = verify_claims(spec, jh_store)
    assert len(results) == 1 and isinstance(results[0], ClaimResult) and results[0].status in {"PASS", "FAIL", "ERROR"}


# ---- 4. broken data files --------------------------------------------------------------------------------------
def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return p


@pytest.mark.parametrize("content", [
    b"", b"not json at all", b'{"type": "FeatureCollection", "features": [', b"\x00\x01\x02\xff\xfe" * 100,
    b'{"type": "FeatureCollection", "features": "nope"}', b"[]", b"null", b"<html><body>404</body></html>",
])
def test_corrupt_data_files_give_a_clean_data_error(tmp_path, content):
    with pytest.raises(DataError):
        read_layer(_write(tmp_path, "bad.geojson", content), "x")


def test_missing_file_and_oversized_file_are_refused(tmp_path, monkeypatch):
    with pytest.raises(DataError, match="does not exist"):
        read_layer(tmp_path / "nope.geojson", "x")
    big = _write(tmp_path, "big.geojson", b'{"type":"FeatureCollection","features":[]}' + b" " * 50_000)
    monkeypatch.setenv("STORYMAP_MAX_DATA_MB", "0.01")
    with pytest.raises(DataError, match="larger than"):
        read_layer(big, "x")


def test_missing_crs_is_assumed_wgs84_only_when_the_numbers_look_like_lon_lat():
    ok = gpd.GeoDataFrame({"n": ["a"]}, geometry=[Point(2.17, 41.39)])
    assert ok.crs is None
    assert read_layer_from_frame(ok).crs.to_epsg() == 4326


def read_layer_from_frame(gdf, tmp=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = f"{d}/f.geojson"
        gdf.set_crs(4326).to_file(p, driver="GeoJSON")
        # write the raw coordinates without a CRS member to emulate a CRS-less file
        data = json.loads(open(p, encoding="utf-8").read())
        data.pop("crs", None)
        open(p, "w", encoding="utf-8").write(json.dumps(data))
        return read_layer(p, "x")


def test_projected_coordinates_without_a_crs_are_refused(tmp_path):
    p = _write(tmp_path, "m.geojson", json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [583960.0, 4507523.0]}}]}))
    with pytest.raises(DataError, match="outside the longitude/latitude range"):
        read_layer(p, "x")


def _orientation_spec(layer):
    return StorySpec.model_validate({"title": "t", "template": "longform", "sections": [{"title": "s"}], "claims": [
        {"kind": "street_orientation", "id": "o", "text": "t", "layer": layer, "name_field": "name", "street": "A",
         "relation": "diagonal_to_grid", "grid_regex": None},
        {"kind": "layer_area", "id": "a", "text": "t", "layer": layer, "expected": 1},
        {"kind": "layer_stat", "id": "s", "text": "t", "layer": layer, "field": "name", "agg": "mean", "expected": 1},
        {"kind": "within_distance", "id": "w", "text": "t", "layer": layer, "target_layer": layer, "max_distance_m": 10,
         "expected_share": 1}]})


@pytest.mark.parametrize("name, geoms", [
    ("empty", []),
    ("all-null-geometry", [None, None]),
    ("points-only", [Point(2.17, 41.39), Point(2.18, 41.40)]),
    ("polygons-only", [Polygon([(2.17, 41.39), (2.18, 41.39), (2.18, 41.40)]), Polygon([(2.1, 41.3), (2.2, 41.3), (2.2, 41.4)])]),
    ("degenerate-line", [LineString([(2.17, 41.39), (2.17, 41.39)]), LineString([(2.17, 41.39), (2.17, 41.39)])]),
    ("single-line", [LineString([(2.17, 41.39), (2.18, 41.40)])]),
    ("mixed", [Point(2.17, 41.39), LineString([(2.17, 41.39), (2.18, 41.40)]), Polygon([(2.17, 41.39), (2.18, 41.39), (2.18, 41.40)])]),
])
def test_odd_layers_yield_error_or_result_but_never_an_exception(name, geoms):
    gdf = gpd.GeoDataFrame({"name": ["A"] * len(geoms)}, geometry=gpd.GeoSeries(geoms, crs=4326)) if geoms else \
        gpd.GeoDataFrame({"name": []}, geometry=gpd.GeoSeries([], crs=4326))
    results = verify_claims(_orientation_spec("L"), DataStore({"L": gdf}))
    assert len(results) == 4 and all(r.status in {"PASS", "FAIL", "ERROR"} for r in results)
    assert all(r.status != "PASS" for r in results if r.id in {"o"} and name in {"empty", "all-null-geometry", "points-only"})


def test_unknown_layer_is_an_error_result(jh_store):
    results = verify_claims(_orientation_spec("nope"), jh_store)
    assert [r.status for r in results] == ["ERROR"] * 4 and "not loaded" in results[0].detail


# ---- 5. scale: the largest allowed spec stays fast ----------------------------------------------------------------
def test_largest_allowed_spec_compiles_quickly_and_validly():
    slide = {"title": "s" * 50, "body": "b" * 100, "map": {"item_id": "a" * 32, "caption": "c"}}
    spec = StorySpec.model_validate({"title": "t", "sections": [{"title": f"S{i}", "intro": "i" * 500, "slides": [slide] * 50}
                                                                for i in range(50)]})
    t0 = time.perf_counter()
    story = compile_story(spec)
    assert check_graph(story) == [] and time.perf_counter() - t0 < 10
    assert len(story["nodes"]) > 10_000


def test_pathological_text_is_handled(example_specs):
    d = copy.deepcopy(example_specs["jackson_heights_open_space"])
    d["sections"][0]["intro"] = "‮\u0000😀 <script>alert(1)</script> &amp;   ​" + "9" * 400 + " 1e999 -0 NaN"
    story = compile_story(StorySpec.model_validate(d))
    assert check_graph(story) == []
    texts = [n["data"]["text"] for n in story["nodes"].values() if n["type"] == "text"]
    assert not any("<script>" in t for t in texts)
