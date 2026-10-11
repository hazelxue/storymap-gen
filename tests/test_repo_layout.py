"""Packaging must include every subpackage: tests import from the source tree, so a missing package only shows up after `pip install`."""
import tomllib
from setuptools import find_packages

from tests.conftest import ROOT


def test_every_subpackage_on_disk_is_packaged():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]
    on_disk = set(find_packages(where=str(ROOT), include=["storymap_gen*"]))
    assert "storymap_gen.sources" in on_disk
    if "packages" in cfg and isinstance(cfg["packages"], list):
        packaged = set(cfg["packages"])
    else:
        packaged = set(find_packages(where=str(ROOT), include=cfg["packages"]["find"]["include"]))
    assert on_disk <= packaged, f"not packaged: {sorted(on_disk - packaged)}"


def test_console_scripts_point_at_importable_functions():
    import importlib
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    for target in scripts.values():
        module, func = target.split(":")
        assert callable(getattr(importlib.import_module(module), func))


def test_missing_mcp_extra_gives_an_install_hint_not_a_traceback():
    import subprocess
    import sys
    code = "import sys; sys.modules['mcp'] = None; sys.modules['mcp.server'] = None; import storymap_gen.mcp_server"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode != 0 and "storymap-gen[mcp]" in out.stderr and "Traceback" not in out.stderr.split("storymap-gen[mcp]")[0][-200:]
