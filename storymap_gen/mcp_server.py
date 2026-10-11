"""MCP server: lets an agent (Claude Code, Claude Desktop, any MCP client) use the verify-first pipeline.

Run:  storymap-gen-mcp        (stdio)

Deliberately NOT exposed: publishing. Creating items in an ArcGIS account needs a user-authorised token, which
should never pass through a model. Use `storymap-gen publish` yourself after reviewing the compiled story.

Environment:
  STORYMAP_DATA_ROOT  optional; data files must live inside this directory
  STORYMAP_OUT_DIR    where compiled stories are written (default ./out)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from .api import build, load_store
from .preview import render_preview
from .spec import StorySpec
from .verify import orientation_facts

server = MCPServer(
    "storymap-gen",
    instructions=(
        "Build ArcGIS StoryMaps with verified facts. Workflow: get_spec_schema, then measure_street_orientation "
        "for the streets you will mention, then write the spec (every number in prose must be backed by a claim), "
        "then verify_spec, then compile_spec. Never state a fact about the data that a tool did not measure."),
)

SAFE_NAME = re.compile(r"^[\w.-]+\.json$")


def _spec(spec: dict[str, Any], template: str | None = None) -> StorySpec:
    return StorySpec.model_validate({**spec, **({"template": template} if template else {})})


def _invalid(e: ValidationError) -> dict[str, Any]:
    return {"ok": False, "error": "spec_invalid",
            "details": [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()][:10]}


@server.tool()
def get_spec_schema() -> dict[str, Any]:
    """JSON Schema of the story spec (sections, slides, sources, and the four claim types)."""
    return StorySpec.model_json_schema()


@server.tool()
def measure_street_orientation(data: dict[str, str], streets: list[str], layer: str = "streets_centerline",
                               name_field: str = "full_stree") -> str:
    """Measure how far each street deviates from the neighbourhood grid (degrees). Call this BEFORE writing
    claims about whether a street is diagonal or parallel to the grid. ``data`` maps layer names to GeoJSON paths."""
    return orientation_facts(load_store(data), streets, layer=layer, name_field=name_field)


@server.tool()
def verify_spec(spec: dict[str, Any], data: dict[str, str]) -> dict[str, Any]:
    """Check every claim against the data and every number in the prose against the claims. Does not compile."""
    try:
        parsed = _spec(spec)
    except ValidationError as e:
        return _invalid(e)
    result = build(parsed, load_store(data))
    return {"ok": True, "passed": result.passed,
            "claims": [{"id": r.id, "status": r.status, "detail": r.detail} for r in result.results],
            "unbacked_numbers": result.report["unbacked_numbers"],
            "uncited_sources": result.report["citation"]["uncited_sources"]}


@server.tool()
def compile_spec(spec: dict[str, Any], data: dict[str, str], out_name: str, template: str | None = None) -> dict[str, Any]:
    """Run the full quality gate and, only if it passes, write the StoryMaps JSON to STORYMAP_OUT_DIR/<out_name>.
    ``out_name`` must be a plain file name ending in .json. ``template``: docked-sidecar, floating-sidecar or longform."""
    if not SAFE_NAME.match(out_name):
        return {"ok": False, "error": "out_name must be a plain file name ending in .json"}
    try:
        parsed = _spec(spec, template)
    except ValidationError as e:
        return _invalid(e)
    result = build(parsed, load_store(data))
    if result.story is None:
        return {"ok": False, "error": "quality_gate_failed", "report": result.report}
    out_dir = Path(os.environ.get("STORYMAP_OUT_DIR", "out"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / out_name
    path.write_text(json.dumps(result.story, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "path": str(path.resolve()), "nodes": len(result.story["nodes"]), "report": result.report}


@server.tool()
def preview_layouts(spec: dict[str, Any]) -> dict[str, Any]:
    """HTML wireframes of the three layouts, to choose a layout before writing the full story."""
    try:
        return {"ok": True, "html": render_preview(_spec(spec))}
    except ValidationError as e:
        return _invalid(e)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
