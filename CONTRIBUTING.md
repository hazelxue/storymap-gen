# Contributing

Thanks for looking. This is a small project, and the bar is simple: every change keeps the quality gate honest and the inputs untrusted.

## Set up
```bash
git clone https://github.com/hazelxue/storymap-gen && cd storymap-gen
pip install -e ".[dev]"
python -m pytest -q          # unit, property, fuzz, security and protocol tests; no network
ruff check . && mypy storymap_gen
```
`pytest -m network` runs the live contract test against the real StoryMaps service. `python scripts/soak.py --n 5000` pushes random inputs through the whole pipeline.
Tests never use the network (downloads are faked, and a real Overpass response is recorded in `fixtures/`) and never touch your real token or cache.

## Workflow
1. Branch from `main` (`feature/<name>`), keep commits small.
2. Add a test with the change. A bug fix starts with a test that fails.
3. Run the checks above, then open a pull request and describe what you measured.
4. Record user-visible changes in `CHANGELOG.md`.

## Treat inputs as untrusted
The spec comes from a model that may have read hostile text, and data comes from the internet. When you add a field, a claim, a download or a tool: bound its size, validate its format,
never evaluate it as code, never put it in a path or URL unvalidated, and add an attack to `tests/test_security.py`. See [docs/security.md](docs/security.md).

## Add a claim type
A claim is a pydantic model that knows how to check itself ([`claims.py`](storymap_gen/claims.py)).

1. Subclass `_Claim`, give it a unique `kind` literal and the parameters it needs (bound every string, make numbers finite).
2. Implement `check(self, store) -> ClaimResult`. Return `PASS`, `FAIL` (the sentence is wrong) or `ERROR` (the claim could not be evaluated), with a `detail` that shows the measured value and a `hint` that says what to change.
3. If the claim authorises numbers in prose, implement `stated_values()`.
4. Add the class to the `Claim` union at the bottom of the file, and to the import in `spec.py`.
5. Regenerate the schema: `storymap-gen schema > docs/spec.schema.json` (a test fails if you forget).
6. Add tests in `tests/test_claims.py` (a PASS, a FAIL and an ERROR case) and a section in `docs/claims.md`.

Ideas: a ratio claim (park area per resident, needs a population input), `layer_overlap`, a claim on raster statistics.

## Add a content block
A block is one kind of thing a section can hold (paragraph, list, swipe, ...). To add one: (1) a pydantic class in `storymap_gen/spec.py` with a unique `type` literal, added to the `Block` union;
(2) in `StorySpec.prose()` yield every piece of reader-facing text it holds, so numbers are checked and placeholders are refused; (3) compile it in `storymap_gen/compiler.py`;
(4) count it in `storymap_gen/depth.py`; (5) regenerate `docs/spec.schema.json`; (6) a test that compiles it to a valid graph and one that checks its text is grounded. Open the result in the real Builder before calling it done.

## Add a data theme
Themes live in `LAYERS` in [`sources/overpass.py`](storymap_gen/sources/overpass.py): an Overpass query with a `{bb}` placeholder, the geometry types to keep, the tags to keep as columns, and a styling role.
Add the theme, a row in `docs/data-sources.md`, and a test that parses a recorded response (record one with a small bounding box and put it in `fixtures/`). Add a style for a new role in `webmap.py`.
A new source type (census, another open-data API) goes in `sources/` and must use `net.request_bytes` for every request.

## Add a layout
Layouts live in `compiler.py`. Add the name to `Template` in `spec.py`, a layout function, and extend the property tests in `tests/test_properties.py`. A new layout must also be opened once in the
StoryMaps Builder; say in the pull request how you checked.

## Good first issues
- Support `.csv` with lon/lat columns in `DataStore`.
- Clip to the place boundary for local files (`storymap-gen clip`).
- A census connector for population by district (needs a source that is open and stable).
- Per-slide map extent helper: derive the extent from the layers a slide's claims touch.

## Ground rules
- Never commit tokens, `.env` files, or personal data. Fixtures must be redistributable; record the source in `fixtures/README.md`.
- Do not add a code path that publishes to a user's account without an explicit command from the user, or that sends a credential anywhere but a validated ArcGIS host.
