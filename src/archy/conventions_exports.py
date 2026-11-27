"""What a module's `__all__` holds, and whether that was read in full (#755).

The module view promises that absence from a list is an answer, so a partly
read `__all__` has to say it is partial. Top-level statements are folded in
order. Every other use of the name, or of the string "__all__", has to be one
known only to read it, or the result is incomplete: listing the ways to write a
list (aliases, helpers, dunder calls) never finishes, and the miss is a
confident wrong answer.

Not detected, from one file's syntax: another module editing this one's
`__all__`, a module `__getattr__` supplying it, `globals().update` or a star
import carrying it in without the name appearing, and an operand class whose
own operators keep a reference to the list.

archy:owns        Exports, read_exports
"""

from __future__ import annotations

import ast
from typing import TypeGuard

from pydantic import BaseModel, ConfigDict

from archy.conventions_exports_uses import (
    SAME_MEMBERS,
    accounted_uses,
    binds_all,
    bound_names,
    called_name,
    is_all,
    may_write,
    string_value,
    this_module_names,
)

_REORDERS = frozenset({"sort", "reverse"})
# `__all__` after a statement, and the use of the name that statement accounts for.
_Folded = tuple["Exports", ast.AST] | None


class Exports(BaseModel):
    """The names read from `__all__`.

    `complete=False` means they may not be the exact set: there may be others,
    and after an unreadable removal or rebinding some listed ones may be gone.
    """

    model_config = ConfigDict(frozen=True)

    names: frozenset[str] = frozenset()
    complete: bool = True

    def plus(self, other: Exports) -> Exports:
        return Exports(names=self.names | other.names, complete=self.complete and other.complete)

    def unsure(self) -> Exports:
        return Exports(names=self.names, complete=False)


def read_exports(tree: ast.Module) -> Exports | None:
    """`__all__` as the module leaves it, or None when nothing declares one."""
    shadowed = bound_names(tree)
    current, accounted = _fold_body(tree, shadowed)
    accounted |= accounted_uses(tree, shadowed)
    this_module = this_module_names(tree)
    if any(n not in accounted and may_write(n, this_module) for n in ast.walk(tree)):
        return (current or Exports()).unsure()
    return current


def _fold_body(tree: ast.Module, shadowed: frozenset[str]) -> tuple[Exports | None, set[ast.AST]]:
    """The top-level statements applied in order, and the uses that accounts for."""
    current: Exports | None = None
    accounted: set[ast.AST] = set()
    for node in tree.body:
        if _only_declares(node):
            accounted.add(node.target)
        elif (folded := _fold(node, current, shadowed)) is not None:
            current, use = folded
            accounted.add(use)
    return current, accounted


def _only_declares(node: ast.stmt) -> TypeGuard[ast.AnnAssign]:
    """`__all__: list[str]` states a type and binds nothing."""
    return isinstance(node, ast.AnnAssign) and node.value is None


def _fold(node: ast.stmt, current: Exports | None, shadowed: frozenset[str]) -> _Folded:
    """Apply one top-level statement, or None when it is not a form read here."""
    if isinstance(node, ast.Assign | ast.AnnAssign):
        return _fold_assign(node, current, shadowed)
    if isinstance(node, ast.ImportFrom):
        return _fold_import(node)
    return None if current is None else _fold_change(node, current, shadowed)


def _fold_change(node: ast.stmt, current: Exports, shadowed: frozenset[str]) -> _Folded:
    """A statement that edits an `__all__` already declared."""
    if isinstance(node, ast.AugAssign):
        return _fold_augment(node, current, shadowed)
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        return _fold_method(node.value, current, shadowed)
    return None


def _fold_assign(
    node: ast.Assign | ast.AnnAssign, current: Exports | None, shadowed: frozenset[str]
) -> _Folded:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    target = next((t for t in targets if is_all(t)), None)
    if node.value is None or target is None:
        return None
    value = _read(node.value, current, shadowed)
    # `__all__ = other = [...]` leaves a second name for the same list.
    return (value if len(targets) == 1 else value.unsure()), target


def _fold_import(node: ast.ImportFrom) -> _Folded:
    alias = next((a for a in node.names if binds_all(a)), None)
    return None if alias is None else (Exports(complete=False), alias)


def _fold_augment(node: ast.AugAssign, current: Exports, shadowed: frozenset[str]) -> _Folded:
    if not is_all(node.target):
        return None
    if not isinstance(node.op, ast.Add):
        return current.unsure(), node.target
    return current.plus(_read(node.value, current, shadowed)), node.target


def _fold_method(call: ast.Call, current: Exports, shadowed: frozenset[str]) -> _Folded:
    """The in-place methods whose effect on the members is certain.

    `remove` is not one: names are a set here, so a duplicate would be lost.
    """
    func = call.func
    if not isinstance(func, ast.Attribute) or not is_all(func.value):
        return None
    if func.attr in _REORDERS:
        return current, func.value
    added = _added_by(func.attr, call.args, current, shadowed)
    return None if added is None else (current.plus(added), func.value)


def _added_by(
    method: str, args: list[ast.expr], current: Exports, shadowed: frozenset[str]
) -> Exports | None:
    if len(args) != 1:
        return None
    if method == "extend":
        return _read(args[0], current, shadowed)
    if method == "append":
        return _read(ast.List(elts=[args[0]]), current, shadowed)
    return None


def _read(node: ast.expr, current: Exports | None, shadowed: frozenset[str]) -> Exports:
    """The string members of an expression, as far as they are literal."""
    names: set[str] = set()
    complete = True
    # A worklist, not recursion: a generated `[...] + [...] + ...` chain is as
    # deep as it is long.
    pending = [node]
    while pending:
        parts, found = _expand(pending.pop(), current, shadowed)
        pending += parts
        names |= found.names
        complete = complete and found.complete
    return Exports(names=frozenset(names), complete=complete)


def _expand(
    node: ast.expr, current: Exports | None, shadowed: frozenset[str]
) -> tuple[list[ast.expr], Exports]:
    """One step of `_read`: the sub-expressions still to read, and what this one gave."""
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return _expand_display(node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return [node.left, node.right], Exports()
    if is_all(node) and current is not None:
        return [], current
    wrapped = _wrapped(node, shadowed)
    if wrapped is not None:
        return [wrapped], Exports()
    return [], Exports(complete=False)


def _expand_display(elements: list[ast.expr]) -> tuple[list[ast.expr], Exports]:
    starred = [e.value for e in elements if isinstance(e, ast.Starred)]
    plain = [string_value(e) for e in elements if not isinstance(e, ast.Starred)]
    names = frozenset(name for name in plain if name is not None)
    return starred, Exports(names=names, complete=None not in plain)


def _wrapped(node: ast.expr, shadowed: frozenset[str]) -> ast.expr | None:
    """The sequence inside `sorted(...)` and its kind, or None for anything else."""
    if not isinstance(node, ast.Call) or len(node.args) != 1:
        return None
    return node.args[0] if called_name(node) in SAME_MEMBERS - shadowed else None
