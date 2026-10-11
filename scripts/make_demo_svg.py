"""Build docs/demo/terminal-demo.svg: an animated terminal replay made from the REAL output of the commands below.

Nothing is typed by hand: each scene runs the command (offline, on the fixtures in this repository), keeps its first lines of output,
and writes them into an animated SVG that plays in a README. Run it again after a change to refresh the replay:

    python scripts/make_demo_svg.py
"""
from __future__ import annotations

import html
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "demo" / "terminal-demo.svg"
WIDTH, HEIGHT, LINE_H, COLS = 940, 540, 18, 108
PY = [sys.executable, "-m", "storymap_gen"]

# (what is shown after the prompt, the arguments really run, how many output lines to keep, lines to skip)
SCENES = [
    ("storymap-gen describe --manifest data/manifest.json | grep -E '^## |named|total'", ["describe", "--manifest", "fixtures/gracia/manifest.json"], 17, 0),
    ("storymap-gen findings --manifest data/manifest.json --top 6", ["findings", "--manifest", "fixtures/gracia/manifest.json", "--top", "6"], 14, 3),
    ("storymap-gen run spec.json --data streets_centerline=streets.geojson", ["run", "examples/jackson_heights_broken.json", "--data",
                                                                              "streets_centerline=fixtures/streets_centerline.geojson"], 13, 0),
    ("storymap-gen run spec.json --manifest data/manifest.json", ["run", "examples/gracia_barcelona_full.json", "--manifest",
                                                                  "fixtures/gracia/manifest.json", "--out", "story.json"], 0, 0),
]
TYPE_SECONDS, LINE_SECONDS, HOLD_SECONDS = 1.6, 0.16, 3.5


def run(args: list[str]) -> list[str]:
    done = subprocess.run([*PY, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    (ROOT / "story.json").unlink(missing_ok=True)
    return (done.stdout + done.stderr).splitlines()


def shorten(line: str) -> str:
    return line if len(line) <= COLS else line[: COLS - 1] + "…"


def pick_gate_lines(lines: list[str]) -> list[str]:
    """For the passing gate: a few PASS lines, then the summary lines (skip the rest of the claims)."""
    passes = [ln for ln in lines if ln.startswith("  PASS")][:5]
    rest = [ln for ln in lines if not ln.startswith("  PASS") and not ln.startswith("Claims")]
    return [f"Claims ({sum(ln.startswith('  PASS') for ln in lines)}): all checked against the data", *passes, "  ...", *rest]


def build() -> str:
    timeline: list[tuple[float, float, str, str]] = []   # (start, end, kind, text)
    t = 0.5
    for shown, args, keep, skip in SCENES:
        out = run(args)
        if "grep" in shown:                       # the same filter the displayed command applies
            out = [ln for ln in out if re.search(r"^## |named|total", ln)]
        body = pick_gate_lines(out) if keep == 0 else out[skip: skip + keep]
        scene_start = t
        timeline.append((t, -1.0, "cmd", shown))
        t += TYPE_SECONDS
        scene_lines = []
        for line in body[: HEIGHT // LINE_H - 3]:
            timeline.append((t, -1.0, "out", shorten(line)))
            scene_lines.append(len(timeline) - 1)
            t += LINE_SECONDS
        t += HOLD_SECONDS
        for i in range(len(timeline)):
            s, e, k, x = timeline[i]
            if e < 0 and s >= scene_start:
                timeline[i] = (s, t, k, x)
    total = t + 0.8
    # lay the lines out scene by scene from the top
    rows, y_by_scene, last_end = [], 0, None
    for s, e, kind, text in timeline:
        if last_end is not None and e != last_end:
            y_by_scene = 0
        rows.append((y_by_scene, s, e, kind, text))
        y_by_scene += 1
        last_end = e
    css = [f".t{{font:13px/{LINE_H}px 'SFMono-Regular',Consolas,'Liberation Mono',monospace;white-space:pre}}"
           ".p{fill:#7ee787}.c{fill:#e6edf3}.o{fill:#b6c2d0}.f{fill:#ff7b72}.k{fill:#7ee787}.w{fill:#e3b341}"]
    body = []
    for n, (row, start, end, kind, text) in enumerate(rows):
        a, b = start / total * 100, end / total * 100
        eps = 0.01
        if kind == "cmd":
            type_end = (start + TYPE_SECONDS * 0.9) / total * 100
            css.append(f"@keyframes a{n}{{0%,{a:.3f}%{{opacity:0;clip-path:inset(0 100% 0 0)}}{a + eps:.3f}%{{opacity:1;clip-path:inset(0 100% 0 0)}}"
                       f"{type_end:.3f}%{{opacity:1;clip-path:inset(0 0 0 0)}}{b:.3f}%{{opacity:1;clip-path:inset(0 0 0 0)}}{b + eps:.3f}%,100%{{opacity:0}}}}")
        else:
            css.append(f"@keyframes a{n}{{0%,{a:.3f}%{{opacity:0}}{a + eps:.3f}%,{b:.3f}%{{opacity:1}}{b + eps:.3f}%,100%{{opacity:0}}}}")
        css.append(f".r{n}{{opacity:0;animation:a{n} {total:.2f}s linear infinite}}")
        y = 44 + row * LINE_H
        esc = html.escape(text)
        if kind == "cmd":
            body.append(f'<g class="r{n}"><text class="t p" x="22" y="{y}">$</text><text class="t c" x="40" y="{y}">{esc}</text></g>')
        else:
            style = "f" if text.lstrip().startswith(("FAIL", "ERROR", "UNBACKED", "Quality gate FAILED")) else \
                "k" if text.lstrip().startswith(("PASS", "Quality gate passed", "Numbers in prose: all")) else \
                "w" if text.lstrip().startswith(("THIN", "fix:")) else "o"
            body.append(f'<text class="t {style} r{n}" x="40" y="{y}">{esc}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}" role="img" '
            f'aria-label="Terminal replay of storymap-gen: describe the data, list findings, a failing quality gate, a passing one">'
            f'<style>{"".join(css)}</style><rect width="{WIDTH}" height="{HEIGHT}" rx="10" fill="#0d1117"/>'
            f'<circle cx="22" cy="18" r="6" fill="#ff5f56"/><circle cx="42" cy="18" r="6" fill="#ffbd2e"/><circle cx="62" cy="18" r="6" fill="#27c93f"/>'
            f'{"".join(body)}</svg>')


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
