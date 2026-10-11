"""Graduated-colour maps, sub-area measures and table joins."""
import json

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon

from storymap_gen.__main__ import main
from storymap_gen.aggregate import aggregate, subareas_frame
from storymap_gen.api import map_problems
from storymap_gen.geo import DataStore
from storymap_gen.join import join_table, read_table, to_number
from storymap_gen.sources.geocode import Region
from storymap_gen.spec import MapRef, StorySpec
from storymap_gen.thematic import LayerStyle, class_breaks, numeric_values, palette_colors, renderer
from storymap_gen.webmap import build_webmap


def squares(values, name="id"):
    """A row of unit-ish squares near Barcelona, each with a value."""
    geoms = [Polygon([(2.0 + i * 0.01, 41.0), (2.01 + i * 0.01, 41.0), (2.01 + i * 0.01, 41.01), (2.0 + i * 0.01, 41.01)]) for i in range(len(values))]
    return gpd.GeoDataFrame({name: [f"a{i}" for i in range(len(values))], "v": values}, geometry=geoms, crs=4326)


# ---- classes and colours ---------------------------------------------------------------------------------------------
def test_quantile_and_equal_breaks_cover_the_values_and_increase():
    vals = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 100])
    q = class_breaks(vals, LayerStyle(field="v", classes=4))
    e = class_breaks(vals, LayerStyle(field="v", method="equal", classes=4))
    for edges in (q, e):
        assert edges[0] == 1 and edges[-1] == 100 and edges == sorted(set(edges))
    assert e == [1.0, 25.75, 50.5, 75.25, 100.0]
    assert q[1] < e[1]                        # quantiles follow the crowd, equal intervals follow the outlier


def test_repeated_values_give_fewer_classes_not_an_error():
    edges = class_breaks(pd.Series([1.0, 1, 1, 1, 1, 1, 2, 2, 3]), LayerStyle(field="v", classes=6))
    assert edges[0] == 1 and edges[-1] == 3 and len(edges) < 7 and edges == sorted(set(edges))
    with pytest.raises(ValueError, match="fewer than two"):
        class_breaks(pd.Series([5.0, 5.0, 5.0]), LayerStyle(field="v"))


def test_manual_breaks_are_validated():
    assert class_breaks(pd.Series([1.0, 9]), LayerStyle(field="v", method="manual", classes=2, breaks=[0, 5, 10])) == [0, 5, 10]
    for bad in ({"classes": 2, "breaks": [0, 5]}, {"classes": 2, "breaks": [0, 5, 5]}, {"classes": 2, "breaks": [0, float("nan"), 2]}):
        with pytest.raises(ValueError):
            LayerStyle(field="v", method="manual", **bad)
    with pytest.raises(ValueError, match="only apply"):
        LayerStyle(field="v", breaks=[1, 2, 3])


def test_palettes_run_light_to_dark_and_reverse():
    light_to_dark = palette_colors("blues", 5)
    assert len(light_to_dark) == 5 and sum(light_to_dark[0]) > sum(light_to_dark[-1])
    assert palette_colors("blues", 5, reverse=True) == light_to_dark[::-1]
    assert all(0 <= c <= 255 for rgb in palette_colors("heat", 7) for c in rgb)


def test_only_numbers_can_be_graded():
    with pytest.raises(ValueError, match="not a numeric"):
        numeric_values(pd.Series(["a", "b"], name="v"))
    assert list(numeric_values(pd.Series([1.0, float("nan"), float("inf"), 3.0]))) == [1.0, 3.0]
    with pytest.raises(ValueError, match="not a numeric"):
        numeric_values(pd.Series([True, False], name="v"))


def test_renderer_labels_and_no_data_class():
    r = renderer(pd.Series([10.0, 20, 30, 40, 5000]), LayerStyle(field="v", classes=2, unit=" per km2"), lambda rgba: {"color": rgba})
    assert r["type"] == "classBreaks" and r["field"] == "v" and len(r["classBreakInfos"]) == 2
    assert r["classBreakInfos"][0]["classMinValue"] == 10 and r["classBreakInfos"][-1]["classMaxValue"] == 5000
    assert r["classBreakInfos"][-1]["label"].endswith("5,000 per km2") and r["defaultLabel"] == "No data"
    assert r["classBreakInfos"][0]["symbol"]["color"][3] == 215 and r["defaultSymbol"]["color"][:3] == [200, 200, 200]


# ---- the Web Map -----------------------------------------------------------------------------------------------------
def test_a_graduated_polygon_layer_in_a_web_map():
    gdf = squares([1.0, 2, 3, 4, 50, None])
    wm, _ = build_webmap({"areas": gdf}, styles={"areas": LayerStyle(field="v", classes=3, palette="oranges", title="Value per km2")})
    layer = wm["operationalLayers"][0]["featureCollection"]["layers"][0]
    rend = layer["layerDefinition"]["drawingInfo"]["renderer"]
    assert rend["type"] == "classBreaks" and rend["field"] == "v" and len(rend["classBreakInfos"]) == 3
    assert next(f for f in layer["layerDefinition"]["fields"] if f["name"] == "v")["alias"] == "Value per km2"
    assert rend["classBreakInfos"][0]["symbol"]["type"] == "esriSFS"
    assert [f["attributes"]["v"] for f in layer["featureSet"]["features"]][-1] is None            # a missing value stays missing


def test_the_coloured_field_is_kept_even_when_it_is_beyond_the_first_twelve_columns():
    data = {f"c{i}": [1, 2, 3] for i in range(15)}
    data["late"] = [1.5, 2.5, 9.5]
    gdf = gpd.GeoDataFrame(data, geometry=squares([1, 2, 3]).geometry, crs=4326)
    wm, _ = build_webmap({"a": gdf}, styles={"a": LayerStyle(field="late", classes=2)})
    layer = wm["operationalLayers"][0]["featureCollection"]["layers"][0]
    assert layer["layerDefinition"]["fields"][1]["name"] == "late" and layer["popupInfo"]["fieldInfos"][0]["fieldName"] == "late"


def test_points_and_lines_can_be_graduated_too():
    pts = gpd.GeoDataFrame({"v": [1.0, 5, 9]}, geometry=[Point(2.0, 41.0), Point(2.01, 41.0), Point(2.02, 41.0)], crs=4326)
    lines = gpd.GeoDataFrame({"v": [1.0, 5, 9]}, geometry=[LineString([(2, 41 + i * 0.01), (2.02, 41 + i * 0.01)]) for i in range(3)], crs=4326)
    wm, _ = build_webmap({"p": pts, "l": lines}, styles={"p": LayerStyle(field="v", classes=2), "l": LayerStyle(field="v", classes=2)})
    kinds = {lay["layerDefinition"]["geometryType"]: lay["layerDefinition"]["drawingInfo"]["renderer"]["classBreakInfos"][0]["symbol"]["type"]
             for op in wm["operationalLayers"] for lay in op["featureCollection"]["layers"]}
    assert kinds == {"esriGeometryPoint": "esriSMS", "esriGeometryPolyline": "esriSLS"}


def test_a_missing_or_text_field_is_a_clear_error():
    with pytest.raises(ValueError, match="no column 'nope'"):
        build_webmap({"a": squares([1, 2, 3])}, styles={"a": LayerStyle(field="nope")})
    with pytest.raises(ValueError, match="not a numeric"):
        build_webmap({"a": squares([1, 2, 3])}, styles={"a": LayerStyle(field="id")})


# ---- the spec and its gate ---------------------------------------------------------------------------------------------
def test_styles_are_part_of_the_map_identity_and_must_name_layers_of_the_map():
    plain = MapRef(layers=["a"])
    styled = MapRef(layers=["a"], styles={"a": {"field": "v"}})
    other = MapRef(layers=["a"], styles={"a": {"field": "v", "palette": "reds"}})
    assert len({plain.key, styled.key, other.key}) == 3
    with pytest.raises(ValueError, match="not one of the map's layers"):
        MapRef(layers=["a"], styles={"b": {"field": "v"}})
    with pytest.raises(ValueError, match="only apply"):
        MapRef(item_id="a" * 32, styles={"a": {"field": "v"}})
    with pytest.raises(ValueError):
        MapRef(layers=["a"], styles={"a": {"field": "v", "colour": "red"}})          # unknown option: refused, not ignored


def test_the_gate_catches_a_style_that_cannot_work():
    store = DataStore({"areas": squares([1.0, 2, 3, 4])})
    def spec(style):
        return StorySpec.model_validate({"title": "T", "template": "longform", "sections": [{"title": "S", "blocks": [
            {"type": "map", "map": {"layers": ["areas"], "styles": {"areas": style}}}]}]})
    assert map_problems(spec({"field": "v"}), store) == []
    assert "no column 'nope'" in " ".join(map_problems(spec({"field": "nope"}), store))
    assert "not a numeric" in " ".join(map_problems(spec({"field": "id"}), store))
    uniform = DataStore({"areas": squares([3.0, 3.0, 3.0])})
    assert "fewer than two" in " ".join(map_problems(spec({"field": "v"}), uniform))


# ---- sub-areas -------------------------------------------------------------------------------------------------------
@pytest.fixture()
def district_store():
    west = Polygon([(2.0, 41.0), (2.02, 41.0), (2.02, 41.02), (2.0, 41.02)])
    east = Polygon([(2.02, 41.0), (2.06, 41.0), (2.06, 41.02), (2.02, 41.02)])
    areas = gpd.GeoDataFrame({"name": ["West", "East"]}, geometry=[west, east], crs=4326)
    stops = gpd.GeoDataFrame({"kind": ["bus", "bus", "bus", "metro"]}, crs=4326,
                             geometry=[Point(2.005, 41.005), Point(2.01, 41.01), Point(2.015, 41.015), Point(2.04, 41.01)])
    roads = gpd.GeoDataFrame({"n": [1]}, geometry=[LineString([(2.0, 41.01), (2.06, 41.01)])], crs=4326)
    parks = gpd.GeoDataFrame({"n": [1]}, geometry=[Polygon([(2.0, 41.0), (2.01, 41.0), (2.01, 41.02), (2.0, 41.02)])], crs=4326)
    return DataStore({"subareas": areas, "stops": stops, "roads": roads, "parks": parks})


def test_aggregate_counts_points_cuts_lines_and_shares_polygons(district_store):
    out = aggregate(district_store, "subareas", ["stops", "roads", "parks"])
    west, east = out.iloc[0], out.iloc[1]
    assert (west.stops_count, east.stops_count) == (3, 1)
    assert west.stops_per_km2 == pytest.approx(3 / west.area_km2, rel=1e-3)
    assert west.roads_km == pytest.approx(0.02 * 111.32 * 0.7547, rel=0.02) and east.roads_km == pytest.approx(2 * west.roads_km, rel=0.02)
    assert west.parks_pct == pytest.approx(50, abs=1) and east.parks_pct == 0
    assert out.area_km2.sum() == pytest.approx(west.area_km2 + east.area_km2)


def test_aggregate_can_filter_a_layer_first_and_refuses_odd_input(district_store):
    out = aggregate(district_store, "subareas", ["stops"], {"stops": "kind == 'metro'"})
    assert list(out.stops_count) == [0, 1]
    with pytest.raises(ValueError, match="polygons"):
        aggregate(district_store, "stops", ["roads"])
    with pytest.raises(KeyError):
        aggregate(district_store, "subareas", ["nope"])
    with pytest.raises(ValueError):
        aggregate(district_store, "subareas", ["stops"], {"stops": "__import__('os')"})


def test_a_combined_region_keeps_its_places_and_turns_them_into_a_layer(monkeypatch):
    from storymap_gen.sources import geocode as gc
    sq = lambda x: json.loads(json.dumps({"type": "Polygon", "coordinates": [[[x, 39.0], [x + 0.02, 39.0], [x + 0.02, 39.02], [x, 39.02], [x, 39.0]]]}))  # noqa: E731
    monkeypatch.setattr(gc, "geocode", lambda q: gc.Region(q, (117.0 if q.startswith("A") else 117.02, 39.0, 117.04, 39.02), boundary=sq(
        117.0 if q.startswith("A") else 117.02)))
    region = gc.geocode_many(["A District, City, Country", "B District, City, Country"], "Both")
    assert [p["name"] for p in region.parts] == ["A District", "B District"]
    again = Region.from_dict(json.loads(json.dumps(region.to_dict())))
    assert [p["name"] for p in again.parts] == ["A District", "B District"]
    frame = subareas_frame(again)
    assert list(frame.name) == ["A District", "B District"] and (frame.area_km2 > 3).all()
    with pytest.raises(ValueError, match="no sub-areas"):
        subareas_frame(Region("X", (0.0, 0.0, 0.1, 0.1)))
    assert Region.from_dict({**region.to_dict(), "parts": [{"name": "x" * 500, "boundary": {"type": "Point"}}, "junk"]}).parts == ()


# ---- joining a table ----------------------------------------------------------------------------------------------------
def test_numbers_are_read_tolerantly_and_never_guessed():
    s = pd.Series(["$54,300", "12.5%", "(3)", "1 234", "n/a", "", None, "7"])
    out = to_number(s)
    assert list(out.dropna()) == [54300, 12.5, -3, 1234, 7] and out.isna().sum() == 3


def tracts():
    return gpd.GeoDataFrame({"GEOID": ["06037", "06059", "06065", "06071"]}, geometry=squares([1, 2, 3, 4]).geometry, crs=4326)


def test_join_matches_keys_after_padding_and_reports_what_did_not_match(tmp_path):
    csv = tmp_path / "t.csv"
    csv.write_text("geoid,income,pop\n6037,\"$54,300\",1000\n6059,61000,2000\n6065,n/a,3000\n99999,1,1\n", encoding="utf-8")
    joined, rep = join_table(tracts(), read_table(csv), "GEOID", "geoid", ["income"], zfill=5)
    assert rep.matched == 3 and rep.unmatched_rows == 1 and rep.unmatched_polygons == ["06071"]
    assert list(joined.income.dropna()) == [54300, 61000] and "pop" not in joined.columns


def test_join_refuses_a_key_that_matches_almost_nothing_or_is_not_unique(tmp_path):
    csv = tmp_path / "t.csv"
    csv.write_text("geoid,income\n1,5\n2,6\n", encoding="utf-8")
    with pytest.raises(ValueError, match="only 0 of 4"):
        join_table(tracts(), read_table(csv), "GEOID", "geoid", ["income"])
    assert join_table(tracts(), read_table(csv), "GEOID", "geoid", ["income"], force=True)[1].matched == 0
    dup = tracts()
    dup.loc[1, "GEOID"] = "06037"
    with pytest.raises(ValueError, match="not unique"):
        join_table(dup, read_table(csv), "GEOID", "geoid", ["income"])
    with pytest.raises(ValueError, match="no column 'zzz'"):
        join_table(tracts(), read_table(csv), "GEOID", "geoid", ["zzz"])


def test_join_does_not_overwrite_an_existing_column(tmp_path):
    csv = tmp_path / "t.csv"
    csv.write_text("geoid,income\n06037,5\n06059,6\n06065,7\n06071,8\n", encoding="utf-8")
    layer = tracts()
    layer["income"] = [0, 0, 0, 0]
    joined, _ = join_table(layer, read_table(csv), "GEOID", "geoid", ["income"])
    assert list(joined.income) == [0, 0, 0, 0] and list(joined.income_joined) == [5, 6, 7, 8]


def test_tables_must_be_csv_and_a_sane_size(tmp_path):
    (tmp_path / "x.xlsx").write_text("x")
    with pytest.raises(ValueError, match=r"\.csv"):
        read_table(tmp_path / "x.xlsx")
    with pytest.raises(FileNotFoundError):
        read_table(tmp_path / "missing.csv")


# ---- the commands ---------------------------------------------------------------------------------------------------------
def test_the_join_command_end_to_end(tmp_path, capsys):
    layer = tmp_path / "tracts.geojson"
    tracts().to_file(layer, driver="GeoJSON")
    csv = tmp_path / "income.csv"
    csv.write_text("geoid,income\n6037,10\n6059,20\n6065,30\n6071,40\n", encoding="utf-8")
    out = tmp_path / "joined.geojson"
    assert main(["join", "--layer", str(layer), "--table", str(csv), "--key", "GEOID", "--table-key", "geoid", "--fields", "income",
                 "--zfill", "5", "--out", str(out)]) == 0
    assert "Joined 4 of 4" in capsys.readouterr().out
    assert sorted(gpd.read_file(out).income) == [10, 20, 30, 40]


def test_the_subarea_and_aggregate_commands_extend_a_manifest(tmp_path, capsys):
    import shutil
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "fixtures" / "tianjin"
    folder = tmp_path / "data"
    folder.mkdir()
    for name in ("boundary", "bus_stops", "transit"):
        shutil.copy(root / f"{name}.geojson", folder / f"{name}.geojson")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["layers"] = [rec for rec in manifest["layers"] if rec["name"] in ("boundary", "bus_stops", "transit")]
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert main(["subareas", "--region", str(root / "region.json"), "--manifest", str(folder / "manifest.json")]) == 0
    assert main(["aggregate", "--manifest", str(folder / "manifest.json"), "--layers", "bus_stops", "transit", "--where",
                 "transit=railway == 'station'", "--out-name", "stats"]) == 0
    out = capsys.readouterr().out
    assert "6 sub-areas" in out and "Heping District" in out and "bus_stops_per_km2" in out
    recorded = {r["name"]: r for r in json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["layers"]}
    assert set(recorded) >= {"subareas", "stats"} and recorded["stats"]["source"] == "derived"
    stats = gpd.read_file(folder / "stats.geojson")
    assert len(stats) == 6 and stats.bus_stops_count.sum() in range(1725, 1735) and stats.transit_count.sum() in range(100, 110)
    assert main(["aggregate", "--manifest", str(folder / "manifest.json"), "--layers", "bus_stops", "--out-name", "stats"]) == 0   # replaces, not duplicates
    assert [r["name"] for r in json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["layers"]].count("stats") == 1
