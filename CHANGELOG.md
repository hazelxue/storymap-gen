# Changelog

## v0.3.0
- **Any place, from scratch.** `region` (place name to boundary), `fetch` (OpenStreetMap themes, public ArcGIS layers, GeoJSON links), `describe` (a profile of the data), `init` (a skeleton spec from a topic and an outline).
  Maps are generated from data layers (`"map": {"layers": [...]}`) as Web Maps with the features embedded; `publish-spec` creates private Web Maps and a private story draft.
- `login` / `logout`: OAuth sign-in with a hidden terminal prompt, token kept in a user-only file until it expires; the token never goes through a chat window.
- New claim type `layer_length`; `layer_area` merges overlapping polygons (`dissolve`).
- The quality gate refuses placeholder text (`TODO`, `TBD`, ...), so a scaffold cannot be published unfinished.
- MCP: `resolve_region`, `fetch_osm_layers`, `describe_data`; tools accept a `manifest`; the data root defaults to the working directory. Still no publish tool and no arbitrary-URL download.
- **Security audit and hardening** ([docs/security.md](docs/security.md)): token can only go to a validated ArcGIS host, redirects are never followed; claim filters are parsed with a whitelist instead of evaluated;
  model-supplied regexes are screened for catastrophic backtracking; every input is size-bounded; downloads refuse private addresses, redirects and oversized responses; MCP file access is confined to a data root.
- **Reliability**: property and fuzz tests, corrupt-file tests, a soak script (`scripts/soak.py`); CLI and MCP turn any failure into a clear message.
- Found with real data: unclipped OpenStreetMap features made a district's park area 160 times too large; all downloads are now clipped to the place boundary.
- Examples: `examples/gracia_barcelona.json` (a district fetched with the new pipeline). Docs: data sources, security, contributing guide for themes.
- Not yet confirmed: the first import of a generated Web Map into the live ArcGIS service (the structure is tested offline).

## v0.2.0
- **Prose numbers are bound to claims**: every number written in a section intro or slide body must be backed by a verified claim; the quality gate fails otherwise.
- New claim types `layer_area` and `within_distance`; orientation claims accept `grid_regex: null` (cities without numbered streets) and `stated_deviation_deg`.
- Claims check themselves (`claims.py`); adding a type is one class. `geo.py` holds the geometry helpers.
- Second domain: Barcelona's Eixample (OpenStreetMap), and a Jackson Heights open-space example that reproduces the 14.33 acre figure from data.
- Public Python API (`storymap_gen.api`) and an MCP server (`storymap-gen-mcp`) with five tools; publishing is deliberately not exposed.
- Packaging: `pyproject.toml`, `storymap-gen` command, typed package, ruff and mypy clean.
- Tests: property-based tests (hypothesis), MCP protocol tests, a live format-contract test (`pytest -m network`), JSON schema sync test.
- CI: lint, types and tests on Python 3.10 and 3.12; weekly live contract check.
- CLI: readable summary with `fix:` hints (did-you-mean for street names); `--json` for the full report; new `schema` command.
- Slide maps are optional for `longform` and required for the sidecar layouts.
- Docs: concepts, claim reference, MCP setup, contributing guide, fixture sources; the skill now installs and runs from the package.

## v0.1.0
- Structured story spec (pydantic) and compiler to StoryMaps JSON, three layouts, one theme.
- Claim verification against GIS data (street orientation, layer statistics) and a quality gate.
- Draft publishing through an OAuth token; verified in the live StoryMaps Builder.
- Ask-first vs direct generation experiment with real model calls (`experiments/RESULTS.md`).
