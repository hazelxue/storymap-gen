"""CLI:  python -m storymap_gen run spec.json --data streets_centerline=streets.geojson --out story.json
       python -m storymap_gen preview spec.json --out preview.html
Exit code 1 if validation or any quality gate fails.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .compiler import compile_story
from .evaluate import evaluate
from .preview import render_preview
from .spec import StorySpec
from .verify import DataStore, verify_claims


def _load_spec(path: str, template: str | None) -> StorySpec:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if template:
        data["template"] = template
    return StorySpec.model_validate(data)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="storymap_gen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="validate -> verify claims -> compile -> evaluate")
    run.add_argument("spec")
    run.add_argument("--data", nargs="*", default=[], metavar="LAYER=PATH")
    run.add_argument("--template", choices=["docked-sidecar", "floating-sidecar", "longform"])
    run.add_argument("--out", default="story.json")
    pub = sub.add_parser("publish", help="create a NEW private draft from a compiled story.json")
    pub.add_argument("story")
    pub.add_argument("--title", required=True)
    pub.add_argument("--username", required=True)
    pub.add_argument("--portal", default="https://www.arcgis.com/sharing/rest")
    fc = sub.add_parser("facts", help="print measured street-vs-grid facts to give the writer before drafting")
    fc.add_argument("--data", nargs="+", required=True, metavar="LAYER=PATH")
    fc.add_argument("--streets", nargs="+", required=True)
    pv = sub.add_parser("preview", help="write a 3-layout wireframe preview")
    pv.add_argument("spec")
    pv.add_argument("--out", default="preview.html")
    args = ap.parse_args(argv)

    if args.cmd == "facts":
        from .verify import orientation_facts
        print(orientation_facts(DataStore.from_files(dict(kv.split("=", 1) for kv in args.data)), args.streets))
        return 0

    if args.cmd == "publish":
        import os
        from .evaluate import check_graph
        from .publish import publish_draft
        token = os.environ.get("ARCGIS_TOKEN")
        if not token:
            print("set ARCGIS_TOKEN in the environment (never pass a token as an argument)", file=sys.stderr)
            return 2
        story = json.loads(Path(args.story).read_text(encoding="utf-8"))
        issues = check_graph(story)
        if issues:
            print("refusing to publish an invalid story graph:\n" + "\n".join(issues), file=sys.stderr)
            return 1
        item_id = publish_draft(story, args.title, args.username, token, sharing=args.portal)
        print(f"created private draft: https://storymaps.arcgis.com/stories/{item_id}/edit")
        return 0

    try:
        spec = _load_spec(args.spec, getattr(args, "template", None))
    except ValidationError as e:
        print(f"SPEC INVALID:\n{e}", file=sys.stderr)
        return 1

    if args.cmd == "preview":
        Path(args.out).write_text(render_preview(spec), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0

    store = DataStore.from_files(dict(kv.split("=", 1) for kv in args.data))
    results = verify_claims(spec, store)
    for r in results:
        print(f"[{r.status}] {r.id}: {r.detail}")
    story = compile_story(spec)
    report = evaluate(spec, story, results)
    print(json.dumps(report, indent=2))
    if report["passed"]:
        Path(args.out).write_text(json.dumps(story, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.out} ({len(story['nodes'])} nodes)")
        return 0
    print("quality gate FAILED; story not written", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
