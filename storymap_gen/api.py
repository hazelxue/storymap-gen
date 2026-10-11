"""Public Python API: the same pipeline as the CLI, usable from code, notebooks, or the MCP server."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .claims import ClaimResult
from .compiler import compile_story
from .evaluate import evaluate
from .geo import DataStore
from .publish import publish_draft, publish_web_map
from .sources.overpass import LAYERS
from .spec import MapRef, StorySpec
from .verify import verify_claims
from .webmap import build_webmap, mercator_to_lonlat

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
    return BuildResult(report, results, story if report["passed"] else None)


def generated_maps(spec: StorySpec) -> dict[str, MapRef]:
    """The maps of a spec that must be generated from data layers, keyed by ``MapRef.key`` (first occurrence wins)."""
    out: dict[str, MapRef] = {}
    for sec in spec.sections:
        for sl in sec.slides:
            if sl.map is not None and sl.map.item_id is None:
                out.setdefault(sl.map.key, sl.map)
    return out


@dataclass
class PublishResult:
    story_id: str
    map_ids: dict[str, str]


def publish_spec(spec: StorySpec, store: DataStore, *, title: str, username: str, token: str, portal: str,
                 summary: str = "") -> PublishResult:
    """Create private Web Maps for the generated maps, then a private story draft that uses them. Existing items are never touched."""
    maps = generated_maps(spec)
    for key, m in maps.items():
        missing = [n for n in m.layers if n not in store.layers]
        if missing:
            raise ValueError(f"map '{key}' needs layers that were not loaded: {missing}")
    built = {key: build_webmap({n: store.get(n) for n in m.layers},
                               roles={n: LAYERS[n].role for n in m.layers if n in LAYERS},
                               titles={n: LAYERS[n].title if n in LAYERS else n for n in m.layers})
             for key, m in maps.items()}
    map_ids: dict[str, str] = {}
    for key, (webmap, box) in built.items():
        map_ids[key] = publish_web_map(webmap, f"{title}: {', '.join(maps[key].layers)}", username, token,
                                       mercator_to_lonlat(box), summary, sharing=portal)
    story = compile_story(spec, map_ids)
    story_id = publish_draft(story, title, username, token, summary=summary, sharing=portal)
    return PublishResult(story_id, map_ids)
