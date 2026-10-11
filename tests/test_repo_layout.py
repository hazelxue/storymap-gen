"""Packaging must include every subpackage: tests import from the source tree, so a missing package only shows up after `pip install`."""
import fnmatch
import re
import subprocess
import sys

from tests.conftest import ROOT


def _packages_on_disk() -> set[str]:
    out = set()
    for init in (ROOT / "storymap_gen").rglob("__init__.py"):
        out.add(".".join(init.parent.relative_to(ROOT).parts))
    return out


def _pyproject() -> str:
    return (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_every_subpackage_on_disk_is_packaged():
    text = _pyproject()
    on_disk = _packages_on_disk()
    assert "storymap_gen.sources" in on_disk
    match = re.search(r"\[tool\.setuptools\.packages\.find\]\s*\ninclude\s*=\s*\[([^\]]*)\]", text)
    if match:
        patterns = re.findall(r'"([^"]+)"', match.group(1))
        packaged = {p for p in on_disk if any(fnmatch.fnmatch(p, pat) for pat in patterns)}
    else:
        listed = re.search(r"\[tool\.setuptools\]\s*\npackages\s*=\s*\[([^\]]*)\]", text)
        assert listed, "pyproject must either use packages.find or list the packages"
        packaged = set(re.findall(r'"([^"]+)"', listed.group(1)))
    assert on_disk <= packaged, f"not packaged: {sorted(on_disk - packaged)}"


def test_console_scripts_point_at_importable_functions():
    import importlib
    block = re.search(r"\[project\.scripts\]\s*\n((?:.+\n)+?)\n", _pyproject() + "\n")
    assert block
    targets = re.findall(r'=\s*"([\w.]+:\w+)"', block.group(1))
    assert len(targets) == 2
    for target in targets:
        module, func = target.split(":")
        assert callable(getattr(importlib.import_module(module), func))


def test_missing_mcp_extra_gives_an_install_hint_not_a_traceback():
    code = "import sys; sys.modules['mcp'] = None; sys.modules['mcp.server'] = None; import storymap_gen.mcp_server"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode != 0 and "storymap-gen[mcp]" in out.stderr and "Traceback" not in out.stderr.split("storymap-gen[mcp]")[0][-200:]
