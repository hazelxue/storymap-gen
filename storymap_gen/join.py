"""Join a table of numbers (a CSV: census figures, a city's indicators) to a polygon layer, so the layer can be coloured by them.

The join key is compared as text after trimming (and, if asked, zero-padding: a census id such as ``06037`` loses its leading zero in
many spreadsheets). Numbers are read tolerantly (``$54,300``, ``12.5%``) and anything that is not a number becomes empty rather than
being guessed. The command reports how many polygons matched and refuses a join that matches too few, because a wrong key is the usual
mistake and a map of mostly empty polygons is worse than an error.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd

MAX_TABLE_BYTES = 50 * 1024 * 1024
MAX_ROWS = 500_000
MIN_MATCH_SHARE = 0.5


@dataclass
class JoinReport:
    polygons: int
    matched: int
    unmatched_polygons: list[str]
    unmatched_rows: int
    fields: list[str]


def read_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() not in {".csv", ".tsv", ".txt"}:
        raise ValueError("the table must be a .csv, .tsv or .txt file")
    if not p.is_file():
        raise FileNotFoundError("table file not found")
    if p.stat().st_size > MAX_TABLE_BYTES:
        raise ValueError(f"the table is larger than {MAX_TABLE_BYTES // (1024 * 1024)} MB")
    sep = "\t" if p.suffix.lower() == ".tsv" else None
    try:
        df = pd.read_csv(p, sep=sep, engine="python", dtype=str, nrows=MAX_ROWS + 1, encoding="utf-8-sig")
    except Exception as e:  # noqa: BLE001 - the readers raise many types for a malformed file
        raise ValueError(f"could not read the table ({type(e).__name__})") from None
    if len(df) > MAX_ROWS:
        raise ValueError(f"the table has more than {MAX_ROWS} rows")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _key(series: pd.Series, zfill: int) -> pd.Series:
    out = series.astype("string").str.strip()
    out = out.str.replace(r"\.0+$", "", regex=True)       # 6037.0 -> 6037
    return out.str.zfill(zfill) if zfill else out


def to_number(series: pd.Series) -> pd.Series:
    """Numbers from text such as '$54,300', '12.5%', '(3)' or '1 234'; anything else is empty."""
    text = series.astype("string").str.strip()
    negative = text.str.match(r"^\(.*\)$")
    cleaned = text.str.replace(r"[,$%\s]", "", regex=True).str.replace(r"^\((.*)\)$", r"\1", regex=True)
    num = pd.to_numeric(cleaned, errors="coerce")
    return num.where(~negative.fillna(False), -num)


def join_table(layer: gpd.GeoDataFrame, table: pd.DataFrame, key: str, table_key: str, fields: list[str], zfill: int = 0,
               force: bool = False) -> tuple[gpd.GeoDataFrame, JoinReport]:
    if key not in layer.columns:
        raise ValueError(f"the layer has no column '{key}' (columns: {sorted(map(str, layer.columns))[:12]})")
    if table_key not in table.columns:
        raise ValueError(f"the table has no column '{table_key}' (columns: {sorted(map(str, table.columns))[:12]})")
    missing = [f for f in fields if f not in table.columns]
    if missing:
        raise ValueError(f"the table has no column {', '.join(repr(m) for m in missing)}")
    if not fields:
        raise ValueError("name at least one field to bring over")
    if layer[key].duplicated().any():
        raise ValueError(f"'{key}' is not unique in the layer; a join needs one polygon per key")
    t = table.copy()
    t["_k"] = _key(t[table_key], zfill)
    t = t.dropna(subset=["_k"]).drop_duplicates("_k", keep="first")
    for f in fields:
        t[f] = to_number(t[f])
    layer_keys = _key(layer[key], zfill)
    out = layer.copy()
    out["_k"] = layer_keys
    merged = out.merge(t[["_k", *fields]], on="_k", how="left", suffixes=("", "_table"))
    for f in fields:
        if f in out.columns:                      # never overwrite an existing column silently
            merged = merged.rename(columns={f"{f}_table": f"{f}_joined"})
    matched_mask = merged[fields[0] if fields[0] in merged.columns else f"{fields[0]}_joined"].notna() | merged["_k"].isin(set(t["_k"]))
    matched = int(matched_mask.sum())
    share = matched / len(layer) if len(layer) else 0.0
    if share < MIN_MATCH_SHARE and not force:
        raise ValueError(f"only {matched} of {len(layer)} polygons matched a table row ({share:.0%}); check the key columns and zero-padding "
                         f"(--zfill), or pass --force")
    report = JoinReport(
        polygons=len(layer), matched=matched,
        unmatched_polygons=[str(v) for v in layer.loc[~matched_mask.values, key].head(10)],
        unmatched_rows=int((~t["_k"].isin(set(layer_keys.dropna()))).sum()), fields=fields)
    return gpd.GeoDataFrame(merged.drop(columns=["_k"]), geometry=layer.geometry.name, crs=layer.crs), report


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", text)[:40] or "layer"
