"""Public Python API: the same pipeline as the CLI, usable from code, notebooks, or the MCP server."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .claims import ClaimResult
from .compiler import compile_story
from .evaluate import evaluate
from .geo import DataStore
from .images import Picture, fetch_pictures, spec_image_urls
from .publish import publish_draft, publish_web_map
from .safe_expr import safe_filter
from .spec import MapBlock, MapRef, StorySpec, SwipeBlock
from .thematic import class_breaks, numeric_values
from .verify import verify_claims
from .webmap import build_webmap, layer_role, layer_title, mercator_to_lonlat

ALLOWED_DATA_SUFFIXES = {".geojson", ".json", ".gpkg", ".shp"}


def resolve_data_path(path: str | Path, root: str | Path | None = None) -> Path:
    """Resolve a data file path. Only GIS file types are accepted; if ``root`` (or the STORYMAP_DATA_ROOT
    environment variable) is set, the file must live inside it."""
    p = Path(path).expanduser().resolve()
    if p.suffix.lower() not in ALLOWED_DATA_SUFFIXES:
        raise ValueError(f"unsupported data file type '{p.suffix}' (allowed: {sorted(ALLOWED_DATA_SUFFIXES)})")
    base = root or os.environ.get("STORYMAP_DATA_ROOT")
    if base and not p.is_relative_to(Path(base).resolve()):
        raise ValueError("data path is outside the allowed data root")
    if not p.is_file():
        raise FileNotFoundError(f"data file not found: {p.name}")
    return p


def load_store(data: Mapping[str, str | Path], root: str | Path | None = None) -> DataStore:
    return DataStore.from_files({name: str(resolve_data_path(p, root)) for name, p in data.items()})


@dataclass
class BuildResult:
    report: dict[str, Any]
    results: list[ClaimResult]
    story: dict[str, Any] | None  # None when the quality gate failed

    @property
    def passed(self) -> bool:
        return bool(self.report["passed"])


def build(spec: StorySpec | dict[str, Any], store: DataStore, template: str | None = None) -> BuildResult:
    """Validate, verify claims, compile and evaluate. The story is only returned if the gate passes."""
    if isinstance(spec, dict):
        spec = StorySpec.model_validate({**spec, **({"template": template} if template else {})})
    elif template:
        spec = spec.model_copy(update={"template": template})
    results = verify_claims(spec, store)
    story = compile_story(spec)
    report = evaluate(spec, story, results)
    problems = map_problems(spec, store)
    report["map_problems"] = problems
    report["passed"] = bool(report["passed"]) and not problems
    return BuildResult(report, results, story if report["passed"] else None)


def _filtered(gdf: Any, expr: str | None) -> Any:
    return safe_filter(gdf, expr) if expr else gdf


def map_problems(spec: StorySpec, store: DataStore) -> list[str]:
    """Problems that would only show up when the maps are built: unknown layers, bad filters, filters that select nothing."""
    out: list[str] = []
    for key, m in generated_maps(spec).items():
        for name in m.layers:
            if name not in store.layers:
                out.append(f"map '{key}': layer '{name}' is not loaded")
                continue
            try:
                kept = _filtered(store.get(name), m.filters.get(name))
            except ValueError as e:
                out.append(f"map '{key}': filter for '{name}' is not valid ({e})")
                continue
            if len(kept) == 0:
                out.append(f"map '{key}': layer '{name}' has no features" + (" after its filter" if name in m.filters else ""))
                continue
            style = m.styles.get(name)
            if style is not None:
                try:
                    if style.field not in kept.columns:
                        raise ValueError(f"has no column '{style.field}'")
                    class_breaks(numeric_values(kept[style.field]), style)
                except ValueError as e:
                    out.append(f"map '{key}': the style for '{name}' does not work ({e})")
    return out


def generated_maps(spec: StorySpec) -> dict[str, MapRef]:
    """The maps of a spec that must be generated from data layers, keyed by ``MapRef.key`` (first occurrence wins)."""
    out: dict[str, MapRef] = {}
    for sec in spec.sections:
        refs = [sl.map for sl in sec.all_slides() if sl.map is not None]
        refs += [b.map for b in sec.blocks if isinstance(b, (MapBlock, SwipeBlock))]
        for ref in refs:
            if ref.item_id is None:
                out.setdefault(ref.key, ref)
    return out


@dataclass
class PublishResult:
    story_id: str
    map_ids: dict[str, str]
    pictures: int = 0                                  # pictures uploaded to the draft
    left_out: list[str] = field(default_factory=list)  # pictures not included, with the reason


def publish_spec(spec: StorySpec, store: DataStore, *, title: str, username: str, token: str, portal: str,
                 summary: str = "", download_images: bool = False) -> PublishResult:
    """Create private Web Maps for the generated maps, then a private story draft that uses them. Existing items are never touched.

    Pictures named in the spec are only downloaded (and uploaded to the draft) when ``download_images`` is true; otherwise they are left out."""
    maps = generated_maps(spec)
    for key, m in maps.items():
        missing = [n for n in m.layers if n not in store.layers]
        if missing:
            raise ValueError(f"map '{key}' needs layers that were not loaded: {missing}")
    built = {key: build_webmap({n: _filtered(store.get(n), m.filters.get(n)) for n in m.layers}, styles=m.styles,
                               roles={n: layer_role(n) for n in m.layers},
                               titles={n: layer_title(n) for n in m.layers})
             for key, m in maps.items()}
    map_ids: dict[str, str] = {}
    for key, (webmap, box) in built.items():
        map_ids[key] = publish_web_map(webmap, f"{title}: {', '.join(maps[key].layers)}", username, token,
                                       mercator_to_lonlat(box), summary, sharing=portal)
    urls = spec_image_urls(spec)
    pictures: dict[str, Picture] = {}
    left_out: list[str] = []
    if urls and download_images:
        pictures, left_out = fetch_pictures(urls)
    elif urls:
        left_out = [f"{u[:80]}: left out (publish with --download-images to include pictures)" for u in urls]
    story = compile_story(spec, map_ids, images=pictures)
    story_id = publish_draft(story, title, username, token, summary=summary, sharing=portal, pictures=list(pictures.values()))
    return PublishResult(story_id, map_ids, len(pictures), left_out)
