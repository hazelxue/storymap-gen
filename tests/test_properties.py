"""Property tests: for ANY valid spec, the compiled story must be a valid, deterministic graph."""
import html
import re

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from storymap_gen.compiler import compile_story
from storymap_gen.evaluate import CITE, check_graph
from storymap_gen.spec import StorySpec

TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=1, max_size=40)
ITEM_ID = st.text(alphabet="0123456789abcdef", min_size=32, max_size=32)
KEYS = ["a", "b", "c", "d"]


@st.composite
def specs(draw):
    template = draw(st.sampled_from(["docked-sidecar", "floating-sidecar", "longform"]))
    sources = [{"key": k, "text": draw(TEXT)} for k in KEYS[: draw(st.integers(0, 4))]]
    keys = [s["key"] for s in sources]
    sections = []
    for _ in range(draw(st.integers(1, 4))):
        slides = []
        for _ in range(draw(st.integers(0, 3))):
            slide = {"title": draw(TEXT), "body": draw(TEXT)}
            if template != "longform" or draw(st.booleans()):
                slide["map"] = {"item_id": draw(ITEM_ID), "caption": draw(TEXT)}
            slides.append(slide)
        sections.append({"title": draw(TEXT), "intro": draw(st.one_of(st.just(""), TEXT)),
                         "cites": draw(st.lists(st.sampled_from(keys), unique=True)) if keys else [],
                         "slides": slides})
    return StorySpec.model_validate({"title": draw(TEXT), "subtitle": draw(TEXT), "template": template,
                                     "sources": sources, "sections": sections})


PROPS = settings(max_examples=75, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@PROPS
@given(specs())
def test_compiled_graph_is_always_valid(spec):
    assert check_graph(compile_story(spec)) == []


@PROPS
@given(specs())
def test_compilation_is_deterministic(spec):
    assert compile_story(spec) == compile_story(spec)


@PROPS
@given(specs())
def test_user_text_is_escaped_and_round_trips(spec):
    story = compile_story(spec)
    headings = [n["data"]["text"] for n in story["nodes"].values()
                if n["type"] == "text" and n["data"]["type"] == "h2"]
    assert [html.unescape(h) for h in headings][: len(spec.sections)] == [s.title for s in spec.sections]
    for n in story["nodes"].values():
        if n["type"] == "text" and n["data"]["type"] not in ("numbered-list", "bullet-list"):
            # the only tags that can appear are the ones the small markup produces; whatever the user typed is escaped
            leftover = re.sub(r"</?(?:strong|em)>|<a href=\"https://[^\"<>]*\" rel=\"noopener noreferrer\" target=\"_blank\">|</a>", "", n["data"]["text"])
            assert "<" not in leftover and ">" not in leftover


@PROPS
@given(specs())
def test_citation_markers_stay_in_range_and_navigation_matches_sections(spec):
    story = compile_story(spec)
    for n in story["nodes"].values():
        if n["type"] == "text" and n["data"]["type"] == "paragraph":
            assert all(1 <= int(m) <= len(spec.sources) for m in CITE.findall(n["data"]["text"]))
    nav = next(n for n in story["nodes"].values() if n["type"] == "navigation")
    assert len(nav["data"]["links"]) == len(spec.sections)
