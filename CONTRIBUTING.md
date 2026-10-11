# Contributing

Thanks for looking. This is a small project, and the bar is simple: every change keeps the quality gate honest.

## Set up
```bash
git clone https://github.com/hazelxue/storymap-gen && cd storymap-gen
pip install -e ".[dev]"
python -m pytest -q          # unit, property and protocol tests, no network
ruff check . && mypy storymap_gen
```
`pytest -m network` runs the live contract test against the real StoryMaps service.

## Workflow
1. Branch from `main` (`feature/<name>`), keep commits small.
2. Add a test with the change. A bug fix starts with a test that fails.
3. Run the checks above, then open a pull request and describe what you measured.
4. Record user-visible changes in `CHANGELOG.md`.

## Add a claim type
A claim is a pydantic model that knows how to check itself ([`claims.py`](storymap_gen/claims.py)).

1. Subclass `_Claim`, give it a unique `kind` literal and the parameters it needs.
2. Implement `check(self, store) -> ClaimResult`. Return `PASS`, `FAIL` (the sentence is wrong) or `ERROR` (the claim could not be
   evaluated), with a `detail` that shows the measured value and a `hint` that says what to change.
3. If the claim authorises numbers in prose, implement `stated_values()`.
4. Add the class to the `Claim` union at the bottom of the file.
5. Regenerate the schema: `storymap-gen schema > docs/spec.schema.json` (a test fails if you forget).
6. Add tests in `tests/test_claims.py` (a PASS, a FAIL, and an ERROR case) and a row in `docs/claims.md`.

Ideas: a ratio claim (park area per resident), `layer_overlap` (share of one layer covered by another), a claim on raster statistics.

## Add a layout
Layouts live in `compiler.py`. Add the name to `Template` in `spec.py`, a layout function, and extend the property tests
in `tests/test_properties.py` (they check that any valid spec compiles to a valid graph). A new layout must also be opened once in the
StoryMaps Builder; say in the pull request how you checked.

## Good first issues
- Support `.csv` with lon/lat columns in `DataStore`.
- A `storymap-gen init` command that scaffolds a spec from the scoping questions.
- Per-slide map `extent` helper: derive the extent from the layers a slide's claims touch.

## Ground rules
- Never commit tokens, `.env` files, or personal data. Fixtures must be redistributable; record the source in `fixtures/README.md`.
- Do not add a code path that publishes to a user's account without an explicit command from the user.
