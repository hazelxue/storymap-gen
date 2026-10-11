"""The manifest of a fetched data folder: where every layer came from, under what licence, and when.

It is written next to the data by ``storymap-gen fetch`` and read back by ``describe``, ``init`` and ``run`` so that the
story's reference list is generated from the real provenance instead of being typed from memory.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .geocode import Region

NAME = re.compile(r"^[A-Za-z][\w-]{0,40}$")


@dataclass
class LayerRecord:
    name: str
    file: str
    features: int
    geometry: str
    source: str            # short source key, e.g. "osm"
    attribution: str       # text for the story's reference list
    licence: str
    origin: str = ""       # query or URL
    fields: list[str] = field(default_factory=list)


@dataclass
class Manifest:
    region: dict[str, Any]
    retrieved: str
    layers: list[LayerRecord]

    def data_paths(self, base: Path) -> dict[str, str]:
        return {rec.name: str(base / rec.file) for rec in self.layers}

    def sources(self) -> list[dict[str, str]]:
        """Reference-list entries for the spec, one per distinct attribution."""
        out: dict[str, dict[str, str]] = {}
        for rec in self.layers:
            key = re.sub(r"[^\w.-]", "-", rec.source)[:64] or "source"
            text = rec.attribution if rec.source == "osm" else f"{rec.attribution}"
            out.setdefault(key, {"key": key, "text": text[:1000]})
        return list(out.values())


def new_manifest(region: Region) -> Manifest:
    return Manifest(region=region.to_dict(), retrieved=datetime.now(timezone.utc).strftime("%Y-%m-%d"), layers=[])


def write_manifest(manifest: Manifest, folder: Path) -> Path:
    path = folder / "manifest.json"
    path.write_text(json.dumps(asdict(manifest), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_manifest(path: Path) -> Manifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        layers = [LayerRecord(**rec) for rec in raw["layers"]]
        for rec in layers:
            if not NAME.match(rec.name) or "/" in rec.file or "\\" in rec.file or rec.file.startswith("."):
                raise ValueError("unsafe layer name or file in the manifest")
        return Manifest(region=raw["region"], retrieved=str(raw["retrieved"]), layers=layers)
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as e:
        raise ValueError(f"could not read the manifest ({type(e).__name__})") from None
