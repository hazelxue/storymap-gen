# storymap-gen

Generate ArcGIS StoryMaps from a structured spec, with claims fact-checked against GIS data.

```
LLM -> StorySpec (pydantic) -> verify claims vs data -> compile to StoryMaps JSON -> evaluate gate
```

## Why
1. **Ask first, don't burn tokens**: scoping questions + layout preview before any full generation (`skill/SKILL.md`).
2. **Claim grounding**: statements about the data are executable checks. Origin: a story labelled Northern Boulevard as
   the street "breaking the grid". Measuring segment bearings shows it is parallel to the avenues
   (0.1 deg from the grid axis); Broadway is the diagonal one (37.1 deg). That manual check is now `verify.py`.
3. **Typed interface**: the LLM only emits a spec; invalid references fail at validation, not at publish time.
4. **Evaluation**: deterministic gates (graph integrity, citation consistency, claim pass-rate) plus an injected LLM judge.

## Layout
| file | role |
|---|---|
| `storymap_gen/spec.py` | pydantic spec, cross-reference validation |
| `storymap_gen/verify.py` | `street_orientation` and `layer_stat` claim checks |
| `storymap_gen/compiler.py` | spec -> node-graph JSON, 3 templates, one theme (`slate`) |
| `storymap_gen/evaluate.py` | graph checker, quality gate, LLM-judge hook |
| `storymap_gen/preview.py` | 3-layout wireframe preview |
| `fixtures/` | a real published story's item JSON and the real street layer (1,001 segments) |

The compile target was reverse-engineered from the public item data of a real story
(`/sharing/rest/content/items/<id>/data`): `{root, nodes, resources}`, with text nodes as HTML.

## Run
```
pip install pydantic geopandas shapely pyproj numpy pytest
python -m storymap_gen run examples/jackson_heights.json --data streets_centerline=fixtures/streets_centerline.geojson --out out/story.json
python -m storymap_gen preview examples/jackson_heights.json --out out/preview.html
python -m pytest -q
```

## What is verified
- 26 tests pass. The graph checker accepts the real 174-node story (0 issues) and rejects deliberately corrupted graphs.
- Compiled output conforms to the real schema: every node type exists in the real story and our `data`/`config` keys are a subset of the real ones (checked by test; a mutation with an unknown key is flagged).
- Orientation check is rotation-invariant (synthetic 30 degree grid test) and reproduces the Northern Blvd / Broadway result on real data.
- The pipeline refuses to write `story.json` when a claim fails (exit code 1).
- **Opened in the live StoryMaps Builder**: all three templates were published as private drafts and loaded without errors.
  `docked-sidecar` (text panel + real Web Map with legend), `floating-sidecar` (full-bleed map + floating card) and
  `longform` (heading, text, inline map) rendered; cover image, navigation bar and numbered reference list were present.

## Publish
```
# token comes from an OAuth consent (see below); it is read from the environment only
export ARCGIS_TOKEN=...   # 60 minute token
python -m storymap_gen publish out/story.json --title "My story" --username <user>
```
Creates a NEW private draft (`smstatusdraft`); never touches an existing item. One-time setup: create an OAuth
"Developer credentials" item (user authentication) in your org, then open
`<portal>/sharing/rest/oauth2/authorize?client_id=<id>&response_type=token&expiration=60&redirect_uri=<registered uri>`
and click Allow. The token appears in the redirect URL.

## Experiments (real model calls; details and limits in `experiments/RESULTS.md`)
Two simulated users with hidden preferences, 12 chains per arm, up to 4 revision rounds, `claude -p` as the backend.

| | direct | ask-first | ask-first + measured facts |
|---|---|---|---|
| accepted on first draft | 0% | 17% | 100% |
| mean rounds | 2.0 | 2.0 | 1.0 |
| cost per chain | $0.176 | $0.133 | $0.086 |

- Asking first removed all preference rework (0/12 chains vs 12/12) and cut cost 24% with no quality drop in a blind LLM judge (3.83 vs 4.00, p=0.32),
  but it **did not reduce the number of rounds**: about 20% of the model's data claims were wrong in both arms, most often calling Northern Blvd
  diagonal (the original manual mistake). Part of the saving is simply shorter output.
- Giving the writer the verifier's measured facts first (`python -m storymap_gen facts`) was the biggest effect: 10/10 first-draft acceptance, about half the cost.
  Small n, fixed street list, simulated users; see `experiments/RESULTS.md`.

## Known limits (not yet done)
- Maps open at the Web Map's default extent unless the spec gives `extent` for the slide.
- The `facts` step covers street orientation only; other claim kinds are not yet measured before writing.
- One theme and three layouts. Claim kinds are limited to orientation and layer statistics; borderline streets (Junction Blvd measures 14.5 deg against a 15 deg tolerance) show the threshold matters.
- Publishing creates a draft only; the user reviews it in the Builder and publishes it themselves.

## License
MIT, see `LICENSE`.
