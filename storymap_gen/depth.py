"""How full is the story? Counts what a reader gets and warns where it is thin.

The yardsticks come from a study of 31 public student district StoryMaps (see docs/case-study-patterns.md): the median
story had about 3,000 words, 5 main sections, 11 sub-headings, 7 maps (every one captioned) and 22 pictures. A story that
only passes the claim checks can still be a few sentences long; these warnings say so. They never fail the gate, because
a short story is sometimes the right one, but the skill treats them as a to-do list.
"""
from __future__ import annotations

from typing import Any

from .spec import (
    BulletsBlock,
    FiguresBlock,
    HeadingBlock,
    ImageBlock,
    MapBlock,
    ParagraphBlock,
    QuoteBlock,
    SlidesBlock,
    StorySpec,
    SwipeBlock,
    plain,
)

PROFILES: dict[str, dict[str, int]] = {
    "brief": {"words": 500, "sections": 3, "subsections": 0, "maps": 3},
    "standard": {"words": 1500, "sections": 4, "subsections": 4, "maps": 5},
    "full": {"words": 3000, "sections": 5, "subsections": 8, "maps": 7},
}
READ_KINDS = {"intro", "slide", "paragraph", "bullet", "quote"}
LONG_PARAGRAPH_WORDS = 220
TEXT_ONLY_WORDS = 450
THIN_SUBSECTION_WORDS = 40


def words(text: str) -> int:
    return len(plain(text).split())


def _maps_of(spec: StorySpec) -> list[tuple[str, str]]:
    """(where, caption) for every map a reader sees: slide maps, map blocks and swipes."""
    out: list[tuple[str, str]] = []
    for sec in spec.sections:
        out += [(f"slide '{sl.title}'", sl.map.caption) for sl in sec.all_slides() if sl.map is not None]
        for b in sec.blocks:
            if isinstance(b, MapBlock):
                out.append((f"section '{sec.title}' map", b.map.caption))
            elif isinstance(b, SwipeBlock):
                out.append((f"section '{sec.title}' swipe", b.caption))
    return out


def depth_report(spec: StorySpec) -> dict[str, Any]:
    prose = spec.prose()
    total_words = sum(words(p.text) for p in prose if p.kind in READ_KINDS)
    maps = _maps_of(spec)
    claim_use = {c.id: 0 for c in spec.claims}
    subsections = h4 = images = figures = swipes = lists = 0
    warnings: list[str] = []
    for sec in spec.sections:
        sec_words = len(plain(sec.intro).split()) + sum(words(sl.body) for sl in sec.all_slides())
        visuals = len(sec.all_slides()) + (1 if sec.image_url else 0)
        used = set(sec.claim_ids) | {i for sl in sec.all_slides() for i in sl.claim_ids}
        current: tuple[str, int] | None = None  # (heading, words since it)

        def close() -> None:
            nonlocal current
            if current and current[1] < THIN_SUBSECTION_WORDS:
                warnings.append(f"subsection '{current[0]}' has only {current[1]} words; say what the data shows and why it matters")
            current = None

        for b in sec.blocks:
            used |= set(b.claim_ids)
            n = 0
            if isinstance(b, HeadingBlock):
                if b.level == 3:
                    close()
                    subsections += 1
                    current = (b.text, 0)
                else:
                    h4 += 1
            elif isinstance(b, ParagraphBlock):
                n = words(b.text)
                if n > LONG_PARAGRAPH_WORDS:
                    warnings.append(f"a paragraph in section '{sec.title}' has {n} words; split it or add a map or list")
            elif isinstance(b, BulletsBlock):
                n = sum(words(i) for i in b.items)
                lists += 1
            elif isinstance(b, QuoteBlock):
                n = words(b.text)
            elif isinstance(b, FiguresBlock):
                figures += 1
                used |= {f.claim_id for f in b.items if f.claim_id}
                visuals += 1
            elif isinstance(b, ImageBlock):
                images += 1
                visuals += 1
                if not b.attribution:
                    warnings.append(f"a picture in section '{sec.title}' has no credit or licence")
            elif isinstance(b, (MapBlock, SwipeBlock)):
                visuals += 1
                swipes += isinstance(b, SwipeBlock)
            elif isinstance(b, SlidesBlock):
                visuals += len(b.slides)
                for sl in b.slides:
                    n += words(sl.body)
                    used |= set(sl.claim_ids)
            sec_words += n
            if current is not None:
                current = (current[0], current[1] + n)
        close()
        for i in used:
            if i in claim_use:
                claim_use[i] += 1
        if sec_words and not used:
            warnings.append(f"section '{sec.title}' states no measured fact (no claim is attached to it)")
        if sec_words > TEXT_ONLY_WORDS and not visuals:
            warnings.append(f"section '{sec.title}' is {sec_words} words of text with no map, figure or picture")
    for where, caption in maps:
        if not caption.strip():
            warnings.append(f"{where} has no caption; say what the reader should look at")
    profile = PROFILES[spec.depth]
    got = {"words": total_words, "sections": len(spec.sections), "subsections": subsections, "maps": len(maps)}
    for key, want in profile.items():
        if got[key] < want:
            warnings.insert(0, f"{key}: {got[key]} (a '{spec.depth}' story has at least {want})")
    unused = [c for c, n in claim_use.items() if n == 0]
    if unused:
        warnings.append(f"claims never attached to any text: {', '.join(unused)}")
    return {
        "profile": spec.depth,
        "words": total_words, "sections": len(spec.sections), "subsections": subsections, "sub_subsections": h4,
        "maps": len(maps), "swipes": swipes, "figures": figures, "lists": lists, "images": images,
        "claims": len(spec.claims), "sources": len(spec.sources),
        "warnings": warnings,
    }
