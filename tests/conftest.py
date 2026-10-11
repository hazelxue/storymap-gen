import json
from pathlib import Path

import pytest

from storymap_gen.geo import DataStore

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


@pytest.fixture(scope="session")
def jh_store() -> DataStore:
    return DataStore.from_files({
        "streets_centerline": str(FIXTURES / "streets_centerline.geojson"),
        "parks_": str(FIXTURES / "parks.geojson"),
        "subway_stations_": str(FIXTURES / "subway_stations.geojson"),
    })


@pytest.fixture(scope="session")
def eixample_store() -> DataStore:
    return DataStore.from_files({"eixample": str(FIXTURES / "eixample_streets.geojson")})


@pytest.fixture(scope="session")
def example_specs() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "examples").glob("*.json")}


@pytest.fixture(autouse=True)
def _isolate_user_state(tmp_path_factory, monkeypatch):
    """Tests must never read or write the real saved token, cache, or credentials of whoever runs them."""
    base = tmp_path_factory.mktemp("state")
    monkeypatch.setenv("STORYMAP_CONFIG_DIR", str(base / "config"))
    monkeypatch.setenv("STORYMAP_CACHE_DIR", str(base / "cache"))
    monkeypatch.delenv("ARCGIS_TOKEN", raising=False)
    monkeypatch.delenv("STORYMAP_CLIENT_ID", raising=False)
