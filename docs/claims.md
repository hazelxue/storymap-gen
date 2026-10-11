# Claim reference

Every claim has an `id` (letters, digits, `.`, `_`, `-`; unique; referenced from `claim_ids` of a section or slide) and a `text` (the sentence it backs).
Run `storymap-gen run spec.json --manifest data/manifest.json` (or `--data layer=file.geojson`) to check them. The layer a claim names is the layer name you gave the data.
`storymap-gen describe` shows each layer's fields and which claim types fit.

| kind | checks | key fields |
|---|---|---|
| `layer_stat` | count / min / max / mean / sum of a field | `layer`, `field`, `agg`, `expected`, `tolerance`, `where` |
| `layer_area` | total polygon area (overlaps merged) | `layer`, `unit` (`acres`, `sq_ft`, `sq_m`), `expected`, `tolerance`, `where`, `dissolve` |
| `layer_length` | total length of lines | `layer`, `unit` (`km`, `mi`, `m`), `expected`, `tolerance`, `where` |
| `within_distance` | share of features within D metres of another layer | `layer`, `target_layer`, `target_names`, `max_distance_m`, `expected_share` |
| `street_orientation` | a street is diagonal or parallel to the neighbourhood grid | `street`, `relation`, `tolerance_deg`, `grid_regex`, `stated_deviation_deg` |

Measurements use the local UTM zone, so lengths and areas are in metres.

## Filters (`where`)
`where` selects features before measuring. It is a small safe language, **not** Python or pandas: column names, comparisons, `in`, `and` / `or` / `not`, and `.notna()` / `.isna()`.
```text
name.notna()            highway == 'living_street'            st_width >= 30 and st_width < 50
amenity in ['school', 'kindergarten']            not name.isna()            (a == 1) or (b != 'x')
```
Anything else (function calls, arithmetic, attribute access, text methods) is rejected, because the claim comes from a model and must never be evaluated as code.

## layer_stat
```json
{"kind": "layer_stat", "id": "schools", "text": "There are 40 schools.",
 "layer": "schools", "where": "amenity == 'school'", "agg": "count", "expected": 40}
```
`count` needs no `field`. `tolerance` is in the unit of the statistic.

## layer_area
```json
{"kind": "layer_area", "id": "green-area", "text": "Parks cover 134 acres.",
 "layer": "parks", "unit": "acres", "expected": 134.0, "tolerance": 0.1}
```
With `dissolve: true` (the default) overlapping polygons are merged first, so a garden inside a park is not counted twice. On the NYC data this reproduces the 14.33 acres of named parks a story had computed by hand.

## layer_length
```json
{"kind": "layer_length", "id": "calm-streets", "text": "26.7 km are living streets.",
 "layer": "streets", "where": "highway == 'living_street'", "unit": "km", "expected": 26.7, "tolerance": 0.05}
```

## within_distance
```json
{"kind": "within_distance", "id": "schools-near-parks", "text": "Every school is within 400 m of a park.",
 "layer": "schools", "target_layer": "parks", "max_distance_m": 400, "expected_share": 1.0}
```
`target_names` (with `target_name_field`) restricts the target layer to named features, for example one street.

## street_orientation
```json
{"kind": "street_orientation", "id": "broadway", "text": "Broadway cuts across the grid.",
 "street": "BROADWAY", "relation": "diagonal_to_grid"}
```
The grid axis is the dominant street direction (length-weighted). A street is **diagonal** if its median deviation from the nearest grid axis exceeds `tolerance_deg` (default 15), otherwise **parallel**.

`grid_regex` says which streets define the grid. The default matches NYC numbered streets and avenues (`34 AVE`, `82 ST`). For any other city use `"grid_regex": null`, which uses every segment in the layer,
and set `name_field` to the street-name column (OpenStreetMap: `name`). If the prose quotes the angle ("0.4 degrees off the grid"), put it in `stated_deviation_deg` and it is verified too.
A street that lies outside the study area is reported as not found, with the closest names.

## Numbers in prose
A number written in a section intro or slide body must equal a value stated by a claim attached to that block (or to the section): `expected`, `expected_share` (also as a percentage),
`max_distance_m`, `tolerance_deg` and `stated_deviation_deg` count as stated. A number may be rounded to the precision it is written with ("134" for 134.01; not "135"). Years, ordinals (`34th`), street
names (`34 AVE`) and labels (`Phase 1`) are ignored. For a number that comes from a source rather than the data (a quoted budget) list it in the spec's `allow_numbers`.

## Adding a claim kind
See [CONTRIBUTING.md](../CONTRIBUTING.md#add-a-claim-type).
