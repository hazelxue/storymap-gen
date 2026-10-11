# Showcase: Gràcia, Barcelona, from a place name to a verified private draft

A district the tool was not built around, run end to end against the real services. Everything below is reproducible from this repository
(`fixtures/gracia/` holds the downloaded data so the verification step runs offline).

## What went in
- A place: "Gràcia, Barcelona" (geocoded to a boundary polygon with Nominatim).
- A topic and outline: streets and green space, two sections ([examples/gracia_barcelona.json](../../examples/gracia_barcelona.json)).
- Data: OpenStreetMap streets, parks, schools and transit, downloaded with `storymap-gen fetch` and clipped to the boundary.

## What the tool checked before it would build anything
Every number in the text is a claim measured on the data. This is the real output:

```
PASS  street-length: length = 91.90 km over 1205 features, claimed 91.9 +/- 0.05
PASS  calm-streets: length = 26.67 km over 383 features, claimed 26.7 +/- 0.05
PASS  green-area: area = 134.01 acres over 150 features, claimed 134.0 +/- 0.1
PASS  schools: count(*) = 40, claimed 40.0 +/- 0.0
PASS  stations: count(*) = 6, claimed 6.0 +/- 0.0
PASS  schools-near-parks: 55/55 features within 400 m = 1.00, claimed 1.0 +/- 0.0
PASS  travessera-grid: median deviation from grid axis = 0.4 deg -> parallel; claim says parallel_to_grid
Numbers in prose: all backed by a verified claim
```

The checks include geometry, not only counting: park area is a dissolve of overlapping polygons, "schools within 400 m of a park" is a spatial query,
and the street-grid claim measures the bearing of a street against the district's grid.

## What came out
Four private Web Maps (generated from the data, features embedded) and one private story draft, opened in the real StoryMaps Builder.

![The transit map in Map Viewer: the six stations drawn over the street network](transit-map.jpg)

The map shows exactly the six stations that the text and the claim `stations` count. Getting there took two real-service findings:

1. The first version drew every transit feature (21) while the text said 6 stations. The text was right and the picture wrong, and no check could see it.
   Fix: a map can carry the same filter as its claim (`"filters": {"transit": "railway == 'station'"}`), and a gate fails when a filter is invalid or selects nothing.
2. With the filter in place the map drew **nothing**: OpenStreetMap ids exceed 32 bits, and ArcGIS silently skips features whose value does not fit the field type.
   Fix: such columns are written as doubles. Full account in the [validation log](../validation-log.md).

## Why this is a useful example
- The claims, the maps and the prose come from one source of truth, so a reader can click from a sentence to the data that backs it.
- The failures it exposed are the kind a mock cannot produce: a real service accepting wrong output without an error.
- It runs from a place name with no local data, and for a place with data of your own you pass it with `--data`.

## Reproduce
```
storymap-gen run examples/gracia_barcelona.json --manifest fixtures/gracia/manifest.json
```
Publishing needs your own ArcGIS organisation and `storymap-gen login`; it only ever creates new private items.
