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
from .spec import StorySpec
from .verify import verify_claims

ALLOWED_DATA_SUFFIXES = {".geojson", ".json", ".gpkg", ".shp"}


def resolve_data_path(path: str | Path, root: str | Path | None = None) -> Path:
    """Resolve a data file path. Only GIS file types are accepted; if ``root`` (or the STORYMAP_DATA_ROOT
    environment variable) is set, the file must live inside it."""
    p = Path(path).expanduser().resolve()
    if p.suffix.lower() not in ALLOWED_DATA_SUFFIXES:
        raise ValueError(f"unsupported data file type '{p.suffix}' (allowed: {sorted(ALLOWED_DATA_SUFFIXES)})")
    base = root or os.environ.get("STORYMAP_DATA_ROOT")
    if base and not p.is_relative_to(Path(base).resolve()):
        raise ValueError(f"data path {p} is outside the allowed root {base}")
    if not p.is_file():
        raise FileNotFoundError(str(p))
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
