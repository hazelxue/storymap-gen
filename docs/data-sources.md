# Data sources

A story is only as good as its data. storymap-gen takes data from three places, in any combination, and records where every layer came from
in a `manifest.json`, so the story's reference list is generated from real provenance.

## 1. Your own files
Any file GeoPandas can read: GeoJSON, GeoPackage (`.gpkg`), shapefile (`.shp`). Pass them as `--data name=path`. Files are size-capped (200 MB, `STORYMAP_MAX_DATA_MB`),
must have a coordinate system (GeoJSON is taken as WGS84, and coordinates outside the longitude/latitude range are refused), and corrupt files give a clear error.
Local data wins where it overlaps with a downloaded layer: just give your layer the name you want to use in claims.

## 2. OpenStreetMap, for any place
`storymap-gen region "<place>"` finds the place and its boundary with Nominatim; `storymap-gen fetch --layers ...` downloads themes with the Overpass API
and **cuts them to the boundary**.

| theme | what | geometry | notes |
|---|---|---|---|
| `streets` | drivable and pedestrian streets | lines | `name`, `highway`, `oneway`, `lanes`, `maxspeed`, `surface`, `bridge`, `tunnel` |
| `bike` | cycleways and streets with bike lanes | lines | |
| `parks` | parks, gardens, playgrounds, nature reserves, recreation grounds | polygons | relations (large parks) are assembled into polygons |
| `transit` | rail, subway and tram stations and entrances | points | filter with `railway == 'station'` for stations only |
| `bus_stops` | bus stops | points | |
| `schools` | schools, kindergartens, colleges, universities | points | areas reduced to a point |
| `health` | hospitals and clinics | points | |
| `buildings` | building footprints | polygons | small areas only (6 km2) |
| `landuse` | land-use polygons | polygons | |
| `water` | water bodies and waterways | polygons, lines | |

How the downloads behave:
- **Clipped to the boundary by default** (`--clip bbox` or `none` to change it). Without clipping, a large feature that merely touches the area is counted whole; in our first
  live test a nature reserve made a district's park area 160 times too large.
- **Never partial.** Overpass reports timeouts in a `remark` next to whatever it collected; such results are discarded, other servers are tried, and the command backs off and retries before giving up.
- **Polite.** Identifying User-Agent, one geocoding request per second, results cached for a day (`--no-cache` to refresh), and a size cap per download.
- **Areas up to 150 km2** (`STORYMAP_MAX_AREA_KM2`); beyond that choose a district.

Caveats about the data itself: OpenStreetMap is volunteer-mapped and coverage varies between places and themes; a layer with few features may mean sparse mapping, not an empty place.
There are no demographics. Areas are computed with overlaps merged (a garden inside a park is not counted twice). Features are those intersecting the boundary and cut to it.

Data is (c) OpenStreetMap contributors, ODbL 1.0. The manifest and the story's reference list carry that attribution; keep it if you share the data.

## 3. Public ArcGIS layers and GeoJSON links
```bash
storymap-gen fetch --region region.json --arcgis zoning=https://services.example.com/arcgis/rest/services/Zoning/FeatureServer/0
storymap-gen fetch --region region.json --geojson trees=https://data.example.org/trees.geojson
```
The layer URL must be a single layer (`.../FeatureServer/<n>` or `.../MapServer/<n>`) over https. Features are requested for the region's bounding box in pages, with a cap on the total.
Check the provider's terms; the manifest records the source and date but cannot know the licence. These options exist on the command line only; the MCP server does not download from arbitrary URLs.

## What cannot be fetched (yet)
Census and demographic data, traffic counts, land values, imagery. Supply them as local files, or add a connector (see CONTRIBUTING.md). Without such data, do not make claims about people.
