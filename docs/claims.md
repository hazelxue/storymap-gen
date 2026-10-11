# Claim reference

Every claim has an `id` (unique, referenced from `claim_ids` of a section or slide) and a `text` (the sentence it backs).
Run `storymap-gen run spec.json --data layer=file.geojson` to check them. Data layers are any file GeoPandas can read
(GeoJSON, GeoPackage, shapefile); the name before `=` is the `layer` a claim refers to.

| kind | checks | key fields |
|---|---|---|
| `street_orientation` | a street is diagonal or parallel to the neighbourhood grid | `street`, `relation`, `tolerance_deg`, `grid_regex`, `stated_deviation_deg` |
| `layer_stat` | count / min / max / mean / sum of a field | `layer`, `field`, `agg`, `expected`, `tolerance`, `where` |
| `layer_area` | total polygon area | `layer`, `unit` (`acres`, `sq_ft`, `sq_m`), `expected`, `tolerance`, `where` |
| `within_distance` | share of features within D metres of another layer | `layer`, `target_layer`, `target_names`, `max_distance_m`, `expected_share` |

## street_orientation
```json
{"kind": "street_orientation", "id": "broadway", "text": "Broadway cuts across the grid.",
 "street": "BROADWAY", "relation": "diagonal_to_grid"}
```
The grid axis is the dominant street direction (length-weighted). A street is **diagonal** if its median deviation from the nearest
grid axis exceeds `tolerance_deg` (default 15), otherwise **parallel**. Segments are measured in the local UTM zone.

`grid_regex` says which streets define the grid. The default matches NYC numbered streets and avenues (`34 AVE`, `82 ST`).
For a city without numbered streets use `"grid_regex": null`, which uses every segment in the layer, and set `name_field`
to the layer's street-name column (OpenStreetMap: `name`). Barcelona's Eixample works this way; see
`examples/barcelona_eixample.json`.

If the prose quotes the measured angle ("37 degrees off the grid"), put it in `stated_deviation_deg` and it is verified too.

## layer_stat
```json
{"kind": "layer_stat", "id": "avg-width", "text": "Streets average 32.6 feet.",
 "layer": "streets_centerline", "field": "st_width", "agg": "mean", "expected": 32.6, "tolerance": 0.1}
```
`where` is a pandas query string (`"name.notna()"`, `"type == 'park'"`). `count` needs no `field`.

## layer_area
```json
{"kind": "layer_area", "id": "public-parks", "text": "Named parks cover 14.33 acres.",
 "layer": "parks_", "where": "name.notna()", "unit": "acres", "expected": 14.33, "tolerance": 0.05}
```
On the Jackson Heights data this reproduces the figure the original story computed by hand (measured 14.32 acres).

## within_distance
```json
{"kind": "within_distance", "id": "stations-on-roosevelt", "text": "Every station is within 50 m of Roosevelt Avenue.",
 "layer": "subway_stations_", "target_layer": "streets_centerline", "target_name_field": "full_stree",
 "target_names": ["ROOSEVELT AVE"], "max_distance_m": 50, "expected_share": 1.0}
```

## Numbers in prose
A number written in a section intro or slide body must equal a value stated by a claim attached to that block (or to the
section). `expected`, `expected_share` (also as a percentage), `max_distance_m` and `stated_deviation_deg` count as stated.
A number may be rounded to the precision it is written with ("33" for 32.6; not "34"). Years, ordinals (`34th`), street names
(`34 AVE`) and labels (`Phase 1`) are ignored. For a number that comes from a source rather than the data (a quoted budget),
list it in the spec's `allow_numbers`.

## Adding a claim kind
See [CONTRIBUTING.md](../CONTRIBUTING.md#add-a-claim-type).
