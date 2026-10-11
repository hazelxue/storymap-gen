"""Claim verification: check statements in the story against the underlying GIS data.

Motivating bug: a story marked Northern Boulevard as the street that "breaks the grid". Measuring segment
bearings showed it is parallel to the avenues; Broadway is the genuinely diagonal street. This turns that manual
check into a repeatable test that runs before a story is compiled or published.
"""
from __future__ import annotations

from .claims import ClaimResult
from .geo import GRID_NAME, DataStore, deviation_from_grid, grid_axis, segments, weighted_median
from .spec import StorySpec

__all__ = ["ClaimResult", "DataStore", "GRID_NAME", "verify_claims", "orientation_facts"]


def verify_claims(spec: StorySpec, store: DataStore) -> list[ClaimResult]:
    out = []
    for c in spec.claims:
        try:
            out.append(c.check(store))
        except Exception as e:  # a broken claim must not hide the others
            out.append(ClaimResult(c.id, "ERROR", f"{type(e).__name__}: {e}"))
    return out


def orientation_facts(store: DataStore, streets: list[str], layer: str = "streets_centerline",
                      name_field: str = "full_stree", tolerance_deg: float = 15.0) -> str:
    """Measured street-vs-grid facts, to be given to the writer BEFORE it drafts (verify-first)."""
    gdf = store.get(layer)
    names = gdf[name_field].fillna("").str.upper().str.strip()
    gl, gb = segments(gdf[names.str.match(GRID_NAME)])
    axis = grid_axis(gl, gb)
    rows = []
    for st in streets:
        tl, tb = segments(gdf[names == st.upper().strip()])
        rows.append(f"{st.upper()}: {weighted_median(deviation_from_grid(tb, axis), tl):.1f} deg")
    return ("\n## Measured facts (from the data, trustworthy)\n"
            "Median deviation of each street from the neighbourhood grid axis: " + "; ".join(rows) +
            f". A street is 'diagonal_to_grid' only if its deviation is above {tolerance_deg:g} deg, "
            "otherwise 'parallel_to_grid'.\n")
