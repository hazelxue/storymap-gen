# Graduated-colour maps and sub-area comparisons

Most district stories have a map where each area is coloured by a number: income, density, the share of something. This page shows how to make one, from a table of your own numbers or from the layers the tool already holds.

## 1. A map coloured by a field
Give a generated map a `styles` entry for a layer:
```json
{"type": "map", "map": {
  "layers": ["districts"],
  "styles": {"districts": {"field": "median_income", "classes": 5, "palette": "blues", "title": "Median income", "unit": " USD"}},
  "caption": "Median household income by tract: the darker the tract, the higher the income."}}
```
| option | meaning |
|---|---|
| `field` | the numeric column to colour by (required) |
| `method` | `quantile` (default: each class holds about the same number of areas), `equal` (equal value intervals) or `manual` |
| `classes` | 2 to 7 (default 5); fewer if many values repeat |
| `breaks` | for `manual`: `classes + 1` increasing numbers |
| `palette` | `blues`, `greens`, `oranges`, `reds`, `purples`, `teal`, `heat` (light to dark); `reverse` flips it |
| `title`, `unit` | the legend and popup name of the field, and a suffix for the class labels |

The classes are computed from the features that are drawn, so the legend always matches the map. Areas with no value are grey and labelled "No data". Points and lines can be graduated too.
The gate fails if the field is missing, is not a number, or has fewer than two different values. A map with a style is a different map from one without (and from one with another palette), so one layer can be shown in two colourings in the same story.

**Quantile or equal?** Quantile breaks guarantee a spread of colours but can put very different values in one class; equal intervals show outliers honestly but can leave most areas in one class. For a story about inequality, show equal intervals or give `manual` breaks that mean something (a poverty line); for a story about ranking, use quantile. Say which in the caption when it matters.

Numbers you state about the map (the highest district, the lowest value) are backed by claims in the usual way: `layer_stat` with `field`, `agg` and a `where` on the name.

## 2. Bring your own numbers: `join`
A table of figures (a census extract, a city indicator file) is joined to a polygon layer by a key column:
```bash
storymap-gen join --layer tracts.geojson --table income.csv --key GEOID --table-key geoid --fields median_income poverty_rate --zfill 11 --out tracts_income.geojson
storymap-gen run spec.json --data tracts=tracts_income.geojson
```
- Keys are compared as trimmed text; `--zfill` restores leading zeros that spreadsheets drop (`6037` to `06037`).
- Numbers are read tolerantly (`$54,300`, `12.5%`, `(3)`); anything that is not a number becomes empty, never a guess.
- It reports how many polygons matched and refuses a join that matches under half of them (a wrong key is the usual mistake); `--force` overrides.
- It never overwrites a column the layer already has: the table's column arrives as `<name>_joined`.
- The table must be `.csv`, `.tsv` or `.txt`, at most 50 MB and 500,000 rows. Polygon layers can come from your files or from a public ArcGIS layer (`fetch --arcgis name=URL`).
- Cite the table as a source of the story (`sources`), with its licence.

## 3. Compare the parts of the study area: `subareas` and `aggregate`
A combined region (`region "A" "B" "C" --name "..."`) remembers its places. Turn them into a layer and measure the other layers inside each:
```bash
storymap-gen subareas --region region.json --manifest data/manifest.json
storymap-gen aggregate --manifest data/manifest.json --subareas subareas --layers bus_stops transit streets --where "transit=railway == 'station'" --out-name subareas_stats
```
`subareas_stats` has, for each place: `area_km2`; for point layers `<layer>_count` and `<layer>_per_km2`; for lines `<layer>_km` and `<layer>_km_per_km2`; for polygon layers `<layer>_pct` (the percent of the place they cover).
Colour it with a `styles` entry, and back each sentence about one place with a `layer_stat` claim:
```json
{"kind": "layer_stat", "id": "heping-bus", "text": "Heping has 12.9 bus stops per km2", "layer": "subareas_stats",
 "field": "bus_stops_per_km2", "agg": "mean", "where": "name == 'Heping District'", "expected": 12.87, "tolerance": 0.01}
```
This is a **derived** layer: the tool computed the numbers from the other layers (points counted by the polygon that contains them, lines cut at the edge), and the claims check that the text matches the layer.
The computation itself is covered by tests, but the layer is only as good as the layers it came from, and the manifest says so. A feature on a shared edge is counted by each polygon that contains its representative point, so the parts can differ from the whole by a feature or two.

See [the Tianjin example](../examples/tianjin_transport.json), where a section compares six districts this way.
