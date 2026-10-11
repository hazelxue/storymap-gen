# MCP server

`storymap-gen-mcp` exposes the verify-first pipeline to any MCP client over stdio, so an agent can go from a place name to a checked story.

```bash
pip install "storymap-gen[mcp]"
```

Register it with your client. For Claude Code:
```bash
claude mcp add storymap-gen -- storymap-gen-mcp
```
For Claude Desktop, add to `claude_desktop_config.json`:
```json
{"mcpServers": {"storymap-gen": {"command": "storymap-gen-mcp",
  "env": {"STORYMAP_DATA_ROOT": "/path/to/project", "STORYMAP_OUT_DIR": "/path/to/project/out"}}}}
```

| tool | purpose |
|---|---|
| `resolve_region` | a place name (or bbox) to a region with a boundary; saved as `<folder>/region.json` |
| `fetch_osm_layers` | download OpenStreetMap themes for that region, cut to the boundary; writes GeoJSON and `manifest.json` into the folder |
| `describe_data` | profile the layers: counts, totals, fields with real values, which claim types fit |
| `get_spec_schema` | the JSON Schema of the story spec |
| `measure_street_orientation` | measure how far streets deviate from the grid, before writing claims |
| `verify_spec` | check every claim, every number in the prose, and placeholders; does not compile |
| `compile_spec` | run the full gate; write the StoryMaps JSON only if it passes |
| `preview_layouts` | HTML wireframes of the three layouts |

Recommended order: `resolve_region`, `fetch_osm_layers`, `describe_data`, `get_spec_schema`, write the spec, `verify_spec`, `compile_spec`. Layers come from `manifest` (a path inside the data root)
and/or `data` (a map of layer name to GeoJSON path).

## Safety
- **No publishing tool, no credentials, no arbitrary-URL downloads.** Creating items in an ArcGIS account needs a user-authorised token, which should not pass through a model: use
  `storymap-gen login` and `publish-spec` yourself. The only network access is to two fixed public services (Nominatim, Overpass), through a client that refuses private addresses, redirects and oversized responses.
- All files are read from and written to the **data root** (`STORYMAP_DATA_ROOT`, default the server's working directory); folder names must be plain; paths are not echoed back.
- Data paths must be GIS files (`.geojson`, `.json`, `.gpkg`, `.shp`). `compile_spec` only writes a plain `*.json` file name into `STORYMAP_OUT_DIR`.
- Every tool validates and size-limits its arguments and returns a structured error instead of raising; the server stays up after a bad call.

## Status
The protocol behaviour is covered by tests that talk to the server through an in-memory MCP client (`tests/test_api_and_mcp.py`, `tests/test_security.py`). It has not yet been exercised from inside Claude Desktop or Claude Code.
