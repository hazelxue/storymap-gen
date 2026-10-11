"""MCP server: lets an agent (Claude Code, Claude Desktop, any MCP client) use the verify-first pipeline for any place.

Run:  storymap-gen-mcp        (stdio)

Deliberately NOT exposed: publishing, anything that needs a credential, and downloads from arbitrary URLs. Creating items in an
ArcGIS account needs a user-authorised token, which should never pass through a model; use `storymap-gen publish-spec`
yourself after reviewing. The only network access is to two fixed public services (Nominatim for place names, Overpass for
OpenStreetMap data), through a client that refuses private addresses, redirects and oversized responses.

Every tool treats its arguments as untrusted (they come from a model that may have read hostile text): inputs are validated and
size-limited, files are only read from and written to the data root, errors are returned as structured results, no tool raises.

Environment:
  STORYMAP_DATA_ROOT  all data files and folders live inside this directory (default: the server's working directory)
  STORYMAP_OUT_DIR    where compiled stories are written (default ./out)
"""
from __future__ import annotations

import functools
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer
except ModuleNotFoundError as e:  # the MCP server is an optional extra
    raise SystemExit("The MCP server needs the 'mcp' package. Install it with:  pip install \"storymap-gen[mcp]\"") from e
from pydantic import ValidationError

from . import net
from .api import build, load_store, resolve_data_path
from .geo import DataStore
from .preview import render_preview
from .sources.manifest import read_manifest
from .spec import StorySpec
from .verify import orientation_facts

server = MCPServer(
    "storymap-gen",
    instructions=(
        "Build ArcGIS StoryMaps with verified facts, for any place. Workflow: resolve_region, fetch_osm_layers (or use the user's own "
        "files), describe_data, find_findings and explore_data (show the user the data), ask for an outline, init_spec, get_spec_schema, measure_street_orientation for streets you will mention, write the spec (every "
        "number in prose must be backed by a claim; maps may use data layers by name), verify_spec, compile_spec. "
        "Never state a fact about the data that a tool did not measure."),
)

SAFE_NAME = re.compile(r"^[\w.-]{1,100}\.json$")
FOLDER = re.compile(r"^[\w-]{1,40}$")
MAX_SPEC_BYTES = 2_000_000
MAX_LAYERS_PER_CALL = 8


def _root() -> str:
    return os.environ.get("STORYMAP_DATA_ROOT") or os.getcwd()


def _guard(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Make a tool total: bad input and unexpected failures become structured results, never exceptions."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except ValidationError as e:
            return {"ok": False, "error": "spec_invalid",
                    "details": [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()][:10]}
        except net.NetworkError as e:
            return {"ok": False, "error": "network", "message": str(e)[:300]}
        except (ValueError, KeyError, FileNotFoundError) as e:
            return {"ok": False, "error": "bad_input", "message": str(e)[:300]}
        except Exception as e:  # noqa: BLE001 - last line of defence for a long-running server
            print(f"storymap-gen-mcp: unexpected {type(e).__name__} in {fn.__name__}", file=sys.stderr)
            return {"ok": False, "error": "internal_error", "message": type(e).__name__}

    return wrapper


def _in_root(path: str) -> str:
    """A relative path is taken relative to the data root (so a client can say 'folder/manifest.json')."""
    return path if Path(path).is_absolute() else str(Path(_root()) / path)


def _store(data: dict[str, str] | None, manifest: str | None) -> DataStore:
    """Layers from a manifest (inside the data root) and/or explicit file paths."""
    paths: dict[str, str] = {}
    if manifest:
        mpath = resolve_data_path(_in_root(manifest), _root())
        paths.update(read_manifest(mpath).data_paths(mpath.parent))
    paths.update(data or {})
    return load_store(paths, root=_root())


def _folder(name: str) -> Path:
    if not FOLDER.match(name):
        raise ValueError("folder must be a plain name (letters, digits, - and _)")
    path = Path(_root()) / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _spec(spec: dict[str, Any], template: str | None = None) -> StorySpec:
    if len(json.dumps(spec, default=str)) > MAX_SPEC_BYTES:
        raise ValueError("the spec is larger than 2 MB")
    return StorySpec.model_validate({**spec, **({"template": template} if template else {})})


@server.tool()
@_guard
def get_spec_schema() -> dict[str, Any]:
    """JSON Schema of the story spec (sections, slides, maps from data layers, sources, and the claim types)."""
    return {"ok": True, "schema": StorySpec.model_json_schema()}


@server.tool()
@_guard
def resolve_region(folder: str, place: str | None = None, bbox: list[float] | None = None) -> dict[str, Any]:
    """Find the area for a story: give a place name (geocoded with OpenStreetMap Nominatim) or a bbox [west, south, east, north].
    Saves it as <folder>/region.json inside the data root and returns a summary. Areas above 150 km2 are refused."""
    from .sources.geocode import geocode, region_from_bbox
    if bbox is not None:
        if len(bbox) != 4:
            raise ValueError("bbox needs four numbers: west, south, east, north")
        region = region_from_bbox(bbox[0], bbox[1], bbox[2], bbox[3], name=place or "region")
    elif place:
        region = geocode(place)
    else:
        raise ValueError("give a place name or a bbox")
    path = _folder(folder) / "region.json"
    path.write_text(json.dumps(region.to_dict(), ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "name": region.name, "display_name": region.display_name, "bbox": list(region.bbox),
            "box_km2": round(region.area_km2, 2), "study_area_km2": round(region.study_area_km2, 2),
            "has_boundary": region.boundary is not None, "folder": folder}


@server.tool()
@_guard
def fetch_osm_layers(folder: str, layers: list[str], clip: str = "boundary") -> dict[str, Any]:
    """Download OpenStreetMap themes for the region saved by resolve_region (streets, bike, parks, transit, bus_stops, schools,
    health, buildings, landuse, water). Data is cut to the place boundary. Writes GeoJSON files and manifest.json into the folder."""
    from .sources import overpass
    from .sources.geocode import Region
    from .sources.manifest import LayerRecord, new_manifest, write_manifest
    if len(layers) > MAX_LAYERS_PER_CALL:
        raise ValueError(f"at most {MAX_LAYERS_PER_CALL} layers per call")
    base = _folder(folder)
    region = Region.from_dict(json.loads((base / "region.json").read_text(encoding="utf-8")))
    manifest = new_manifest(region)
    failed: list[dict[str, str]] = []
    boundary = region.boundary_frame()
    if boundary is not None:  # density and coverage claims measure against the study area
        boundary.to_file(base / "boundary.geojson", driver="GeoJSON")
        manifest.layers.append(LayerRecord("boundary", "boundary.geojson", 1, "Polygon", "osm", region.attribution, overpass.LICENCE,
                                           "place boundary from Nominatim", ["name"]))
    for name in layers:
        try:
            gdf = overpass.fetch_layer(name, region, clip=clip)
        except (net.NetworkError, ValueError) as e:
            failed.append({"layer": name, "error": str(e)[:300]})
            continue
        target = base / f"{name}.geojson"
        if gdf.empty:
            target.write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")
        else:
            gdf.to_file(target, driver="GeoJSON")
        kinds = ", ".join(sorted(set(map(str, gdf.geometry.geom_type)))) if len(gdf) else "none"
        manifest.layers.append(LayerRecord(name, f"{name}.geojson", int(len(gdf)), kinds, "osm", overpass.ATTRIBUTION, overpass.LICENCE,
                                           f"Overpass API, theme '{name}', clipped to {clip}",
                                           [str(c) for c in gdf.columns if c != gdf.geometry.name]))
    if manifest.layers:
        write_manifest(manifest, base)
    return {"ok": bool(manifest.layers), "layers": [{"name": r.name, "features": r.features} for r in manifest.layers],
            "failed": failed, "manifest": f"{folder}/manifest.json" if manifest.layers else None, "sources": manifest.sources()}


@server.tool()
@_guard
def describe_data(data: dict[str, str] | None = None, manifest: str | None = None) -> dict[str, Any]:
    """Profile the data layers: features, sizes, fields with real values, and which claim types fit. Read this before writing claims."""
    from .describe import profile_store, render_markdown
    return {"ok": True, "markdown": render_markdown(profile_store(_store(data, manifest)))}


@server.tool()
@_guard
def measure_street_orientation(streets: list[str], data: dict[str, str] | None = None, manifest: str | None = None,
                               layer: str = "streets_centerline", name_field: str = "full_stree") -> dict[str, Any]:
    """Measure how far each street deviates from the neighbourhood grid (degrees). Call this BEFORE writing
    claims about whether a street is diagonal or parallel to the grid. Layers come from the manifest or ``data``."""
    if len(streets) > 200:
        raise ValueError("at most 200 streets per call")
    return {"ok": True, "facts": orientation_facts(_store(data, manifest), streets, layer=layer, name_field=name_field)}


@server.tool()
@_guard
def verify_spec(spec: dict[str, Any], data: dict[str, str] | None = None, manifest: str | None = None) -> dict[str, Any]:
    """Check every claim against the data and every number in the prose against the claims. Does not compile."""
    result = build(_spec(spec), _store(data, manifest))
    return {"ok": True, "passed": result.passed,
            "claims": [{"id": r.id, "status": r.status, "detail": r.detail, "hint": r.hint} for r in result.results],
            "unbacked_numbers": result.report["unbacked_numbers"], "placeholders": result.report["placeholders"],
            "uncited_sources": result.report["citation"]["uncited_sources"], "map_problems": result.report["map_problems"],
            "depth": result.report["depth"]}


@server.tool()
@_guard
def compile_spec(spec: dict[str, Any], out_name: str, data: dict[str, str] | None = None, manifest: str | None = None,
                 template: str | None = None) -> dict[str, Any]:
    """Run the full quality gate and, only if it passes, write the StoryMaps JSON to STORYMAP_OUT_DIR/<out_name>.
    ``out_name`` must be a plain file name ending in .json. ``template``: docked-sidecar, floating-sidecar or longform."""
    if not SAFE_NAME.match(out_name):
        return {"ok": False, "error": "out_name must be a plain file name ending in .json"}
    result = build(_spec(spec, template), _store(data, manifest))
    if result.story is None:
        return {"ok": False, "error": "quality_gate_failed", "report": result.report}
    out_dir = Path(os.environ.get("STORYMAP_OUT_DIR", "out"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / out_name
    path.write_text(json.dumps(result.story, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "path": str(path.resolve()), "nodes": len(result.story["nodes"]), "report": result.report}


@server.tool()
@_guard
def init_spec(folder: str, topic: str, framework: str = "auto", outline: str | None = None, template: str = "longform",
              manifest: str | None = None) -> dict[str, Any]:
    """A skeleton spec for the region in <folder> (from resolve_region): ``framework`` is auto (the outline if given, else essay),
    outline, systems, essay or brief. ``outline`` is markdown: unindented lines become sections, indented lines subsections.
    Every text field to write contains TODO, and the gate refuses unfinished text. Returns the spec; nothing is written."""
    from .scaffold import scaffold_spec
    from .sources.geocode import Region
    if len(topic) > 300 or (outline is not None and len(outline) > 20_000):
        raise ValueError("topic or outline is too long")
    base = _folder(folder)
    region = Region.from_dict(json.loads((base / "region.json").read_text(encoding="utf-8")), check_area=False)
    mf = read_manifest(resolve_data_path(_in_root(manifest), _root())) if manifest else None
    spec = scaffold_spec(region, topic, mf, outline, template, None, framework)
    StorySpec.model_validate(spec)
    return {"ok": True, "spec": spec, "sections": [s["title"] for s in spec["sections"]]}


@server.tool()
@_guard
def find_findings(data: dict[str, str] | None = None, manifest: str | None = None, limit: int = 30) -> dict[str, Any]:
    """Measure a standard battery over the data layers (reach of services, gaps, which half of the area holds what, concentration,
    how layers relate) and return the notable results as sentences with claims that already pass. Call it after describe_data and
    before writing: write from these measured findings, and paste their claims into the spec."""
    from .findings import all_claims, compute_findings, render_markdown
    if not 1 <= limit <= 60:
        raise ValueError("limit must be between 1 and 60")
    store = _store(data, manifest)
    found = compute_findings(store, limit=limit)
    return {"ok": True, "markdown": render_markdown(found, store), "claims": all_claims(found),
            "findings": [{"id": f.id, "topic": f.topic, "text": f.text, "claim_ids": [c["id"] for c in f.claims]} for f in found]}


@server.tool()
@_guard
def explore_data(out_name: str, data: dict[str, str] | None = None, manifest: str | None = None, title: str = "Data explorer") -> dict[str, Any]:
    """Write a self-contained HTML map of the data layers (toggle layers, zoom, click a feature for its attributes) to
    STORYMAP_OUT_DIR/<out_name>, to show the user what the data holds before writing. ``out_name`` ends in .html; it needs no network."""
    from .explore import build_explorer_html
    if not re.match(r"^[\w.-]{1,100}\.html$", out_name):
        return {"ok": False, "error": "out_name must be a plain file name ending in .html"}
    html = build_explorer_html(_store(data, manifest), title[:100])
    out_dir = Path(os.environ.get("STORYMAP_OUT_DIR", "out"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / out_name
    path.write_text(html, encoding="utf-8")
    return {"ok": True, "path": str(path.resolve()), "kilobytes": len(html) // 1024}


@server.tool()
@_guard
def preview_layouts(spec: dict[str, Any]) -> dict[str, Any]:
    """HTML wireframes of the three layouts, to choose a layout before writing the full story."""
    return {"ok": True, "html": render_preview(_spec(spec))}


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
