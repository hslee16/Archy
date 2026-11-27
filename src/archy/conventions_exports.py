"""What a module's `__all__` holds, and whether that was read in full (#755).

The module view promises that absence from a list is an answer, so a partly
read `__all__` has to say it is partial. Top-level statements are folded in
order; any other write to `__all__`, wherever it sits, makes the result
incomplete rather than guessed at.

archy:owns        Exports, read_exports
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict

_NAME = "__all__"
# Wrappers that keep a sequence's members, as in `__all__ = sorted(__all__)`.
_SAME_MEMBERS = frozenset({"list", "tuple", "sorted", "set"})
_REORDERS = frozenset({"sort", "reverse"})
_MUTATORS = (
    _REORDERS
    | {"append", "extend", "insert", "remove", "pop", "clear"}
    | {
        "add",
        "update",
        "discard",
    }
)


class Exports(BaseModel):
    """The names read from `__all__`. `complete=False` means there may be others
    (or, after an unreadable removal, fewer)."""

    model_config = ConfigDict(frozen=True)

    names: frozenset[str] = frozenset()
    complete: bool = True

    def plus(self, other: Exports) -> Exports:
        return Exports(names=self.names | other.names, complete=self.complete and other.complete)

    def unsure(self) -> Exports:
        return Exports(names=self.names, complete=False)


def read_exports(tree: ast.Module) -> Exports | None:
    """`__all__` as the module leaves it, or None when nothing declares one."""
    current: Exports | None = None
    # `__all__: list[str]` declares a type and binds nothing.
    handled: set[ast.stmt] = {
        n for n in tree.body if isinstance(n, ast.AnnAssign) and n.value is None
    }
    for node in tree.body:
        folded = _fold(node, current)
        if folded is not None:
            current = folded
            handled.add(node)
    if any(_writes_all(node) for node in _walk_outside(tree, handled)):
        current = (current or Exports()).unsure()
    return current


def _fold(node: ast.stmt, current: Exports | None) -> Exports | None:
    """`__all__` after this statement, or None when it is not a form read here."""
    if isinstance(node, ast.Assign | ast.AnnAssign):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if node.value is None or not any(_is_all(t) for t in targets):
            return None
        return _read(node.value, current)
    if isinstance(node, ast.ImportFrom) and any(_binds_all(a) for a in node.names):
        return Exports(complete=False)
    if current is None:
        return None
    if isinstance(node, ast.AugAssign) and _is_all(node.target):
        if isinstance(node.op, ast.Add | ast.BitOr):
            return current.plus(_read(node.value, current))
        return current.unsure()
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        return _fold_method(node.value, current)
    return None


def _fold_method(call: ast.Call, current: Exports) -> Exports | None:
    func = call.func
    if not isinstance(func, ast.Attribute) or not _is_all(func.value):
        return None
    if func.attr in _REORDERS:
        return current
    if func.attr == "extend" and len(call.args) == 1:
        return current.plus(_read(call.args[0], current))
    if func.attr in {"append", "remove"} and len(call.args) == 1:
        name = _string(call.args[0])
        if name is not None:
            names = current.names | {name} if func.attr == "append" else current.names - {name}
            return Exports(names=names, complete=current.complete)
    return current.unsure() if func.attr in _MUTATORS else None


def _read(node: ast.expr, current: Exports | None) -> Exports:
    """The string members of an expression, as far as they are literal."""
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        result = Exports()
        for element in node.elts:
            result = result.plus(_element(element, current))
        return result
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.BitOr):
        return _read(node.left, current).plus(_read(node.right, current))
    if _is_all(node) and current is not None:
        return current
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _SAME_MEMBERS
        and len(node.args) == 1
    ):
        return _read(node.args[0], current)
    return Exports(complete=False)


def _element(node: ast.expr, current: Exports | None) -> Exports:
    if isinstance(node, ast.Starred):
        return _read(node.value, current)
    name = _string(node)
    return Exports(complete=False) if name is None else Exports(names=frozenset({name}))


def _string(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_all(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == _NAME


def _walk_outside(tree: ast.Module, handled: set[ast.stmt]) -> Iterator[ast.AST]:
    for node in tree.body:
        if node not in handled:
            yield from ast.walk(node)


def _writes_all(node: ast.AST) -> bool:
    """Whether this node can change what `__all__` holds."""
    if isinstance(node, ast.Name):
        return node.id == _NAME and not isinstance(node.ctx, ast.Load)
    if isinstance(node, ast.Subscript):
        return _is_all(node.value) and not isinstance(node.ctx, ast.Load)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return _is_all(node.func.value) and node.func.attr in _MUTATORS
    return isinstance(node, ast.alias) and _binds_all(node)


def _binds_all(alias: ast.alias) -> bool:
    return (alias.asname or alias.name) == _NAME
