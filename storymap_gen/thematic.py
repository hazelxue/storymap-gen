"""Thematic (graduated colour) maps: colour a layer by a number, with classes, a palette and a legend.

Most district stories show at least one map where polygons are coloured by a value (income, density, share of something).
A style names the field, how to cut the values into classes (quantile, equal interval or breaks you give), and a palette.
The classes are computed from the data that is drawn, so the legend always matches the map; the numbers a story states about
the map (the highest value, the lowest) are backed by claims in the usual way.
"""
from __future__ import annotations

import math
from typing import Any, Literal, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

# light to dark; classes are interpolated between these anchors (RGB)
PALETTES: dict[str, list[tuple[int, int, int]]] = {
    "blues": [(222, 235, 247), (107, 174, 214), (8, 69, 148)],
    "greens": [(229, 245, 224), (116, 196, 118), (0, 90, 50)],
    "oranges": [(254, 237, 222), (253, 141, 60), (140, 45, 4)],
    "reds": [(254, 229, 217), (251, 106, 74), (153, 0, 13)],
    "purples": [(242, 240, 247), (158, 154, 200), (84, 39, 143)],
    "teal": [(224, 243, 241), (80, 175, 170), (0, 80, 85)],
    "heat": [(255, 255, 204), (253, 141, 60), (128, 0, 38)],
}
Palette = Literal["blues", "greens", "oranges", "reds", "purples", "teal", "heat"]
NO_DATA_COLOR = [200, 200, 200, 160]


class LayerStyle(BaseModel):
    """How to colour one layer of a generated map by a numeric field."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(max_length=100, description="The numeric column to colour by")
    method: Literal["quantile", "equal", "manual"] = Field(default="quantile", description="How to cut the values into classes")
    classes: int = Field(default=5, ge=2, le=7)
    breaks: list[float] = Field(default_factory=list, max_length=8, description="For method=manual: classes+1 increasing numbers, the class edges")
    palette: Palette = "blues"
    reverse: bool = Field(default=False, description="Dark for low values instead of high")
    title: str = Field(default="", max_length=80, description="Legend and popup name of the field (default: the field name)")
    unit: str = Field(default="", max_length=20, description="Appended to the legend labels, e.g. '%' or ' per km2'")

    @model_validator(mode="after")
    def _check(self) -> LayerStyle:
        if self.method == "manual":
            if len(self.breaks) != self.classes + 1 or any(not math.isfinite(b) for b in self.breaks) \
                    or any(b2 <= b1 for b1, b2 in zip(self.breaks, self.breaks[1:], strict=False)):
                raise ValueError("manual breaks need classes+1 finite, strictly increasing numbers")
        elif self.breaks:
            raise ValueError("breaks only apply to method=manual")
        return self


def palette_colors(name: str, n: int, reverse: bool = False) -> list[list[int]]:
    """n colours from light to dark (or dark to light when reversed), interpolated along the palette."""
    anchors = np.array(PALETTES[name], dtype=float)
    pos = np.linspace(0, len(anchors) - 1, n)
    out = []
    for p in pos:
        lo = int(math.floor(p))
        hi = min(lo + 1, len(anchors) - 1)
        c = anchors[lo] + (anchors[hi] - anchors[lo]) * (p - lo)
        out.append([int(round(v)) for v in c])
    return out[::-1] if reverse else out


def numeric_values(series: pd.Series) -> pd.Series:
    """The finite numeric values of a column (text that is not a number is not coerced)."""
    if not pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
        raise ValueError(f"'{series.name}' is not a numeric column; a graduated map needs numbers (join or convert it first)")
    vals = series.astype(float)
    return vals[np.isfinite(vals)]


def class_breaks(values: pd.Series, style: LayerStyle) -> list[float]:
    """The edges of the classes: ``classes+1`` increasing numbers from the lowest to the highest value (fewer if values repeat)."""
    if values.nunique() < 2:
        raise ValueError(f"'{style.field}' has fewer than two different values; there is nothing to grade")
    if style.method == "manual":
        return list(style.breaks)
    lo, hi = float(values.min()), float(values.max())
    if style.method == "equal":
        edges = np.linspace(lo, hi, style.classes + 1)
    else:
        edges = np.quantile(values, np.linspace(0, 1, style.classes + 1))
    out = [float(e) for e in edges]
    out[0], out[-1] = lo, hi
    dedup = [out[0]]
    for e in out[1:]:
        if e > dedup[-1]:
            dedup.append(e)
    return dedup


def _fmt(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 100:
        return f"{v:.0f}"
    if abs(v) >= 10:
        return f"{v:.1f}".rstrip("0").rstrip(".")
    return f"{v:.2f}".rstrip("0").rstrip(".")


def renderer(values: pd.Series, style: LayerStyle, make_symbol: Any) -> dict[str, Any]:
    """An Esri classBreaks renderer. ``make_symbol(rgba)`` builds the symbol of one class for the layer's geometry type."""
    edges = class_breaks(values, style)
    n = len(edges) - 1
    colors = palette_colors(style.palette, n, style.reverse)
    infos = []
    for i in range(n):
        lo, hi = edges[i], edges[i + 1]
        infos.append({"classMinValue": lo, "classMaxValue": hi, "label": f"{_fmt(lo)} to {_fmt(hi)}{style.unit}",
                      "description": "", "symbol": make_symbol([*colors[i], 215])})
    return {"type": "classBreaks", "field": style.field, "minValue": edges[0], "classBreakInfos": infos,
            "defaultSymbol": make_symbol(NO_DATA_COLOR), "defaultLabel": "No data"}


def summarize(values: pd.Series, style: Optional[LayerStyle] = None) -> dict[str, Any]:
    """A few numbers about the values (for messages and tests)."""
    return {"count": int(len(values)), "min": float(values.min()), "max": float(values.max()), "median": float(values.median())}
