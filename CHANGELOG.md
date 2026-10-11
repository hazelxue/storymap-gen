# Changelog

## v0.5.0
- **Step-by-step guide with screenshots** ([docs/step-by-step.md](docs/step-by-step.md)): from installing Python to a private draft in ArcGIS, each step with a checkpoint.
- **Simpler sign-in**: `storymap-gen login` now opens the browser, you click Allow, and you are back in the terminal: nothing to copy or paste (authorization code with PKCE and a one-shot local listener). `--stay-signed-in` renews the sign-in for about two weeks (opt-in; `logout` revokes it); `--manual` keeps the paste flow for a computer without a browser. The credential's redirect URL is now `http://localhost:8765/storymap-gen-callback`. Checked against the real ArcGIS service: sign-in, silent renewal and publishing a draft.
Rich stories. The first versions passed every check and still produced thin stories (a few sentences per section). A study of 31 public district StoryMaps
([docs/case-study-patterns.md](docs/case-study-patterns.md)) set the target: about 3,000 words, 5 sections, 11 subsections, 7 captioned maps, pictures, lists, swipes.
- **Content blocks** in a section: subsection headings (h3, h4), paragraphs, bullet and numbered lists, quotes, maps, **swipe** maps (two layers compared; shared base layers), rows of **key figures**, pictures (alt text required, caption, credit), separators and slides.
  Text may use `**bold**`, `*italic*` and `[label](https://link)`; paragraphs may cite sources. The old `intro` and `slides` fields still work.
- **Every kind of text is checked**: numbers in paragraphs, list items, quotes, key figures and captions must be backed by a claim; placeholders are refused anywhere. Licence versions (`CC BY 4.0`) are not mistaken for data.
- **Depth report** (`THIN ...` lines, never blocking): words, sections, subsections, maps, pictures against a `brief` / `standard` / `full` target, plus maps without captions, thin subsections, sections that state no measured fact, unused claims.
- **Frameworks**: `init --framework systems | essay | brief | outline` writes a skeleton with sections, subsections, maps chosen from the data, and a finding / map / meaning pattern per subsection; an indented outline becomes sections and subsections.
- **New claim types**: `layer_share` ("29% of street length"), `layer_density` (per km2), `area_coverage` (the share of the area within D metres of a layer); area units `ha` and `sq_km`. `fetch` now also writes the study area as a `boundary` layer.
- **`describe` lists named highlights** (longest streets, largest parks, station names) so prose can name real places.
- **`explore`**: one offline HTML file that draws the fetched layers (toggle, zoom, click for attributes) to show the user the data before writing. MCP: `explore_data`, `init_spec`.
- **Sign-in**: `status` command; the client id and organisation are remembered; organisation addresses are accepted in any common form; a clearer hidden-paste prompt with retries.
- **`context`** (and MCP `get_place_context`): Wikipedia background and reusable Wikimedia Commons pictures for a place, with a source entry, credit lines and image blocks to start from. Two fixed hosts, an exact article title is preferred over a search hit, only CC0 / public domain / CC BY / CC BY-SA photographs are listed, and the text is cleaned and handed over as data to verify.
- **Graduated-colour maps**: a `styles` entry on a generated map colours a layer by a numeric field in classes (quantile, equal interval or manual breaks; seven palettes), with a legend and a "No data" class; the gate fails on a missing, non-numeric or constant field. `join` brings a CSV of numbers onto a polygon layer (tolerant number parsing, key padding, refuses a poor match). `subareas` and `aggregate` give per-district counts, lengths and densities as a layer to colour or to cite. [docs/thematic-maps.md](docs/thematic-maps.md)
- **`findings`** (and MCP `find_findings`): a standard battery measured over any layers: reach at sensible distances, gaps, which half of the area holds what, concentration in a few big features, how a service layer relates to an access layer compared with a typical place, and what each layer is made of. Results are ranked, written as sentences, and each carries claims that already pass, so a writer starts from measured, notable facts. New: `layer_share` takes `half`.
- **Several places as one study area**: `region "A" "B" --name "..."` takes the union of their boundaries; a stray far-away piece in an OpenStreetMap boundary is dropped with a warning.
- Found by a fresh-agent test: unknown fields in a spec are now errors (a `where` on `within_distance`, or `filter` for `filters`, used to do nothing); a plain-text outline works; `init` no longer applies the download-size limit to a region already on disk; docs say what the whole is in a share.
- **Pictures**: the Builder ignores pictures given as web addresses, so `publish-spec --download-images` fetches each picture (guarded client: https, public address, no redirect, 8 MB, checked by its bytes), uploads it to the draft as a resource, and references it from the story. Without the flag pictures are left out and the command says so.
- **Verified live** in the StoryMaps Builder: key figures, swipe maps, inline maps, subsection headings, lists, pictures and the cover. See [docs/showcase/gracia.md](docs/showcase/gracia.md).
- Fix: the `bike` theme included streets tagged "no cycleway" (37.5 km for a district that has 14.3 km of bike infrastructure). Found while writing the long example; see the validation log.
- Example: [examples/gracia_barcelona_full.json](examples/gracia_barcelona_full.json), 46 claims, nine maps, three pictures with licences.

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

## v0.3.1
- **Generated maps verified in the live ArcGIS service** (Web Maps opened in Map Viewer, all three story layouts opened in the Builder). Findings in [docs/validation-log.md](docs/validation-log.md), a worked example in [docs/showcase/gracia.md](docs/showcase/gracia.md).
- `map.filters`: a per-layer filter (`{"transit": "railway == 'station'"}`) so a map shows exactly what the claim counted. The gate (`map_problems`) fails on unknown layers, invalid filters and filters that select nothing.
- Fix: integer columns that do not fit 32 bits (OSM ids) are written as doubles; before, ArcGIS silently did not draw those features.
- Fix: sub-headings are no longer forced to the wide size (misaligned in the longform layout).

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
