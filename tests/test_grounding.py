import copy

import pytest

from storymap_gen.compiler import compile_story
from storymap_gen.evaluate import evaluate
from storymap_gen.grounding import numbers_in, unbacked_numbers
from storymap_gen.spec import StorySpec
from storymap_gen.verify import verify_claims


@pytest.mark.parametrize("text, expected", [
    ("Planned in 1909 and rebuilt in the 1920s.", []),                      # years
    ("34th Avenue and 82nd Street.", []),                                  # ordinals
    ("The 34 AVE corridor and 37 Avenue.", []),                            # street names
    ("See Phase 1 and Map 3, Class II lanes.", []),                        # labels
    ("There are 1,001 segments averaging 32.6 feet.", ["1,001", "32.6"]),  # data numbers
    ("Parks cover 14.33 acres, 6 feet at the narrowest.", ["14.33", "6"]),
])
def test_numbers_in(text, expected):
    assert [raw for raw, _ in numbers_in(text)] == expected


def spec_with(**edits) -> StorySpec:
    from tests.test_storymap_gen import EXAMPLE
    d = copy.deepcopy(EXAMPLE)
    d.update(edits)
    return StorySpec.model_validate(d)


def test_example_has_no_unbacked_numbers():
    assert unbacked_numbers(spec_with()) == []


def test_number_that_matches_no_claim_is_flagged():
    from tests.test_storymap_gen import EXAMPLE
    d = copy.deepcopy(EXAMPLE)
    d["sections"][1]["slides"][0]["body"] = "The grid has 1,001 segments with an average width of 35 feet."
    flagged = unbacked_numbers(StorySpec.model_validate(d))
    assert [u.text for u in flagged] == ["35"]
    assert "The Street Grid" in flagged[0].location


def test_number_with_no_claim_attached_to_the_block_is_flagged():
    from tests.test_storymap_gen import EXAMPLE
    d = copy.deepcopy(EXAMPLE)
    d["sections"][1]["slides"][0]["claim_ids"] = []          # claims still exist, but are not attached here
    d["sections"][1]["claim_ids"] = []
    assert {u.text for u in unbacked_numbers(StorySpec.model_validate(d))} == {"1,001", "32.6"}


def test_section_level_claim_backs_slides_but_slide_claim_does_not_back_intro():
    from tests.test_storymap_gen import EXAMPLE
    d = copy.deepcopy(EXAMPLE)
    d["sections"][1]["intro"] = "It has 1,001 segments."
    d["sections"][1]["claim_ids"] = []                        # intro is only backed by section-level claims
    assert [u.text for u in unbacked_numbers(StorySpec.model_validate(d))] == ["1,001"]


def test_rounding_to_the_written_precision_is_allowed_but_not_further():
    from tests.test_storymap_gen import EXAMPLE
    d = copy.deepcopy(EXAMPLE)
    body = lambda s: d["sections"][1]["slides"][0].__setitem__("body", s)  # noqa: E731
    body("About 33 feet on average.")                        # 32.6 rounded to an integer
    assert unbacked_numbers(StorySpec.model_validate(d)) == []
    body("About 34 feet on average.")
    assert [u.text for u in unbacked_numbers(StorySpec.model_validate(d))] == ["34"]


def test_allow_numbers_escape_hatch():
    from tests.test_storymap_gen import EXAMPLE
    d = copy.deepcopy(EXAMPLE)
    d["sections"][0]["intro"] = "A community member quoted 89 million dollars."
    assert [u.text for u in unbacked_numbers(StorySpec.model_validate(d))] == ["89"]
    d["allow_numbers"] = [89]
    assert unbacked_numbers(StorySpec.model_validate(d)) == []


def test_gate_fails_on_unbacked_numbers(jh_store):
    from tests.test_storymap_gen import EXAMPLE
    d = copy.deepcopy(EXAMPLE)
    d["sections"][1]["slides"][0]["body"] = "The grid has 1,001 segments with an average width of 35 feet."
    spec = StorySpec.model_validate(d)
    rep = evaluate(spec, compile_story(spec), verify_claims(spec, jh_store))
    assert not rep["passed"] and rep["claims"]["fail"] == 0 and rep["unbacked_numbers"] == ["35 in slide 'The Street Grid'"]


# ---- placeholder text must never be published ------------------------------------------------------
@pytest.mark.parametrize("text", ["TODO: write this", "to be done TBD", "see [[claim]]", "Lorem ipsum dolor", "FIXME later"])
def test_placeholder_text_fails_the_gate(text, jh_store):
    from tests.test_storymap_gen import EXAMPLE
    d = copy.deepcopy(EXAMPLE)
    d["sections"][1]["slides"][0]["body"] = text
    spec = StorySpec.model_validate(d)
    rep = evaluate(spec, compile_story(spec), verify_claims(spec, jh_store))
    assert rep["placeholders"] and not rep["passed"]


def test_ordinary_words_that_contain_todo_are_not_flagged(jh_store):
    from tests.test_storymap_gen import EXAMPLE
    d = copy.deepcopy(EXAMPLE)
    d["sections"][0]["intro"] = "The mastodon todos and tbdx are fine, as is a standard sentence."
    spec = StorySpec.model_validate(d)
    assert evaluate(spec, compile_story(spec), verify_claims(spec, jh_store))["placeholders"] == []
