"""What a first-time user touches must work: documents link to things that exist, the skill installs, the issue forms are valid."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _links(path: Path):
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)       # not the examples inside code blocks
    for target in LINK.findall(text):
        if not re.match(r"^(https?:|mailto:|#)", target):
            yield target.split("#")[0]


DOCS = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md", ROOT / "CHANGELOG.md", ROOT / "skill" / "SKILL.md",
        *sorted((ROOT / "docs").rglob("*.md"))]


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_relative_link_points_at_a_file_that_exists(doc):
    missing = [t for t in _links(doc) if t and not (doc.parent / t).exists()]
    assert missing == [], missing


def test_the_readme_opens_with_the_demo_then_the_instructions():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert text.index("terminal-demo.svg") < text.index("## Get started") < text.index("## What it is")
    for must in ("python -m pip install -e .", "install_skill.py", "issues/new/choose", "docs/getting-started.md"):
        assert must in text


def test_the_installed_skill_has_no_broken_links_and_the_documents_it_needs(tmp_path):
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "install_skill.py"), "--project", str(tmp_path)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    base = tmp_path / ".claude" / "skills" / "storymap-gen"
    skill = base / "SKILL.md"
    assert skill.is_file() and (base / "docs" / "writing-guide.md").is_file() and (base / "examples" / "gracia_barcelona_full.json").is_file()
    missing = [t for t in _links(skill) if t and not (base / t).exists()]
    assert missing == [] and "](../" not in skill.read_text(encoding="utf-8")
    assert subprocess.run([sys.executable, str(ROOT / "scripts" / "install_skill.py"), "--project", str(tmp_path)]).returncode == 0   # again: an update


def test_the_version_flag_reports_the_package_version():
    out = subprocess.run([sys.executable, "-m", "storymap_gen", "--version"], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0 and re.match(r"storymap-gen \d+\.\d+\.\d+", out.stdout)


def test_issue_forms_have_the_fields_users_need_and_a_privacy_confirmation():
    yaml = pytest.importorskip("yaml")
    for name in ("feedback", "bug_report"):
        form = yaml.safe_load((ROOT / ".github" / "ISSUE_TEMPLATE" / f"{name}.yml").read_text(encoding="utf-8"))
        kinds = [b["type"] for b in form["body"]]
        assert form["name"] and "textarea" in kinds and "checkboxes" in kinds
        privacy = next(b for b in form["body"] if b["type"] == "checkboxes")
        assert privacy["attributes"]["options"][0]["required"] is True
        assert all(b["attributes"].get("label") for b in form["body"] if b["type"] != "markdown")
    config = yaml.safe_load((ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml").read_text(encoding="utf-8"))
    assert config["blank_issues_enabled"] is False and len(config["contact_links"]) >= 1


def test_the_getting_started_guide_covers_every_step_a_new_user_needs():
    text = (ROOT / "docs" / "getting-started.md").read_text(encoding="utf-8")
    for must in ("## 1. Try it", "## 2. Make a story", "## 3. Put it in ArcGIS", "## When something goes wrong", "## What it can and cannot do",
                 "Developer credentials", "https://localhost:8765/storymap-gen-callback", "--download-images", "STORYMAP_MAX_AREA_KM2"):
        assert must in text, must
