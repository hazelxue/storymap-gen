"""A tiny, safe filter language for the ``where`` field of claims.

Claims come from a language model (and so, indirectly, from whatever text the model has read), so a ``where`` string
must never be handed to ``DataFrame.query`` / ``eval``. This module parses the expression with ``ast`` and evaluates only
an explicit whitelist:

    name.notna()          type == 'park'          st_width >= 30 and st_width < 50
    highway in ['primary', 'secondary']          not name.isna()          (a == 1) or (b != 'x')

Anything else (attribute access, calls other than ``.notna()/.isna()``, subscripts, comprehensions, names that are not
columns, arithmetic, ...) raises ``ValueError``.
"""
from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from typing import Any

import pandas as pd

MAX_LENGTH = 300
MAX_NODES = 80
_COMPARE: dict[type, Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}
_NULL_TESTS = {"notna": "notna", "notnull": "notna", "isna": "isna", "isnull": "isna"}


def _const(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool, type(None))):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant) \
            and isinstance(node.operand.value, (int, float)) and not isinstance(node.operand.value, bool):
        return -node.operand.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_const(e) for e in node.elts]
    raise ValueError("only literal values are allowed on the right of a comparison")


def _column(node: ast.AST, df: pd.DataFrame) -> pd.Series:
    if isinstance(node, ast.Name) and node.id in df.columns:
        return df[node.id]
    name = node.id if isinstance(node, ast.Name) else type(node).__name__
    raise ValueError(f"'{name}' is not a column of the layer (columns: {sorted(map(str, df.columns))[:12]})")


def _eval(node: ast.AST, df: pd.DataFrame) -> pd.Series:
    if isinstance(node, ast.BoolOp):
        parts = [_eval(v, df) for v in node.values]
        out = parts[0]
        for p in parts[1:]:
            out = (out & p) if isinstance(node.op, ast.And) else (out | p)
        return out
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return ~_eval(node.operand, df)
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
        op, right = node.ops[0], _const(node.comparators[0])
        left = _column(node.left, df)
        if isinstance(op, (ast.In, ast.NotIn)):
            if not isinstance(right, list):
                raise ValueError("'in' needs a list of literals")
            mask = left.isin(right)
            return mask if isinstance(op, ast.In) else ~mask
        if type(op) in _COMPARE:
            if isinstance(right, list):
                raise ValueError("comparison with a list needs 'in'")
            return _COMPARE[type(op)](left, right).fillna(False).astype(bool)
        raise ValueError(f"operator {type(op).__name__} is not allowed")
    if (isinstance(node, ast.Call) and not node.args and not node.keywords and isinstance(node.func, ast.Attribute)
            and node.func.attr in _NULL_TESTS):
        col = _column(node.func.value, df)
        return col.notna() if _NULL_TESTS[node.func.attr] == "notna" else col.isna()
    raise ValueError(f"unsupported expression element: {type(node).__name__}")


def safe_filter(df: pd.DataFrame, expr: str) -> pd.DataFrame:
    """Return the rows of ``df`` matching ``expr`` (see module docstring for the language)."""
    if len(expr) > MAX_LENGTH:
        raise ValueError(f"filter expression is longer than {MAX_LENGTH} characters")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise ValueError(f"filter expression is not valid: {e.msg}") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ValueError("filter expression is too complex")
    try:
        mask = _eval(tree.body, df)
    except TypeError as e:  # e.g. comparing a text column with a number
        raise ValueError(f"filter compares incompatible types: {e}") from None
    return df[mask.fillna(False).astype(bool)]
