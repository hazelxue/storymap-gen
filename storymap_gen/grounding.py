"""Text-to-claim binding: every number written in prose must be backed by a verified claim.

The claim checker proves that a claim is true; it does not prove that the sentence in the story says the same
thing. This closes the gap: numbers in a section intro or slide body must match the value of a claim attached to
that block (or to the section). Every kind of text counts: section intros, paragraphs, list items, quotes, key figures, captions. Years, ordinals ("34th"), street names ("34 AVE"), and labels ("Phase 1") are
ignored; other numbers can be allowed explicitly with ``StorySpec.allow_numbers``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .claims import Claim, backs
from .spec import StorySpec, plain

NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?")
_ORDINAL = re.compile(r"^(st|nd|rd|th)\b", re.I)
_STREET_WORD = re.compile(r"^\s*(ave|avenue|st|street|blvd|boulevard|rd|road|pl|place|dr|drive|ln|lane)\b", re.I)
_LABEL = re.compile(r"(phase|map|figure|fig\.|class|slide|step|route|exit|chapter|version|CC[ -]BY(?:-(?:SA|NC|ND))*|ODbL|CC0)\s*$", re.I)


@dataclass(frozen=True)
class UnbackedNumber:
    location: str
    text: str


def numbers_in(text: str) -> list[tuple[str, float]]:
    """Numbers in ``text`` that look like data (as written, value)."""
    found = []
    for m in NUMBER.finditer(text):
        whole, frac = m.group(1), m.group(2) or ""
        raw = whole + frac
        after, before = text[m.end():], text[: m.start()]
        value = float(raw.replace(",", ""))
        if _ORDINAL.match(after) or _STREET_WORD.match(after) or _LABEL.search(before):
            continue
        if not frac and "," not in whole and 1700 <= value <= 2100:
            continue  # a year (also catches 1920s)
        found.append((raw, value))
    return found


def unbacked_numbers(spec: StorySpec) -> list[UnbackedNumber]:
    by_id: dict[str, Claim] = {c.id: c for c in spec.claims}
    allowed = set(spec.allow_numbers)
    out: list[UnbackedNumber] = []

    def check(text: str, location: str, claim_ids: list[str]) -> None:
        claims = [by_id[i] for i in claim_ids if i in by_id]
        for raw, value in numbers_in(text):
            if value in allowed or any(backs(c, value, raw) for c in claims):
                continue
            out.append(UnbackedNumber(location, raw))

    for item in spec.prose():
        if item.kind != "heading":
            check(plain(item.text), item.where, list(item.claim_ids))
    return out
