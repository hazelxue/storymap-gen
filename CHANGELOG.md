# Changelog

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
