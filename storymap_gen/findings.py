"""Candidate findings: a standard battery of measurements over whatever layers were fetched, so a story starts from what the data
actually shows instead of from guesses.

A writer (a model or a person) who sees only counts writes a table of counts. The sentences that make a district story are about
*distribution*: how much of the place a service reaches, where something is missing, which half of the area holds most of it,
how concentrated it is, how two layers relate. This module measures those for every layer, ranks them by how notable they are,
and turns each into a claim that has already been checked against the data, ready to paste into a spec.

It works on any layers (themes with a known meaning get sensible distances; other layers get defaults) and needs the study-area
polygon (the ``boundary`` layer written by ``fetch``) for everything that is about the whole place.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pydantic import TypeAdapter
from shapely.geometry import box
from shapely.strtree import STRtree

from .claims import Claim, ClaimResult
from .geo import DataStore
from .webmap import layer_title

# distances (metres) at which each kind of layer is worth measuring reach: a short walk for a bus stop, a longer one for a station
DISTANCES: dict[str, list[float]] = {
    "transit": [500, 1000], "bus_stops": [200, 300, 500], "schools": [400, 800], "health": [400, 800],
    "bike": [100, 300], "parks": [0, 200, 400], "water": [0, 200],
}
DEFAULT_DISTANCES = [300, 500]
# layers that are everywhere by nature: "100% of schools are near a street" says nothing
BASE_LAYERS = {"streets", "landuse", "buildings", "boundary"}
SKIP_COLUMNS = {"name", "osm_type", "osm_id", "id", "geometry", "ref", "operator", "network", "website", "wikidata", "note"}
MAX_SAMPLE_POINTS = 15_000
MIN_SAMPLE_STEP_M = 40.0
PER_LAYER_CAP = 7
PER_LAYER_TOPIC_CAP = {"reach": 4, "scale": 3, "share": 3, "relation": 3}
_CLAIMS: TypeAdapter[Any] = TypeAdapter(Claim)


@dataclass
class Finding:
    id: str
    topic: str           # scale | reach | gap | share | skew | concentration | relation
    layer: str
    text: str            # the sentence, with its numbers
    score: float         # how notable (0 to 1)
    claims: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""     # how it was measured
    fixed_pct: int = 0   # percentages at the start of the sentence that are not a claim's (the land share in a skew)


def _sid(*parts: str) -> str:
    return re.sub(r"[^\w.-]+", "-", "-".join(parts)).strip("-")[:60]


def _kind(gdf: gpd.GeoDataFrame) -> str:
    t = gdf.geometry.geom_type
    counts = (("point", int(t.isin(["Point", "MultiPoint"]).sum())), ("line", int(t.isin(["LineString", "MultiLineString"]).sum())),
              ("polygon", int(t.isin(["Polygon", "MultiPolygon"]).sum())))
    return max(counts, key=lambda kv: kv[1])[0]


def _sample_points(study: Any) -> tuple[np.ndarray, np.ndarray, float]:
    """A regular grid of points inside the study area (UTM metres): they stand for 'a random place in the district'."""
    minx, miny, maxx, maxy = study.bounds
    step = max(MIN_SAMPLE_STEP_M, math.sqrt(study.area / MAX_SAMPLE_POINTS))
    xs, ys = np.meshgrid(np.arange(minx + step / 2, maxx, step), np.arange(miny + step / 2, maxy, step))
    xs, ys = xs.ravel(), ys.ravel()
    inside = shapely.contains_xy(study, xs, ys)
    return xs[inside], ys[inside], step


def _nearest_distances(points: Any, targets: Any) -> np.ndarray:
    """Distance from each point to the nearest of ``targets`` (a GeoSeries in the same metric CRS)."""
    geoms = [g for g in targets.values if g is not None and not g.is_empty]
    if not geoms:
        return np.full(len(points), np.inf)
    tree = STRtree(geoms)
    pairs, dist = tree.query_nearest(points, return_distance=True, all_matches=False)
    out = np.full(len(points), np.inf)
    out[pairs[0]] = dist            # one nearest target per input point
    return out


def _categorical_columns(gdf: gpd.GeoDataFrame, limit: int = 3) -> list[str]:
    cols = []
    for c in gdf.columns:
        if c in SKIP_COLUMNS or c == gdf.geometry.name:
            continue
        s = gdf[c]
        if not (pd.api.types.is_object_dtype(s) or isinstance(s.dtype, pd.StringDtype)):
            continue
        filled = s.notna().mean()
        n = s.nunique()
        if filled >= 0.5 and 2 <= n <= 14 and s.dropna().astype(str).str.len().max() <= 30:
            cols.append((filled * min(n, 6), c))
    return [c for _, c in sorted(cols, reverse=True)[:limit]]


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


class _Battery:
    def __init__(self, store: DataStore):
        self.store = store
        self.out: list[Finding] = []
        self.boundary = store.layers.get("boundary")
        self.has_boundary = self.boundary is not None and len(self.boundary) > 0
        ref = self.boundary if self.boundary is not None and self.has_boundary else next(iter(store.layers.values()))
        self.crs = ref.estimate_utm_crs()
        self._proj_cache: dict[str, gpd.GeoDataFrame] = {}
        self._dist_cache: dict[str, np.ndarray] = {}
        if self.has_boundary:
            assert self.boundary is not None
            self.study = self.boundary.to_crs(self.crs).geometry.make_valid().union_all()
            self.area_km2 = float(self.study.area) / 1e6
            self.sx, self.sy, self.step = _sample_points(self.study)
            self.sample = shapely.points(self.sx, self.sy)
            self.centre = self.study.centroid
        else:
            self.study = None
            self.area_km2 = 0.0

    # ---- helpers ---------------------------------------------------------------------------------------
    def _proj(self, name: str) -> gpd.GeoDataFrame:
        if name not in self._proj_cache:
            self._proj_cache[name] = self.store.get(name).to_crs(self.crs)
        return self._proj_cache[name]

    def _area_distances(self, name: str) -> np.ndarray:
        """Distance from every sample point of the study area to the nearest feature of a layer (cached)."""
        if name not in self._dist_cache:
            self._dist_cache[name] = _nearest_distances(self.sample, self._proj(name).geometry)
        return self._dist_cache[name]

    def add(self, f: Finding) -> None:
        self.out.append(f)

    # ---- per-layer measurements -----------------------------------------------------------------------
    def layer(self, name: str) -> None:
        gdf = self.store.get(name)
        if gdf.empty:
            return
        kind = _kind(gdf)
        title = layer_title(name).lower()
        proj = self._proj(name)
        if kind == "point":
            self.add(Finding(_sid(name, "count"), "scale", name, f"There are {len(gdf):,} {title}.", 0.15,
                             [{"kind": "layer_stat", "id": _sid(name, "count"), "text": f"{len(gdf)} {title}", "layer": name, "agg": "count",
                               "expected": float(len(gdf))}]))
        elif kind == "line":
            km = float(proj.geometry.length.sum()) / 1000
            self.add(Finding(_sid(name, "length"), "scale", name, f"{title.capitalize()} add up to {km:,.1f} km.", 0.15,
                             [{"kind": "layer_length", "id": _sid(name, "length"), "text": f"{km:.1f} km of {title}", "layer": name,
                               "unit": "km", "expected": round(km, 1), "tolerance": 0.05}]))
        else:
            ha = float(proj.geometry.make_valid().union_all().area) / 1e4
            self.add(Finding(_sid(name, "area"), "scale", name, f"{title.capitalize()} cover {ha:,.1f} ha.", 0.15,
                             [{"kind": "layer_area", "id": _sid(name, "area"), "text": f"{ha:.1f} ha of {title}", "layer": name,
                               "unit": "ha", "expected": round(ha, 1), "tolerance": 0.05}]))
        if self.has_boundary:
            self._density(name, kind, title, proj)
            if name not in BASE_LAYERS:
                self._reach(name, kind, title)
                if kind == "point":
                    self._reach_by_type(name, title, proj)
            self._skew(name, kind, title, proj)
        self._shares(name, kind, title, proj)
        if kind == "polygon":
            self._concentration(name, title, proj)

    def _density(self, name: str, kind: str, title: str, proj: gpd.GeoDataFrame) -> None:
        if kind == "point":
            d = len(proj) / self.area_km2
            self.add(Finding(_sid(name, "density"), "scale", name, f"That is {d:.1f} {title} per km².", 0.2,
                             [{"kind": "layer_density", "id": _sid(name, "density"), "text": f"{d:.1f} {title} per km2", "layer": name,
                               "expected": round(d, 2), "tolerance": 0.05}]))
        elif kind == "line":
            d = float(proj.geometry.length.sum()) / 1000 / self.area_km2
            self.add(Finding(_sid(name, "density"), "scale", name, f"That is {d:.1f} km of {title} per km².", 0.2,
                             [{"kind": "layer_density", "id": _sid(name, "density"), "text": f"{d:.1f} km of {title} per km2", "layer": name,
                               "measure": "length_km", "expected": round(d, 2), "tolerance": 0.05}]))

    def _reach(self, name: str, kind: str, title: str) -> None:
        dist = self._area_distances(name)
        for d in DISTANCES.get(name, DEFAULT_DISTANCES):
            if d == 0 and kind != "polygon":
                continue
            share = float((dist <= max(d, 1e-6)).mean())
            where = f"within {d:g} m of {title}" if d else f"covered by {title}"
            service = d > 0
            gap = service and share < 0.8
            score = (1 - share) * 0.9 if gap else (0.5 if 0.5 <= share <= 0.97 else 0.3 if service else 0.4)
            text = f"Only {_pct(share)} of the study area is {where}." if gap else f"{_pct(share)} of the study area is {where}."
            self.add(Finding(_sid(name, "reach", f"{d:g}"), "gap" if gap else "reach", name, text, score,
                             [{"kind": "area_coverage", "id": _sid(name, "reach", f"{d:g}"), "text": f"{_pct(share)} of the area is {where}",
                               "layer": name, "max_distance_m": d, "expected_share": round(share, 2), "tolerance": 0.005}],
                             detail="estimated on a regular grid; the claim is checked exactly"))
        finite = dist[np.isfinite(dist)]
        if len(finite) and kind != "polygon":
            p90 = float(np.quantile(finite, 0.9))
            if p90 > 0:
                d90 = max(50.0, math.ceil(p90 / 50) * 50)
                if d90 in DISTANCES.get(name, DEFAULT_DISTANCES):
                    return                      # already reported at that distance
                share = float((dist <= d90).mean())
                self.add(Finding(_sid(name, "reach90", f"{d90:g}"), "reach", name,
                                 f"{_pct(share)} of the study area is within {d90:g} m of {title}: that is how far the farthest tenth has to go.", 0.35,
                                 [{"kind": "area_coverage", "id": _sid(name, "reach90", f"{d90:g}"),
                                   "text": f"{_pct(share)} of the area is within {d90:g} m of {title}", "layer": name, "max_distance_m": d90,
                                   "expected_share": round(share, 2), "tolerance": 0.005}]))

    def _reach_by_type(self, name: str, title: str, proj: gpd.GeoDataFrame) -> None:
        """A point layer often mixes kinds (stations and their entrances; schools and kindergartens): reach can differ by kind."""
        gdf = self.store.get(name)
        cols = _categorical_columns(gdf, limit=1)
        if not cols or len(gdf) < 20:
            return
        col = cols[0]
        counts = gdf[col].dropna().astype(str).value_counts()
        for value in list(counts.index[:3]):
            share = counts[value] / len(gdf)
            if "'" in value or "\\" in value or counts[value] < 5 or not 0.1 <= share <= 0.9:
                continue
            sub = proj[gdf[col].astype(str) == value]
            dist = _nearest_distances(self.sample, sub.geometry)
            for d in DISTANCES.get(name, DEFAULT_DISTANCES)[:2]:
                cov = float((dist <= d).mean())
                cid = _sid(name, col, value, "reach", f"{d:g}")
                what = f"{col} = {value}"
                gap = cov < 0.8
                self.add(Finding(cid, "gap" if gap else "reach", name,
                                 f"{'Only ' if gap else ''}{_pct(cov)} of the study area is within {d:g} m of {title} with {what}.",
                                 (1 - cov) * 0.85 if gap else 0.4,
                                 [{"kind": "area_coverage", "id": cid, "text": f"{_pct(cov)} of the area is within {d:g} m of {title} with {what}",
                                   "layer": name, "where": f"{col} == '{value}'", "max_distance_m": d, "expected_share": round(cov, 2),
                                   "tolerance": 0.005}], detail="estimated on a regular grid; the claim is checked exactly"))

    def _skew(self, name: str, kind: str, title: str, proj: gpd.GeoDataFrame) -> None:
        if len(proj) < 8 or self.study is None:
            return
        pts = proj.geometry.representative_point()
        weight = proj.geometry.length if kind == "line" else proj.geometry.area if kind == "polygon" else pd.Series(1.0, index=proj.index)
        total = float(weight.sum())
        if total <= 0:
            return
        minx, miny, maxx, maxy = self.study.bounds
        cx, cy = self.centre.x, self.centre.y
        halves = {"north": (pts.y > cy, box(minx, cy, maxx, maxy)), "south": (pts.y <= cy, box(minx, miny, maxx, cy)),
                  "east": (pts.x > cx, box(cx, miny, maxx, maxy)), "west": (pts.x <= cx, box(minx, miny, cx, maxy))}
        measure = {"point": "count", "line": "length", "polygon": "area"}[kind]
        best: tuple[float, str, float, float] | None = None
        for half, (mask, region) in halves.items():
            share = float(weight[mask].sum()) / total
            area_share = float(self.study.intersection(region).area / self.study.area)
            if best is None or abs(share - area_share) > abs(best[0]):
                best = (share - area_share, half, share, area_share)
        assert best is not None
        gap, half, share, area_share = best
        if abs(gap) < 0.12:
            return
        self.add(Finding(_sid(name, "skew", half), "skew", name,
                         f"The {half}ern half of the study area, {_pct(area_share)} of the land, holds {_pct(share)} of the {title}"
                         f"{' (by length)' if measure == 'length' else ' (by area)' if measure == 'area' else ''}.",
                         min(1.0, abs(gap) * 2.2),
                         claims=[{"kind": "layer_share", "id": _sid("land", half), "text": f"{_pct(area_share)} of the study area lies in the {half} half",
                                  "layer": "boundary", "half": half, "measure": "area", "expected_share": round(area_share, 2), "tolerance": 0.005},
                                 {"kind": "layer_share", "id": _sid(name, "skew", half), "text": f"{_pct(share)} of {title} lie in the {half} half",
                                  "layer": name, "half": half, "measure": measure, "expected_share": round(share, 2), "tolerance": 0.005}],
                         detail=f"the land is split at the study area's centre; the {half}ern half is {_pct(area_share)} of it"))

    def _shares(self, name: str, kind: str, title: str, proj: gpd.GeoDataFrame) -> None:
        gdf = self.store.get(name)
        measure = {"point": "count", "line": "length", "polygon": "count"}[kind]
        weight = proj.geometry.length if measure == "length" else pd.Series(1.0, index=proj.index)
        total = float(weight.sum())
        if total <= 0:
            return
        for col in _categorical_columns(gdf):
            sums = weight.groupby(gdf[col].astype(str).where(gdf[col].notna())).sum().sort_values(ascending=False)
            for value in list(sums.index[:2]):
                if "'" in value or "\\" in value:
                    continue
                share = float(sums[value]) / total
                cid = _sid(name, col, value)
                what = {"count": f"of the {title}", "length": f"of the length of {title}"}[measure]
                score = 0.55 if 0.25 <= share <= 0.75 else 0.45 if share > 0.75 else 0.25
                self.add(Finding(cid, "share", name, f"{_pct(share)} {what} have {col} = {value}.", score,
                                 [{"kind": "layer_share", "id": cid, "text": f"{_pct(share)} of {title} have {col} {value}", "layer": name,
                                   "where": f"{col} == '{value}'", "measure": measure, "expected_share": round(share, 2), "tolerance": 0.005}]))

    def _concentration(self, name: str, title: str, proj: gpd.GeoDataFrame) -> None:
        gdf = self.store.get(name)
        if "name" not in gdf.columns:
            return
        has_name = gdf["name"].notna()
        if int(has_name.sum()) < 5:
            return
        areas = proj[has_name].geometry.make_valid().area.groupby(gdf.loc[has_name, "name"].astype(str)).sum().sort_values(ascending=False)
        total = float(proj.geometry.make_valid().union_all().area)
        top = [n for n in areas.index[:3] if "'" not in n and "\\" not in n]
        if len(top) < 3 or total <= 0:
            return
        share = float(areas[top].sum()) / total
        if share < 0.4:
            return
        names = ", ".join(top)
        self.add(Finding(_sid(name, "top3"), "concentration", name,
                         f"Three of the {title} ({names}) hold {_pct(share)} of all their area.", min(1.0, 0.4 + share * 0.6),
                         [{"kind": "layer_share", "id": _sid(name, "top3"), "text": f"{_pct(share)} of {title} area is in {names}",
                           "layer": name, "where": "name in [" + ", ".join(f"'{n}'" for n in top) + "]", "measure": "area",
                           "expected_share": round(share, 2), "tolerance": 0.005}]))

    # ---- relations between layers ---------------------------------------------------------------------
    def relations(self, names: list[str]) -> None:
        """Does a service sit near an access layer more (or less) often than a random place does? Only differences count: a layer that
        is everywhere (streets, land use) explains nothing."""
        if not self.has_boundary:
            return
        points = [n for n in names if _kind(self.store.get(n)) == "point"][:6]
        for a in points:
            pa = self._proj(a).geometry.representative_point()
            if len(pa) < 5:
                continue
            arr = shapely.points(pa.x.values, pa.y.values)
            for b in names:
                if b == a or b in BASE_LAYERS:
                    continue
                d = DISTANCES.get(b, DEFAULT_DISTANCES)[0] or 100
                share = float((_nearest_distances(arr, self._proj(b).geometry) <= d).mean())
                area_share = float((self._area_distances(b) <= d).mean())
                lift = share - area_share
                if abs(lift) < 0.10:
                    continue                    # about what a random place would show: nothing to say
                ta, tb = layer_title(a).lower(), layer_title(b).lower()
                word = "more" if lift > 0 else "less"
                cid = _sid(a, "near", b, f"{d:g}")
                cov = _sid(b, "reach", f"{d:g}")
                self.add(Finding(cid, "relation", a,
                                 f"{_pct(share)} of {ta} are within {d:g} m of {tb}, {word} often than a typical place ({_pct(area_share)} of the land is).",
                                 min(1.0, abs(lift) * 2.0 + (0.2 if share < 0.5 else 0)),
                                 [{"kind": "within_distance", "id": cid, "text": f"{_pct(share)} of {ta} are within {d:g} m of {tb}", "layer": a,
                                   "target_layer": b, "max_distance_m": d, "expected_share": round(share, 2), "tolerance": 0.005},
                                  {"kind": "area_coverage", "id": cov, "text": f"{_pct(area_share)} of the area is within {d:g} m of {tb}",
                                   "layer": b, "max_distance_m": d, "expected_share": round(area_share, 2), "tolerance": 0.005}]))


# ---- verification and wording ---------------------------------------------------------------------------------
def _check(store: DataStore, claim: dict[str, Any]) -> tuple[ClaimResult | None, Claim | None]:
    try:
        c = _CLAIMS.validate_python(claim)
        return c.check(store), c
    except Exception:  # noqa: BLE001 - a candidate that cannot be measured is simply dropped
        return None, None


def _settle(store: DataStore, f: Finding) -> bool:
    """Set each claim's expected value from the exact measurement and make sure it passes; make the sentence say the checked number.
    False if a claim cannot be made to pass."""
    for claim in f.claims:
        res, parsed = _check(store, claim)
        if res is None or parsed is None or res.status == "ERROR" or res.measured is None:
            return False
        kind, m = claim["kind"], res.measured
        if kind == "layer_stat":
            claim["expected"] = float(round(m))
        elif kind in ("layer_length", "layer_area"):
            claim["expected"], claim["tolerance"] = round(m, 1), 0.05
        elif kind == "layer_density":
            claim["expected"], claim["tolerance"] = round(m, 2), 0.05
        else:  # shares and reach: two decimals, so the sentence says a whole percent
            claim["expected_share"], claim["tolerance"] = round(m, 2), 0.005
        res2, _ = _check(store, claim)
        if res2 is None or res2.status != "PASS":
            return False
    first = f.claims[0]
    if "expected_share" in first:
        # sentence and claim texts carry the exact percentages, in order: the first claim's, then (relations) the second's
        shares = [c["expected_share"] for c in f.claims if "expected_share" in c]
        for c in f.claims:
            if "expected_share" in c:
                c["text"] = re.sub(r"\d+%", _pct(c["expected_share"]), c["text"], count=1)
        seen = 0
        pieces, i = [], 0
        for p in re.split(r"(\d+%)", f.text):
            if re.fullmatch(r"\d+%", p):
                seen += 1
                if seen > f.fixed_pct and i < len(shares):
                    p, i = _pct(shares[i]), i + 1
            pieces.append(p)
        f.text = "".join(pieces)
    elif first["kind"] == "layer_stat":
        f.text = re.sub(r"\d[\d,]*", f"{int(first['expected']):,}", f.text, count=1)
    return True


def compute_findings(store: DataStore, limit: int = 40) -> list[Finding]:
    """Measure, rank and verify. Every returned finding's claims pass against ``store``."""
    battery = _Battery(store)
    names = [n for n, g in store.layers.items() if n != "boundary" and len(g)]
    for n in names:
        battery.layer(n)
    battery.relations(names)
    kept: list[Finding] = []
    per_layer: dict[str, int] = {}
    per_topic: dict[tuple[str, str], int] = {}
    for f in sorted(battery.out, key=lambda f: (-f.score, f.id)):
        topic_key = (f.layer, "reach" if f.topic in ("reach", "gap") else f.topic)
        if per_layer.get(f.layer, 0) >= PER_LAYER_CAP or per_topic.get(topic_key, 0) >= PER_LAYER_TOPIC_CAP.get(topic_key[1], 3):
            continue
        if _settle(store, f):
            kept.append(f)
            per_layer[f.layer] = per_layer.get(f.layer, 0) + 1
            per_topic[topic_key] = per_topic.get(topic_key, 0) + 1
        if len(kept) >= limit:
            break
    return kept


def study_area_line(store: DataStore) -> str:
    b = store.layers.get("boundary")
    if b is None or b.empty:
        return "No study-area polygon: reach, density and half-of-the-area findings need a layer named `boundary`."
    km2 = float(b.to_crs(b.estimate_utm_crs()).geometry.make_valid().union_all().area) / 1e6
    return f"Study area: {km2:.2f} km2."


GROUPS = [("gap", "Gaps: where the place is not served"), ("skew", "Skews: which part of the place holds it"),
          ("concentration", "Concentration"), ("relation", "How layers relate"), ("reach", "Reach"), ("share", "What it is made of"),
          ("scale", "Scale")]


def render_markdown(findings: list[Finding], store: DataStore) -> str:
    lines = ["# Candidate findings", "", study_area_line(store), "",
             "Every finding below was measured and its claim already passes against this data. Pick the ones that serve the story's thesis, "
             "copy the claims (`--claims-out`) into the spec, and attach them to the sentence that states each number. Prefer gaps, skews and "
             "relations to bare counts: they are what a reader can weigh.", ""]
    for key, title in GROUPS:
        group = [f for f in findings if f.topic == key]
        if not group:
            continue
        lines.append(f"## {title}")
        for f in group:
            ids = ", ".join(f"`{c['id']}`" for c in f.claims)
            lines.append(f"- **{f.text}**  ({ids}; notability {f.score:.2f})" + (f" {f.detail}." if f.detail else ""))
        lines.append("")
    return "\n".join(lines)


def to_json(findings: list[Finding]) -> list[dict[str, Any]]:
    return [asdict(f) for f in findings]


def all_claims(findings: list[Finding]) -> list[dict[str, Any]]:
    """The claims of every finding, once each, ready to paste into a spec."""
    seen: dict[str, dict[str, Any]] = {}
    for f in findings:
        for c in f.claims:
            seen.setdefault(c["id"], c)
    return list(seen.values())
