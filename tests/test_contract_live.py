"""Contract test against a LIVE public StoryMap (needs network): run with `pytest -m network`.

StoryMaps item JSON is not an officially documented API, so our compiler's output is checked against a story that
the live service currently serves. If Esri changes the format, this fails before users notice.
"""
import json

import pytest
import requests

from storymap_gen.compiler import compile_story
from storymap_gen.evaluate import check_graph
from storymap_gen.spec import StorySpec

pytestmark = pytest.mark.network

REFERENCE_ITEM = "1c0c4523c6dc4c999a11ad712ec33d08"  # a public, published story used as the format reference
URL = f"https://www.arcgis.com/sharing/rest/content/items/{REFERENCE_ITEM}/data?f=json"


@pytest.fixture(scope="module")
def live_story():
    r = requests.get(URL, timeout=60)
    r.raise_for_status()
    return r.json()


def test_live_story_is_still_a_valid_graph_by_our_checker(live_story):
    assert check_graph(live_story) == []


@pytest.mark.parametrize("template", ["docked-sidecar", "floating-sidecar", "longform"])
def test_what_we_emit_is_a_subset_of_what_the_live_service_serves(live_story, template, example_specs):
    live: dict[str, tuple[set, set]] = {}
    for n in live_story["nodes"].values():
        d, c = live.setdefault(n["type"], (set(), set()))
        d |= set(n.get("data", {}))
        c |= set(n.get("config", {}))
    spec = {**example_specs["jackson_heights"], "template": template}
    for n in compile_story(StorySpec.model_validate(spec))["nodes"].values():
        if template == "floating-sidecar" and n["type"] == "immersive":
            continue  # 'floating-panel' is not used by the reference story; it was verified in the Builder instead
        assert n["type"] in live, f"live service no longer serves node type '{n['type']}'"
        assert set(n.get("data", {})) <= live[n["type"]][0] | {"subtype"}, n["type"]
        assert set(n.get("config", {})) <= live[n["type"]][1], n["type"]


def test_local_fixture_and_live_story_agree_on_node_types(live_story):
    local = json.loads(open("fixtures/jackson_heights_story.json", encoding="utf-8").read())
    assert {n["type"] for n in local["nodes"].values()} <= {n["type"] for n in live_story["nodes"].values()}
