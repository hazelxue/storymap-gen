import copy

import pytest
from pydantic import ValidationError

from storymap_gen.claims import AreaClaim, OrientationClaim, ProximityClaim
from storymap_gen.compiler import compile_story
from storymap_gen.evaluate import check_graph
from storymap_gen.geo import GRID_NAME_PATTERN
from storymap_gen.spec import StorySpec


# ---- area -----------------------------------------------------------------
def area(**kw):
    base = dict(id="a", text="t", layer="parks_", unit="acres", expected=14.33, tolerance=0.05)
    return AreaClaim(**{**base, **kw})


def test_named_parks_reproduce_the_story_figure(jh_store):
    r = area(where="name.notna()").check(jh_store)
    assert r.status == "PASS" and r.measured == pytest.approx(14.32, abs=0.01)


def test_all_open_space_is_about_25_acres(jh_store):
    assert area(expected=24.67).check(jh_store).status == "PASS"


def test_wrong_area_fails_and_empty_filter_errors(jh_store):
    assert area(where="name.notna()", expected=20).check(jh_store).status == "FAIL"
    assert area(where="name == 'No Such Park'").check(jh_store).status == "ERROR"


def test_area_units_are_consistent(jh_store):
    acres = area(where="name.notna()").check(jh_store).measured
    sq_ft = area(where="name.notna()", unit="sq_ft", expected=acres * 43560, tolerance=50).check(jh_store)
    sq_m = area(where="name.notna()", unit="sq_m", expected=acres * 4046.8564224, tolerance=1).check(jh_store)
    assert sq_ft.status == "PASS" and sq_m.status == "PASS"


# ---- proximity ------------------------------------------------------------
def near(**kw):
    base = dict(id="p", text="t", layer="subway_stations_", target_layer="streets_centerline",
                target_name_field="full_stree", target_names=["ROOSEVELT AVE"], max_distance_m=50, expected_share=1.0)
    return ProximityClaim(**{**base, **kw})


def test_all_stations_are_within_50m_of_roosevelt(jh_store):
    r = near().check(jh_store)
    assert r.status == "PASS" and r.measured == 1.0


def test_proximity_distinguishes_streets(jh_store):
    far = near(target_names=["NORTHERN BLVD"], max_distance_m=10)  # stations are not on Northern Blvd
    r = far.check(jh_store)
    assert r.status == "FAIL" and r.measured < 1.0


def test_proximity_errors_are_reported_not_raised(jh_store):
    assert near(target_names=["NO SUCH STREET"]).check(jh_store).status == "ERROR"
    assert near(target_name_field=None).check(jh_store).status == "ERROR"


def test_share_must_be_a_fraction():
    with pytest.raises(ValidationError):
        near(expected_share=1.5)


# ---- orientation on another city (no numbered grid) ----------------------------
def orient(street, relation, **kw):
    return OrientationClaim(id="o", text="t", layer="eixample", name_field="name", street=street,
                            relation=relation, grid_regex=None, **kw)


def test_default_grid_definition_is_nyc_numbered_streets():
    assert OrientationClaim(id="o", text="t", street="X", relation="parallel_to_grid").grid_regex == GRID_NAME_PATTERN


def test_diagonal_is_diagonal_in_barcelona(eixample_store):
    r = orient("Avinguda Diagonal", "diagonal_to_grid").check(eixample_store)
    assert r.status == "PASS" and r.measured > 15


@pytest.mark.parametrize("street", ["Carrer d'Aragó", "Carrer de Mallorca", "Passeig de Gràcia", "Passeig de Sant Joan"])
def test_cerda_streets_follow_the_grid(eixample_store, street):
    r = orient(street, "parallel_to_grid").check(eixample_store)
    assert r.status == "PASS" and r.measured < 5


def test_wrong_relation_fails_in_barcelona(eixample_store):
    assert orient("Avinguda Diagonal", "parallel_to_grid").check(eixample_store).status == "FAIL"


def test_nyc_regex_on_a_city_without_numbered_streets_is_an_error_not_a_pass(eixample_store):
    c = OrientationClaim(id="o", text="t", layer="eixample", name_field="name", street="Avinguda Diagonal",
                         relation="diagonal_to_grid")  # default NYC grid_regex matches nothing here
    r = c.check(eixample_store)
    assert r.status == "ERROR" and "grid_regex" in r.detail


# ---- optional slide maps -------------------------------------------------------
def test_sidecar_requires_a_map_on_every_slide(example_specs):
    d = copy.deepcopy(example_specs["barcelona_eixample"])
    d["template"] = "docked-sidecar"
    with pytest.raises(ValidationError, match="needs a map"):
        StorySpec.model_validate(d)


def test_longform_compiles_without_maps(example_specs):
    story = compile_story(StorySpec.model_validate(example_specs["barcelona_eixample"]))
    assert check_graph(story) == []
    assert not any(n["type"] == "webmap" for n in story["nodes"].values())
