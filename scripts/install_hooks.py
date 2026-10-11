"""Install the git hook that runs the privacy check before every push, so private information cannot be published by accident.

    python scripts/install_hooks.py

The hook runs `python scripts/privacy_check.py --history` and refuses the push if it fails. Hooks are local to a clone (git does not
copy them), so run this once in every clone; the same check also runs in CI.
"""
from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = """#!/bin/sh
# installed by scripts/install_hooks.py: stop private information from being pushed
cd "$(git rev-parse --show-toplevel)" || exit 1
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1; then
    "$py" scripts/privacy_check.py --history || { echo "push refused by the privacy check" >&2; exit 1; }
    exit 0
  fi
done
echo "no python found to run the privacy check; push refused" >&2
exit 1
"""


def main() -> int:
    hooks = Path(subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip())
    hooks = hooks if hooks.is_absolute() else ROOT / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    target = hooks / "pre-push"
    target.write_text(HOOK, encoding="utf-8", newline="\n")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"installed {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
