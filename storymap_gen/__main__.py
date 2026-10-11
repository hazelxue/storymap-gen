"""storymap-gen command line.

From a place and a topic to a StoryMap:
  storymap-gen region "Park Slope, Brooklyn" --out region.json       find the area (or give --bbox)
  storymap-gen fetch --region region.json --layers streets parks transit --out data/    download public data
  storymap-gen describe --manifest data/manifest.json                what is in the data (read this before writing claims)
  storymap-gen init --region region.json --topic "..." --manifest data/manifest.json --out spec.json    skeleton spec
  storymap-gen facts --data streets=data/streets.geojson --streets "BROADWAY"    measure before you write
  storymap-gen run spec.json --manifest data/manifest.json           verify, compile, quality-gate
  storymap-gen login --client-id ID                                  sign in (in YOUR terminal; hidden prompt)
  storymap-gen publish-spec spec.json --manifest data/manifest.json --title "..."   private maps + story draft

Other: preview, schema, publish (a compiled story.json), logout.
Exit code 0 = success, 1 = spec invalid / quality gate failed / a download failed, 2 = usage problem.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import geopandas as gpd
from pydantic import ValidationError

from . import auth
from .api import BuildResult, build, load_store, publish_spec
from .preview import render_preview
from .scaffold import FRAMEWORKS, scaffold_spec
from .spec import StorySpec

TEMPLATES = ["docked-sidecar", "floating-sidecar", "longform"]
LAYER_NAME = re.compile(r"^[A-Za-z][\w-]{0,40}$")
MAX_TEXT_FILE = 200_000


def _parse_pairs(pairs: list[str] | None, what: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for kv in pairs or []:
        if "=" not in kv:
            raise ValueError(f"{what} expects NAME=VALUE, got '{kv}'")
        name, value = kv.split("=", 1)
        if not LAYER_NAME.match(name):
            raise ValueError(f"'{name}' is not a valid layer name (letters, digits, - and _, starting with a letter)")
        out[name] = value
    return out


def _collect_data(args: argparse.Namespace) -> dict[str, str]:
    """Layers from a manifest and/or explicit --data pairs (explicit pairs win)."""
    from .sources.manifest import read_manifest
    data: dict[str, str] = {}
    if getattr(args, "manifest", None):
        mpath = Path(args.manifest)
        data.update(read_manifest(mpath).data_paths(mpath.parent))
    data.update(_parse_pairs(getattr(args, "data", None), "--data"))
    return data


def _read_text(path: str) -> str:
    p = Path(path)
    if p.stat().st_size > MAX_TEXT_FILE:
        raise ValueError(f"{p.name} is larger than {MAX_TEXT_FILE // 1000} KB")
    return p.read_text(encoding="utf-8")


def _load_spec(path: str, template: str | None) -> StorySpec:
    data = json.loads(_read_text(path))
    if template:
        data["template"] = template
    return StorySpec.model_validate(data)


def _print_invalid(e: ValidationError) -> None:
    print("The spec is not valid:", file=sys.stderr)
    for err in e.errors()[:12]:
        print(f"  - {'.'.join(str(x) for x in err['loc'])}: {err['msg']}", file=sys.stderr)


def _print_report(result: BuildResult) -> None:
    report = result.report
    print(f"Claims ({len(result.results)})")
    for r in result.results:
        print(f"  {r.status:5s} {r.id}: {r.detail}")
        if r.hint:
            print(f"        fix: {r.hint}")
    unb = report["unbacked_numbers"]
    print("Numbers in prose: " + ("all backed by a verified claim" if not unb else f"{len(unb)} NOT backed"))
    for u in unb:
        print(f"  UNBACKED {u}")
    if unb:
        print("        fix: attach a claim that states the number (claim_ids), or list it in allow_numbers")
    for ph in report.get("placeholders", []):
        print(f"  PLACEHOLDER {ph}")
    if report.get("placeholders"):
        print("        fix: replace the placeholder text with real, data-backed text")
    for mp in report.get("map_problems", []):
        print(f"  MAP {mp}")
    if report.get("map_problems"):
        print("        fix: name only loaded layers, and use filters that select at least one feature")
    cit = report["citation"]
    if cit["uncited_sources"] or cit["out_of_range_markers"]:
        print(f"Citations: uncited sources {cit['uncited_sources']}, bad markers {cit['out_of_range_markers']}")
    else:
        print(f"Citations: ok (sections with sources: {cit['sections_with_cites']})")
    for g in report["graph_issues"]:
        print(f"  GRAPH {g}")
    d = report.get("depth")
    if d:
        print(f"Depth ({d['profile']}): {d['words']} words, {d['sections']} sections, {d['subsections']} subsections, {d['maps']} maps "
              f"({d['swipes']} swipes), {d['figures']} key-figure rows, {d['images']} pictures, {d['claims']} claims")
        for w in d["warnings"]:
            print(f"  THIN {w}")
        if d["warnings"]:
            print("        these do not fail the gate; fix them before publishing (docs/writing-guide.md)")


# ---- commands ------------------------------------------------------------------------------------------------
def _cmd_run(args: argparse.Namespace) -> int:
    spec = _load_spec(args.spec, args.template)
    result = build(spec, load_store(_collect_data(args)))
    if args.json:
        print(json.dumps(result.report, indent=2))
    else:
        _print_report(result)
    if result.story is None:
        print("Quality gate FAILED; nothing written.", file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(result.story, ensure_ascii=False), encoding="utf-8")
    print(f"Quality gate passed. Wrote {args.out} ({len(result.story['nodes'])} nodes, layout {spec.template}).")
    return 0


def _cmd_facts(args: argparse.Namespace) -> int:
    from .verify import orientation_facts
    print(orientation_facts(load_store(_collect_data(args)), args.streets, layer=args.layer, name_field=args.name_field))
    return 0


def _cmd_region(args: argparse.Namespace) -> int:
    from .sources.geocode import geocode_many, region_from_bbox
    if args.bbox:
        west, south, east, north = args.bbox
        region = region_from_bbox(west, south, east, north, name=args.name or " + ".join(args.place))
    else:
        region = geocode_many(args.place, args.name)
    Path(args.out).write_text(json.dumps(region.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    w, s, e, n = region.bbox
    print(f"{region.name}: {region.display_name or '(bounding box given)'}")
    print(f"  bbox west={w:.5f} south={s:.5f} east={e:.5f} north={n:.5f}  (box ~{region.area_km2:.1f} km2)")
    print(f"  boundary: {'yes, ~' + format(region.study_area_km2, '.1f') + ' km2' if region.boundary else 'none (the box is used)'}")
    print(f"Wrote {args.out}")
    return 0


def _write_layer(gdf: gpd.GeoDataFrame, path: Path) -> None:
    if gdf.empty:
        path.write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")
    else:
        gdf.to_file(path, driver="GeoJSON")


def _cmd_fetch(args: argparse.Namespace) -> int:
    from . import net
    from .sources import arcgis, overpass
    from .sources.geocode import Region
    from .sources.manifest import LayerRecord, new_manifest, write_manifest
    region = Region.from_dict(json.loads(_read_text(args.region)))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = new_manifest(region)
    failures: list[str] = []

    def record(name: str, gdf: gpd.GeoDataFrame, source: str, attribution: str, licence: str, origin: str) -> None:
        _write_layer(gdf, out / f"{name}.geojson")
        kinds = ", ".join(sorted(set(map(str, gdf.geometry.geom_type)))) if len(gdf) else "none"
        manifest.layers.append(LayerRecord(name, f"{name}.geojson", int(len(gdf)), kinds, source, attribution, licence, origin,
                                           [str(c) for c in gdf.columns if c != gdf.geometry.name]))
        print(f"  {name}: {len(gdf)} features")

    print(f"Fetching for {region.name} (~{region.area_km2:.1f} km2)")
    boundary = region.boundary_frame()
    if boundary is not None:  # the study area itself is a layer: density and coverage claims measure against it
        record("boundary", boundary, "osm", region.attribution, overpass.LICENCE, "place boundary from Nominatim")
    for name in args.layers or []:
        try:
            if not LAYER_NAME.match(name):
                raise ValueError(f"'{name}' is not a valid layer name")
            record(name, overpass.fetch_layer(name, region, use_cache=not args.no_cache, clip=args.clip), "osm", overpass.ATTRIBUTION,
                   overpass.LICENCE, f"Overpass API, theme '{name}', clipped to {args.clip}")
        except (net.NetworkError, ValueError) as e:
            failures.append(f"{name}: {e}")
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for name, url in _parse_pairs(args.arcgis, "--arcgis").items():
        try:
            host = urlsplit(url).hostname or "arcgis"
            record(name, arcgis.fetch_service_layer(url, region, where=args.where), f"arcgis-{host}",
                   f"ArcGIS feature service at {host}, layer '{name}', retrieved {when}; check the provider's terms of use",
                   "see provider", url)
        except (net.NetworkError, ValueError) as e:
            failures.append(f"{name}: {e}")
    for name, url in _parse_pairs(args.geojson, "--geojson").items():
        try:
            host = urlsplit(url).hostname or "web"
            record(name, arcgis.fetch_geojson_url(url), f"web-{host}", f"GeoJSON from {host}, layer '{name}', retrieved {when}; "
                   "check the provider's terms of use", "see provider", url)
        except (net.NetworkError, ValueError) as e:
            failures.append(f"{name}: {e}")
    if manifest.layers:
        print(f"Wrote {write_manifest(manifest, out)}")
    for f in failures:
        print(f"  FAILED {f}", file=sys.stderr)
    return 1 if failures or not manifest.layers else 0


def _cmd_describe(args: argparse.Namespace) -> int:
    from .describe import profile_store, render_markdown
    profiles = profile_store(load_store(_collect_data(args)))
    print(json.dumps(profiles, indent=2) if args.json else render_markdown(profiles))
    return 0


def _cmd_explore(args: argparse.Namespace) -> int:
    from .explore import build_explorer_html
    store = load_store(_collect_data(args))
    html = build_explorer_html(store, args.title)
    out = Path(args.out)
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB, {len(store.layers)} layers). Open it in a browser; it works offline.")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def _add_to_manifest(manifest_path: Path, name: str, gdf: gpd.GeoDataFrame, source: str, attribution: str, licence: str, origin: str) -> Path:
    """Write ``gdf`` next to the manifest and record it there (replacing an earlier layer of the same name)."""
    from .sources.manifest import LayerRecord, read_manifest, write_manifest
    manifest = read_manifest(manifest_path)
    target = manifest_path.parent / f"{name}.geojson"
    _write_layer(gdf, target)
    kinds = ", ".join(sorted(set(map(str, gdf.geometry.geom_type)))) if len(gdf) else "none"
    rec = LayerRecord(name, f"{name}.geojson", int(len(gdf)), kinds, source, attribution, licence, origin,
                      [str(c) for c in gdf.columns if c != gdf.geometry.name])
    manifest.layers = [r for r in manifest.layers if r.name != name] + [rec]
    write_manifest(manifest, manifest_path.parent)
    return target


def _cmd_subareas(args: argparse.Namespace) -> int:
    from .aggregate import subareas_frame
    from .sources.geocode import Region
    region = Region.from_dict(json.loads(_read_text(args.region)), check_area=False)
    gdf = subareas_frame(region)
    path = _add_to_manifest(Path(args.manifest), args.name, gdf, "osm", region.attribution, "ODbL 1.0", "place boundaries from Nominatim")
    print(f"Wrote {path} ({len(gdf)} sub-areas: {', '.join(gdf['name'])}) and added it to the manifest.")
    return 0


def _cmd_aggregate(args: argparse.Namespace) -> int:
    from .aggregate import aggregate, fields_added
    manifest_path = Path(args.manifest)
    store = load_store(_collect_data(args))
    where = _parse_pairs(args.where, "--where")
    result = aggregate(store, args.subareas, args.layers, where)
    added = fields_added(store.get(args.subareas), result)
    _add_to_manifest(manifest_path, args.out_name, result, "derived", "Computed by storymap-gen from: " + ", ".join(args.layers) + ".",
                     "as the sources", f"aggregate of {', '.join(args.layers)} over '{args.subareas}'")
    print(f"Wrote layer '{args.out_name}': {len(result)} sub-areas with fields {', '.join(added)}")
    for _, row in result.iterrows():
        shown = ", ".join(f"{c}={row[c]}" for c in added[:4])
        print(f"  {row.get('name', '?')}: {shown}")
    return 0


def _cmd_join(args: argparse.Namespace) -> int:
    from .api import resolve_data_path
    from .join import join_table, read_table
    layer = load_store({"layer": str(resolve_data_path(args.layer))}).get("layer")
    table = read_table(args.table)
    joined, report = join_table(layer, table, args.key, args.table_key or args.key, args.fields, args.zfill, args.force)
    _write_layer(joined, Path(args.out))
    print(f"Joined {report.matched} of {report.polygons} polygons; {report.unmatched_rows} table rows matched no polygon. Wrote {args.out}.")
    if report.unmatched_polygons:
        print(f"  polygons without a match (first {len(report.unmatched_polygons)}): {', '.join(report.unmatched_polygons)}")
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    from .sources.context import image_blocks, place_context
    from .sources.geocode import Region
    query = args.query
    if not query:
        if not args.region:
            print("give a place name, or --region region.json", file=sys.stderr)
            return 2
        query = Region.from_dict(json.loads(_read_text(args.region)), check_area=False).name
    ctx = place_context(query, args.lang, args.images, args.image_query)
    wiki = ctx["wikipedia"]
    if wiki:
        print(f"Wikipedia: {wiki['title']} ({wiki['url']})")
        if wiki.get("extract"):
            print(f"  {wiki['extract'][:600]}")
    for i, img in enumerate(ctx["images"], 1):
        print(f"  [{i}] {img['title']}  {img['width']}x{img['height']}  {img['credit']}")
    for note in ctx["notes"]:
        print(f"  note: {note}")
    if args.out:
        ctx["image_blocks"] = image_blocks(ctx["images"])
        Path(args.out).write_text(json.dumps(ctx, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {args.out}: a source entry, image blocks with credits, and the Wikipedia summary. Check each picture before using it.")
    return 0 if (wiki or ctx["images"]) else 1


def _cmd_findings(args: argparse.Namespace) -> int:
    from .findings import all_claims, compute_findings, render_markdown, to_json
    store = load_store(_collect_data(args))
    found = compute_findings(store, limit=args.top)
    print(json.dumps(to_json(found), indent=2, ensure_ascii=False) if args.json else render_markdown(found, store))
    if args.claims_out:
        claims = all_claims(found)
        Path(args.claims_out).write_text(json.dumps(claims, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {len(claims)} verified claims to {args.claims_out}", file=sys.stderr)
    return 0 if found else 1


def _cmd_init(args: argparse.Namespace) -> int:
    from .sources.geocode import Region
    from .sources.manifest import read_manifest
    region = Region.from_dict(json.loads(_read_text(args.region)), check_area=False)
    manifest = read_manifest(Path(args.manifest)) if args.manifest else None
    outline = _read_text(args.outline) if args.outline else None
    spec = scaffold_spec(region, args.topic, manifest, outline, args.template, args.layers, args.framework)
    StorySpec.model_validate(spec)  # the skeleton itself must be a valid spec
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"{out} exists; pass --force to overwrite it", file=sys.stderr)
        return 2
    out.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out}: {len(spec['sections'])} sections. Replace every TODO (the quality gate refuses unfinished text), "
          "then add claims for the numbers you state.")
    return 0


def _cmd_login(args: argparse.Namespace) -> int:
    profile = auth.load_profile()
    client_id = args.client_id or os.environ.get("STORYMAP_CLIENT_ID", "") or profile.get("client_id", "")
    if not client_id:
        print("A client id is required the first time. Create an OAuth 'Developer credentials' item (user authentication) in your "
              "organisation, then run: storymap-gen login --client-id <Client ID> --portal <your-org>.maps.arcgis.com\n"
              "Step by step: README, 'Signing in and publishing'.", file=sys.stderr)
        return 2
    portal = auth.resolve_portal(args.portal)
    url = auth.authorize_url(client_id, portal, minutes=args.minutes)
    print("1. Open this address in your browser and click Allow (you sign in on the ArcGIS page; this tool never sees your password):\n")
    print(f"   {url}\n")
    if not args.no_browser:
        webbrowser.open(url)
    print("2. After you click Allow, the browser shows a page that fails to load (localhost). That is expected.\n"
          "   Copy the full address from the browser's address bar (it starts with https://localhost:8765/ and contains access_token=).")
    if not sys.stdin.isatty():
        print("This step needs an interactive terminal (the paste is hidden). Run the command yourself in a terminal.", file=sys.stderr)
        return 2
    who, seconds, token = None, None, ""
    for _attempt in range(3):
        pasted = getpass.getpass("3. Paste it here, right now, at this prompt. Nothing is displayed, not even dots: "
                                 "right-click or Ctrl+V, then Enter: ")
        try:
            token, seconds = auth.parse_token(pasted)
            who = auth.verify_token(token, portal)
            break
        except (ValueError, RuntimeError) as e:
            print(f"   That did not work ({e}). Paste the address of the page you reached AFTER clicking Allow.", file=sys.stderr)
    if who is None:
        return 1
    note = "not saved (--no-save)"
    if not args.no_save:
        auth.save_token(token, who["username"], portal, seconds)
        auth.save_profile(client_id, portal)
        note = f"saved for this machine until it expires (about {(seconds or 3300) // 60} minutes); `storymap-gen logout` removes it"
    print(f"Signed in as {who['username']}. Token {note}.")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    st = auth.status(args.portal)
    if st["signed_in"]:
        print(f"Signed in to {st['portal'].removesuffix('/sharing/rest')} as {st['username']} ({st['minutes_left']} minutes left).")
        return 0
    print(f"Not signed in to {st['portal'].removesuffix('/sharing/rest')}. Run: storymap-gen login")
    return 1


def _cmd_logout(_: argparse.Namespace) -> int:
    print("Removed the saved token." if auth.delete_token() else "No saved token.")
    return 0


def _cmd_publish_spec(args: argparse.Namespace) -> int:
    spec = _load_spec(args.spec, args.template)
    store = load_store(_collect_data(args))
    result = build(spec, store)
    _print_report(result)
    if result.story is None:
        print("Quality gate FAILED; nothing was published.", file=sys.stderr)
        return 1
    portal = auth.resolve_portal(args.portal)
    token, username = auth.resolve_credentials(portal, args.username)
    from urllib.parse import urlsplit

    from .images import spec_image_urls
    urls = spec_image_urls(spec)
    if urls and args.download_images:
        hosts = ", ".join(sorted({urlsplit(u).hostname or "?" for u in urls}))
        print(f"Downloading {len(urls)} picture(s) from {hosts} and uploading them to the draft.")
    published = publish_spec(spec, store, title=args.title or spec.title, username=username, token=token, portal=portal,
                             summary=spec.subtitle, download_images=args.download_images)
    for key, item_id in published.map_ids.items():
        print(f"Created private Web Map '{key}': {item_id}")
    print(f"Created private draft: https://storymaps.arcgis.com/stories/{published.story_id}/edit")
    if published.pictures:
        print(f"Uploaded {published.pictures} picture(s) to the draft.")
    for note in published.left_out:
        print(f"  PICTURE {note}", file=sys.stderr)
    print("Review it in the Builder. The maps are private; share them (or publish the story) yourself when you are ready.")
    return 0


def _cmd_publish(args: argparse.Namespace) -> int:
    from .evaluate import check_graph
    from .publish import publish_draft
    story = json.loads(Path(args.story).read_text(encoding="utf-8"))
    issues = check_graph(story)
    if issues:
        print("Refusing to publish an invalid story graph:\n" + "\n".join(issues), file=sys.stderr)
        return 1
    portal = auth.resolve_portal(args.portal)
    token, username = auth.resolve_credentials(portal, args.username)
    item_id = publish_draft(story, args.title, username, token, sharing=portal)
    print(f"Created private draft: https://storymaps.arcgis.com/stories/{item_id}/edit")
    return 0


def _cmd_preview(args: argparse.Namespace) -> int:
    Path(args.out).write_text(render_preview(_load_spec(args.spec, None)), encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


def _cmd_schema(_: argparse.Namespace) -> int:
    print(json.dumps(StorySpec.model_json_schema(), indent=2))
    return 0


# ---- parser ------------------------------------------------------------------------------------------------
def _data_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--manifest", help="manifest.json written by `fetch`; its layers are loaded by name")
    p.add_argument("--data", nargs="*", default=[], metavar="LAYER=PATH", help="more GIS files (GeoJSON, GeoPackage, shapefile)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="storymap-gen", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--debug", action="store_true", help="show a traceback on unexpected errors")
    sub = ap.add_subparsers(dest="cmd", required=True)

    rg = sub.add_parser("region", help="find the bounding box of a place (OpenStreetMap Nominatim)")
    rg.add_argument("place", nargs="+", help="one place, or several (the union of their boundaries is the study area)")
    rg.add_argument("--name", help="a name for the combined area (default: the places joined by commas)")
    rg.add_argument("--bbox", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"), help="use this box instead of geocoding")
    rg.add_argument("--out", default="region.json")

    fe = sub.add_parser("fetch", help="download public data for a region")
    fe.add_argument("--region", required=True)
    fe.add_argument("--layers", nargs="*", default=[], metavar="THEME", help="OpenStreetMap themes: streets bike parks transit bus_stops "
                    "schools health buildings landuse water")
    fe.add_argument("--arcgis", nargs="*", default=[], metavar="NAME=URL", help="a public ArcGIS layer URL (.../FeatureServer/0)")
    fe.add_argument("--geojson", nargs="*", default=[], metavar="NAME=URL", help="a public https GeoJSON file")
    fe.add_argument("--where", default="1=1", help="attribute filter sent to ArcGIS services")
    fe.add_argument("--clip", choices=["boundary", "bbox", "none"], default="boundary",
                    help="cut OpenStreetMap features to the place boundary (default), its bounding box, or not at all")
    fe.add_argument("--no-cache", action="store_true")
    fe.add_argument("--out", default="data")

    ds = sub.add_parser("describe", help="profile the data layers before writing claims")
    _data_args(ds)
    ds.add_argument("--json", action="store_true")

    cx = sub.add_parser("context", help="Wikipedia summary and reusable Wikimedia Commons pictures (with credits) for a place")
    cx.add_argument("query", nargs="?", help="a place name (default: the name in --region)")
    cx.add_argument("--region")
    cx.add_argument("--lang", default="en", help="Wikipedia language code (default en)")
    cx.add_argument("--images", type=int, default=6, help="how many pictures to list (default 6)")
    cx.add_argument("--image-query", help="search Commons for this instead (a street, a square, a landmark: more specific than the place)")
    cx.add_argument("--out", help="write the result (with a source entry and image blocks) to this JSON file")
    sa = sub.add_parser("subareas", help="write the places of a combined region as a polygon layer, to compare them")
    sa.add_argument("--region", required=True)
    sa.add_argument("--manifest", required=True)
    sa.add_argument("--name", default="subareas")
    ag = sub.add_parser("aggregate", help="add counts, lengths and densities of other layers to each sub-area polygon (a layer to colour a map by)")
    _data_args(ag)
    ag.add_argument("--subareas", default="subareas", help="the polygon layer to measure per polygon")
    ag.add_argument("--layers", nargs="+", required=True)
    ag.add_argument("--where", nargs="*", help="filter a layer first: layer=expression, e.g. transit=\"railway == 'station'\"")
    ag.add_argument("--out-name", default="subareas_stats")
    jn = sub.add_parser("join", help="join a CSV of numbers to a polygon layer by a key column")
    jn.add_argument("--layer", required=True, help="polygon file (GeoJSON, GeoPackage, shapefile)")
    jn.add_argument("--table", required=True, help="a .csv or .tsv file")
    jn.add_argument("--key", required=True, help="key column in the layer")
    jn.add_argument("--table-key", help="key column in the table (default: the same name)")
    jn.add_argument("--fields", nargs="+", required=True, help="table columns to bring over as numbers")
    jn.add_argument("--zfill", type=int, default=0, help="pad keys with leading zeros to this width (census ids)")
    jn.add_argument("--force", action="store_true", help="accept a join that matches fewer than half of the polygons")
    jn.add_argument("--out", required=True)
    fi = sub.add_parser("findings", help="measure a standard battery (reach, gaps, skews, concentration, relations) and list the notable results as verified claims")
    _data_args(fi)
    fi.add_argument("--top", type=int, default=40, help="how many findings to keep (default 40)")
    fi.add_argument("--json", action="store_true")
    fi.add_argument("--claims-out", help="write the claims of every finding to this JSON file, to paste into a spec")
    ex = sub.add_parser("explore", help="write a self-contained HTML map of the data, to look at before writing")
    _data_args(ex)
    ex.add_argument("--title", default="Data explorer")
    ex.add_argument("--out", default="explore.html")
    ex.add_argument("--open", action="store_true", help="open it in the default browser")
    ini = sub.add_parser("init", help="write a skeleton spec from a region, a topic and an optional outline")
    ini.add_argument("--region", required=True)
    ini.add_argument("--topic", required=True)
    ini.add_argument("--manifest")
    ini.add_argument("--outline", help="a markdown file; unindented lines become sections, indented lines subsections")
    ini.add_argument("--framework", choices=FRAMEWORKS, default="auto",
                     help="shape of the story: outline (from --outline), systems, essay or brief; auto = outline if given, else essay")
    ini.add_argument("--layers", nargs="*", help="layers to draw (default: all in the manifest)")
    ini.add_argument("--template", choices=TEMPLATES, default="longform")
    ini.add_argument("--out", default="spec.json")
    ini.add_argument("--force", action="store_true")

    run = sub.add_parser("run", help="validate, verify claims, compile, quality-gate")
    run.add_argument("spec")
    _data_args(run)
    run.add_argument("--template", choices=TEMPLATES, help="override the layout in the spec")
    run.add_argument("--out", default="story.json")
    run.add_argument("--json", action="store_true", help="print the full machine-readable report")

    fc = sub.add_parser("facts", help="measure street-vs-grid facts to read BEFORE writing claims")
    _data_args(fc)
    fc.add_argument("--streets", nargs="+", required=True)
    fc.add_argument("--layer", default="streets_centerline")
    fc.add_argument("--name-field", default="full_stree")

    pv = sub.add_parser("preview", help="write a 3-layout wireframe preview")
    pv.add_argument("spec")
    pv.add_argument("--out", default="preview.html")

    sub.add_parser("schema", help="print the JSON Schema of the story spec")

    lg = sub.add_parser("login", help="sign in to ArcGIS (interactive; run it in your own terminal)")
    lg.add_argument("--client-id", help="Client ID of your OAuth credentials item (or set STORYMAP_CLIENT_ID)")
    lg.add_argument("--portal", default=None, help="your organisation, e.g. myorg.maps.arcgis.com (remembered after the first login)")
    lg.add_argument("--minutes", type=int, default=60)
    lg.add_argument("--no-save", action="store_true", help="check the token but do not keep it")
    lg.add_argument("--no-browser", action="store_true")

    st = sub.add_parser("status", help="show whether you are signed in (never prints the token)")
    st.add_argument("--portal", default=None)
    sub.add_parser("logout", help="delete the saved token")

    ps = sub.add_parser("publish-spec", help="check a spec, then create private Web Maps and a private story draft")
    ps.add_argument("spec")
    _data_args(ps)
    ps.add_argument("--template", choices=TEMPLATES)
    ps.add_argument("--download-images", action="store_true",
                    help="download the pictures named in the spec (public https only) and upload them to the draft; without it they are left out")
    ps.add_argument("--title")
    ps.add_argument("--username")
    ps.add_argument("--portal", default=None, help="your organisation (default: the one you signed in to)")

    pub = sub.add_parser("publish", help="create a NEW private draft from a compiled story.json")
    pub.add_argument("story")
    pub.add_argument("--title", required=True)
    pub.add_argument("--username")
    pub.add_argument("--portal", default=None, help="your organisation (default: the one you signed in to)")
    return ap


COMMANDS = {"region": _cmd_region, "fetch": _cmd_fetch, "describe": _cmd_describe, "explore": _cmd_explore, "findings": _cmd_findings, "context": _cmd_context, "subareas": _cmd_subareas, "aggregate": _cmd_aggregate, "join": _cmd_join, "init": _cmd_init, "run": _cmd_run,
            "facts": _cmd_facts, "preview": _cmd_preview, "schema": _cmd_schema, "login": _cmd_login, "status": _cmd_status, "logout": _cmd_logout,
            "publish-spec": _cmd_publish_spec, "publish": _cmd_publish}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args)
    except ValidationError as e:
        _print_invalid(e)
        return 1
    except (ValueError, FileNotFoundError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001 - a CLI should explain, not dump a traceback
        if args.debug:
            raise
        print(f"Unexpected error ({type(e).__name__}): {str(e)[:300]}. Re-run with --debug for a traceback.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
