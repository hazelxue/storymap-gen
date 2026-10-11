# Validation log: what the real services showed that tests did not

Offline tests prove the code does what the author expected. They cannot show what ArcGIS, OpenStreetMap or the Builder do with the output.
This log records every shortfall found while running the tool against the real services for places it was not built around
(Gràcia, Barcelona, was the main case: [showcase](showcase/gracia.md)). Each entry says how it showed up, why tests missed it, and what changed.

| # | Found by | Shortfall | Status |
|---|----------|-----------|--------|
| 1 | Real Overpass data | Unclipped OpenStreetMap features made a district's park area 160 times too large (a nature reserve extends far outside it) | Fixed: all downloads are clipped to the boundary; pinned by a test |
| 2 | Real StoryMaps import | Generated Web Map was never opened live before; a map showed *all* transit features (21) while the text claimed *stations* (6): the story passed every check and was still wrong on screen | Fixed: `map.filters` and a `map_problems` gate (unknown layer, invalid filter, filter that selects nothing) |
| 3 | Map Viewer | The six stations drew **no points at all** while other features of the same layer did | Fixed: see below |
| 4 | Real StoryMaps import | In the longform layout sub-headings were pushed out of line with the paragraphs under them (`size: wide` on `h3`) | Fixed: sub-headings are no longer forced wide |
| 5 | CI on Python 3.12 | A missing geometry is `None` in some pandas versions and `NaN` in others; the map builder crashed on one of them | Fixed: type check instead of `is None`; pinned by a test |
| 6 | Clean-environment install | The built wheel lacked the `sources` subpackage, so `fetch` failed after `pip install` although every test passed | Fixed: package discovery plus a test that compares packages on disk with packaged ones |
| 7 | Security review | Multicast and IPv4-mapped IPv6 addresses passed the "public address only" download check | Fixed: `_is_public`; tests |
| 8 | Writing the long example | The `bike` theme asked OpenStreetMap for every way with a `cycleway` tag, including `cycleway=no`: 37.5 km of "bike infrastructure" in a district that has 14.3 km. A count that passes every check can still describe the wrong thing | Fixed: the query keeps lane, track, shared lane and separate only; `describe` shows value counts so the trap is visible; the writing guide says to read the layer definition |
| 9 | Reading the first story back | The first generated stories passed every check yet were two sections and about 150 words, with qualitative statements ("at the edge", "one of the densest") that no check covers | Fixed in v0.5.0: content blocks, frameworks, depth report; the long example avoids statements it cannot verify, and the guide says to check any direction against the data |
| 10 | Opening the full example in the Builder | Pictures given as web addresses (`provider: uri`) are ignored by the Builder: the cover and every picture stayed empty, with no error | Fixed: with `publish-spec --download-images` each picture is fetched through the guarded client (https, public address, no redirect, 8 MB, identified by its bytes), uploaded as a resource of the draft, and referenced as an `item-resource`; without the flag pictures are left out and the tool says so |
| 11 | Opening the full example in the Builder | A swipe needs the two sides of one map to differ by layer visibility, with shared layers on both sides; the first design had no way to say "shown on both" | Fixed: layers named on neither side show on both; verified live (slider, legend, captions) |
| 12 | Tianjin (second place) | Nominatim's match for one district carried a stray boundary piece in another province: the study area became 90,000 km2 and the download was refused | Fixed: parts of a boundary far from its main body are dropped with a warning; several places can be combined into one region |
| 13 | Tianjin (second place) | With only counts and totals to go on, a writer had nothing notable to say about a place it had not studied by hand | Fixed: `findings` measures reach, gaps, skews, concentration and relations for any layers, ranks them, and hands out claims that already pass |

## Entry 3 in detail: points that silently do not draw

`osm_id` values of OpenStreetMap nodes now exceed 2^31 (the Gràcia station has id 5308271838). The map builder typed every integer column
as `esriFieldTypeInteger`, which is 32 bits. ArcGIS did not report an error; it simply did not draw the features whose value overflowed.
In the Gràcia transit layer 13 of 21 features overflowed, **including all 6 stations**, so the filtered map looked empty and the unfiltered one
looked "mostly fine" (the 8 small ids drew). That is the worst kind of failure: no error, a plausible picture, wrong content.

How it was found: the two stored layers were compared key by key (definition, symbol, geometry, popup, spatial reference, extent).
Everything was equal except attribute types, which led to the data: all drawn features had ids below 2^31 and none of the undrawn ones did.
Why tests missed it: structure tests check that fields exist and have a valid type, not that a value fits the type.
Fix: an integer column that does not fit 32 bits becomes `esriFieldTypeDouble` (exact up to 2^53, far above any OSM id). Pinned by
`test_large_integer_attributes_do_not_get_a_32_bit_field`.

## Still open

- **The StoryMaps item JSON is not an officially documented format.** It was reverse-engineered from published stories. A weekly contract test (`pytest -m network`) watches for drift, but Esri can change it.
- **Only the structure of a draft is checked automatically.** Whether a layout *looks* right (spacing, alignment, readability) was judged by opening it in the real Builder; there is no automated visual check.
- **Embedded features are limited** to 20,000 per layer and about 8 MB per map, and 12 attribute columns per layer. Larger places need a hosted feature layer, which this tool does not create.
- **Sign-in needs a one-time setup.** Signing in is now one click (the browser opens, you click Allow; PKCE with a one-shot local listener, checked against the real service, including silent renewal), but each user still creates one OAuth credential item in their own ArcGIS account first (three minutes, with screenshots in [the step-by-step guide](step-by-step.md)), because a shared credential would have to be registered under one person's or organisation's account. Renewal is opt-in (`--stay-signed-in`), because it keeps a two-week renewal token on the user's disk.
- **Test items cannot be removed by the tool.** It never deletes anything; items created during validation are private and have to be deleted by their owner in the portal.
- **Qualitative statements are not verified**; only numbers in prose are bound to claims.
- **One theme, three layouts**, tested in one organisation's portal. Behaviour in other portal versions (for example Enterprise) is unknown.

## A fresh model, only the skill: what it produced and what it hit
After the Tianjin story (written by the same model that built the tool), a separate model was given only the skill's instructions and the Tianjin data and asked for a story on a different theme (access to schools and hospitals), with no outline and no help.
It produced a story of about 1,500 words, four sections, 14 subsections, seven maps (one a swipe), three rows of key figures and 45 claims, and the gate passed after three fix rounds. The gate caught a number written with no claim behind it.
Its own judgement was "roughly a B": a sound, honest access analysis with a real thesis (schools reach 96% of the area within 1,000 m, hospitals 61%) that flags its limits, but not vivid (no pictures, few named places), measuring reach of land rather than of people.

What it found that the tool could not catch, and what changed:
| found | status |
|---|---|
| A sentence paired a correct number with the wrong half ("south and east hold 67%": 67% is the south alone). Every number was backed, so the gate passed | **Open.** The gate checks that a number exists among the attached claims, not that it belongs to the noun beside it. A claim-to-sentence check needs language understanding |
| A map caption said stations "follow the southern and central districts"; measured, 52% of stations are in the north. Captions and qualitative statements are not verified | **Open.** The writing guide says to check any direction against the data; the agent did, because it probed |
| `init` refused the 194 km2 region that was already downloaded | Fixed: the area limit applies to downloads only |
| A plain-text outline was silently ignored | Fixed: plain lines are sections |
| A `where` on `within_distance` was silently ignored, so "schools" counted all 404 education sites | Fixed: unknown fields are refused; the docs say there is no `where` there |
| `layer_share` with `half` divides by the whole layer unless `of_where` is given | Fixed in the docs, with an example |
| To read a value the agent wrote claims with `expected: 0` and read the fix lines | **Open.** A `measure` command would help; `findings` covers the common cases |
| `findings` lists generic items ("65% of street length have oneway = yes") that an access story does not need, and does not rank by theme | **Open.** A `--theme` option is the next step |
| No population, so "reach" is reach of land; no district-level split although the area is six districts | **Open.** Needs population data and sub-area layers |

The honest reading: the tool plus skill let a fresh model reach a passing, depth-adequate, numerically sound story from raw data in about seven minutes. It does not make that story vivid or population-aware, and the gate cannot tell a true number attached to the wrong claim.
