"""storymap-gen command line.

  storymap-gen run spec.json --data layer=file.geojson --out story.json   verify, compile, quality-gate
  storymap-gen facts --data layer=file.geojson --streets "BROADWAY" ...    measure before you write
  storymap-gen preview spec.json                                           3-layout wireframes
  storymap-gen schema                                                      JSON Schema of the spec
  storymap-gen publish story.json --title T --username U                   NEW private draft (needs ARCGIS_TOKEN)

Exit code 0 = success, 1 = spec invalid / quality gate failed, 2 = usage problem.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from .api import build, load_store
from .preview import render_preview
from .spec import StorySpec

TEMPLATES = ["docked-sidecar", "floating-sidecar", "longform"]


def _parse_data(pairs: list[str]) -> dict[str, str]:
    out = {}
    for kv in pairs:
        if "=" not in kv:
            raise ValueError(f"--data expects LAYER=PATH, got '{kv}'")
        name, path = kv.split("=", 1)
        out[name] = path
    return out


def _load_spec(path: str, template: str | None) -> StorySpec:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if template:
        data["template"] = template
    return StorySpec.model_validate(data)


def _print_invalid(e: ValidationError) -> None:
    print("The spec is not valid:", file=sys.stderr)
    for err in e.errors()[:12]:
        print(f"  - {'.'.join(str(x) for x in err['loc'])}: {err['msg']}", file=sys.stderr)


def _cmd_run(args: argparse.Namespace) -> int:
    spec = _load_spec(args.spec, args.template)
    store = load_store(_parse_data(args.data))
    result = build(spec, store)
    report = result.report
    if args.json:
        print(json.dumps(report, indent=2))
    else:
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
        cit = report["citation"]
        if cit["uncited_sources"] or cit["out_of_range_markers"]:
            print(f"Citations: uncited sources {cit['uncited_sources']}, bad markers {cit['out_of_range_markers']}")
        else:
            print(f"Citations: ok (sections with sources: {cit['sections_with_cites']})")
        for g in report["graph_issues"]:
            print(f"  GRAPH {g}")
    if result.story is None:
        print("Quality gate FAILED; nothing written.", file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(result.story, ensure_ascii=False), encoding="utf-8")
    print(f"Quality gate passed. Wrote {args.out} ({len(result.story['nodes'])} nodes, layout {spec.template}).")
    return 0


def _cmd_facts(args: argparse.Namespace) -> int:
    from .verify import orientation_facts
    print(orientation_facts(load_store(_parse_data(args.data)), args.streets, layer=args.layer, name_field=args.name_field))
    return 0


def _cmd_publish(args: argparse.Namespace) -> int:
    from .evaluate import check_graph
    from .publish import publish_draft
    token = os.environ.get("ARCGIS_TOKEN")
    if not token:
        print("Set ARCGIS_TOKEN in the environment (never pass a token as an argument). See README: Publish.", file=sys.stderr)
        return 2
    story = json.loads(Path(args.story).read_text(encoding="utf-8"))
    issues = check_graph(story)
    if issues:
        print("Refusing to publish an invalid story graph:\n" + "\n".join(issues), file=sys.stderr)
        return 1
    item_id = publish_draft(story, args.title, args.username, token, sharing=args.portal)
    print(f"Created private draft: https://storymaps.arcgis.com/stories/{item_id}/edit")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="storymap-gen", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="validate, verify claims, compile, quality-gate")
    run.add_argument("spec")
    run.add_argument("--data", nargs="*", default=[], metavar="LAYER=PATH", help="GIS files the claims are checked against")
    run.add_argument("--template", choices=TEMPLATES, help="override the layout in the spec")
    run.add_argument("--out", default="story.json")
    run.add_argument("--json", action="store_true", help="print the full machine-readable report")

    fc = sub.add_parser("facts", help="measure street-vs-grid facts to read BEFORE writing claims")
    fc.add_argument("--data", nargs="+", required=True, metavar="LAYER=PATH")
    fc.add_argument("--streets", nargs="+", required=True)
    fc.add_argument("--layer", default="streets_centerline")
    fc.add_argument("--name-field", default="full_stree")

    pv = sub.add_parser("preview", help="write a 3-layout wireframe preview")
    pv.add_argument("spec")
    pv.add_argument("--out", default="preview.html")

    sub.add_parser("schema", help="print the JSON Schema of the story spec")

    pub = sub.add_parser("publish", help="create a NEW private draft from a compiled story.json")
    pub.add_argument("story")
    pub.add_argument("--title", required=True)
    pub.add_argument("--username", required=True)
    pub.add_argument("--portal", default="https://www.arcgis.com/sharing/rest")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "schema":
            print(json.dumps(StorySpec.model_json_schema(), indent=2))
            return 0
        if args.cmd == "facts":
            return _cmd_facts(args)
        if args.cmd == "publish":
            return _cmd_publish(args)
        if args.cmd == "preview":
            spec = _load_spec(args.spec, None)
            Path(args.out).write_text(render_preview(spec), encoding="utf-8")
            print(f"Wrote {args.out}")
            return 0
        return _cmd_run(args)
    except ValidationError as e:
        _print_invalid(e)
        return 1
    except (ValueError, FileNotFoundError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
