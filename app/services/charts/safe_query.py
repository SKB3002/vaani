"""Filter sanitiser — validates user-supplied pandas `DataFrame.query` strings via AST walk.

Only a tiny subset is allowed:
- BoolOp: `and`, `or`
- Compare: `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`
- Name (column references)
- Constant (str / int / float / bool)
- List / Tuple of constants (for `in` / `not in`)
- Unary `not`

Everything else (function calls, attribute access, subscripts, imports, generators,
lambdas, comprehensions, walrus, ...) is rejected with `UnsafeFilterError`.
"""
from __future__ import annotations

import ast


class UnsafeFilterError(ValueError):
    """Raised when a filter string contains disallowed syntax."""


_ALLOWED_CMP = (
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
)

_ALLOWED_BOOL = (ast.And, ast.Or)
_ALLOWED_UNARY = (ast.Not, ast.USub, ast.UAdd)


def validate_filter(expr: str) -> str:
    """Return the same string if safe; raise `UnsafeFilterError` otherwise."""
    if not isinstance(expr, str) or not expr.strip():
        raise UnsafeFilterError("filter must be a non-empty string")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise UnsafeFilterError(f"filter is not valid Python expression: {e}") from e

    _walk(tree.body)
    return expr


def _walk(node: ast.AST) -> None:
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, _ALLOWED_BOOL):
            raise UnsafeFilterError(f"bool op {type(node.op).__name__} not allowed")
        for v in node.values:
            _walk(v)
        return

    if isinstance(node, ast.Compare):
        _walk(node.left)
        for op in node.ops:
            if not isinstance(op, _ALLOWED_CMP):
                raise UnsafeFilterError(f"comparison {type(op).__name__} not allowed")
        for cmp in node.comparators:
            _walk(cmp)
        return

    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARY):
            raise UnsafeFilterError(f"unary {type(node.op).__name__} not allowed")
        _walk(node.operand)
        return

    if isinstance(node, ast.Name):
        return  # column reference

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (str, int, float, bool)) or node.value is None:
            return
        raise UnsafeFilterError(f"constant of type {type(node.value).__name__} not allowed")

    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for elt in node.elts:
            if not isinstance(elt, ast.Constant):
                raise UnsafeFilterError("list/tuple elements must be literals")
            _walk(elt)
        return

    raise UnsafeFilterError(f"syntax {type(node).__name__} not allowed in filter")
