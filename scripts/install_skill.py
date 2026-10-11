"""Install the storymap-gen skill into Claude Code, so you can ask for a StoryMap in plain language.

    python scripts/install_skill.py                 for you, in every project   (~/.claude/skills/storymap-gen)
    python scripts/install_skill.py --project DIR   for one project only        (DIR/.claude/skills/storymap-gen)

It copies the skill and the few documents it points to (the writing guide, the claim reference, a finished example), and rewrites the
links so they work from the installed folder. Nothing is downloaded and nothing outside that folder is changed. Run it again to update.
The `storymap-gen` command itself must be installed too (`pip install ...`, see docs/getting-started.md).
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["concepts.md", "claims.md", "data-sources.md", "writing-guide.md", "thematic-maps.md", "case-study-patterns.md", "security.md", "spec.schema.json"]
EXAMPLES = ["gracia_barcelona_full.json"]


def rewrite_links(text: str) -> str:
    text = text.replace("](../docs/", "](docs/").replace("](../examples/", "](examples/")
    return re.sub(r"\]\(\.\./([A-Za-z0-9_./-]+)\)", r"](https://github.com/hazelxue/storymap-gen/blob/main/\1)", text)


def install(target: Path) -> list[str]:
    (target / "docs").mkdir(parents=True, exist_ok=True)
    (target / "examples").mkdir(parents=True, exist_ok=True)
    skill = (ROOT / "skill" / "SKILL.md").read_text(encoding="utf-8")
    (target / "SKILL.md").write_text(rewrite_links(skill), encoding="utf-8")
    copied = ["SKILL.md"]
    for name in DOCS:
        shutil.copyfile(ROOT / "docs" / name, target / "docs" / name)
        copied.append(f"docs/{name}")
    for name in EXAMPLES:
        shutil.copyfile(ROOT / "examples" / name, target / "examples" / name)
        copied.append(f"examples/{name}")
    return copied


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="install into this project's .claude/skills instead of your personal skills folder")
    args = ap.parse_args()
    base = Path(args.project).expanduser().resolve() / ".claude" / "skills" if args.project else Path.home() / ".claude" / "skills"
    target = base / "storymap-gen"
    copied = install(target)
    print(f"Installed the storymap-gen skill into {target} ({len(copied)} files).")
    print("Start Claude Code and ask, for example: \"Make a StoryMap about transport in <a district>.\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
