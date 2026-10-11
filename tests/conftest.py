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
