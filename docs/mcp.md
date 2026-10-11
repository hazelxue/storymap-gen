# MCP server

`storymap-gen-mcp` exposes the verify-first pipeline to any MCP client over stdio.

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
  "env": {"STORYMAP_DATA_ROOT": "/path/to/your/gis/data", "STORYMAP_OUT_DIR": "/path/to/output"}}}}
```

| tool | purpose |
|---|---|
| `get_spec_schema` | the JSON Schema of the story spec |
| `measure_street_orientation` | measure how far streets deviate from the grid, before writing claims |
| `verify_spec` | check every claim and every number in the prose; does not compile |
| `compile_spec` | run the full gate; write the StoryMaps JSON only if it passes |
| `preview_layouts` | HTML wireframes of the three layouts |

Recommended order: `get_spec_schema`, `measure_street_orientation`, write the spec, `verify_spec`, `compile_spec`.

## Safety
- **No publishing tool.** Creating items in an ArcGIS account needs a user-authorised token, which should not pass through a model.
  Publish yourself with `storymap-gen publish`.
- Data paths must be GIS files (`.geojson`, `.json`, `.gpkg`, `.shp`). Set `STORYMAP_DATA_ROOT` to confine reads to one folder.
- `compile_spec` only writes a plain `*.json` file name into `STORYMAP_OUT_DIR`; path separators are rejected.

## Status
The protocol behaviour is covered by tests that talk to the server through an in-memory MCP client (`tests/test_api_and_mcp.py`).
It has not yet been exercised from inside Claude Desktop or Claude Code.
