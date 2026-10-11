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
- **Sign-in is manual.** The OAuth token lasts about an hour and is entered by the user in a hidden prompt. There is no refresh flow on purpose (no stored secrets).
- **Test items cannot be removed by the tool.** It never deletes anything; items created during validation are private and have to be deleted by their owner in the portal.
- **Qualitative statements are not verified**; only numbers in prose are bound to claims.
- **One theme, three layouts**, tested in one organisation's portal. Behaviour in other portal versions (for example Enterprise) is unknown.
