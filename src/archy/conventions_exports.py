"""What a module's `__all__` holds, and whether that was read in full (#755).

The module view promises that absence from a list is an answer, so a partly
read `__all__` has to say it is partial. Top-level statements are folded in
order. Every other use of the name has to be one known only to read it, or the
result is incomplete: listing the ways to write a list (aliases, helpers,
dunder calls) never finishes, and the miss is a confident wrong answer.

Not detected, from one file's syntax: another module editing this one's
`__all__`, a module `__getattr__` supplying it, and `globals().update` or a
star import carrying it in without the name appearing.

archy:owns        Exports, read_exports
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict

_NAME = "__all__"
# Wrappers that keep a sequence's members, as in `__all__ = sorted(__all__)`.
_SAME_MEMBERS = frozenset({"list", "tuple", "sorted", "set"})
# Builtins that read a sequence and keep no reference a caller could write through.
_READERS = _SAME_MEMBERS | {"len", "iter", "enumerate", "frozenset", "print", "isinstance"}
_READING_METHODS = frozenset({"index", "count", "copy"})
_REORDERS = frozenset({"sort", "reverse"})
# Calls that hand out the module's namespace, so a key can rebind `__all__`.
_NAMESPACES = frozenset({"globals", "locals", "vars"})


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
    shadowed = _bound_names(tree)
    current: Exports | None = None
    # The uses of the name the fold below has accounted for.
    accounted: set[ast.AST] = set()
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and node.value is None:
            accounted.add(node.target)  # declares a type and binds nothing
            continue
        folded = _fold(node, current, shadowed)
        if folded is not None:
            current, use = folded
            accounted.add(use)
    accounted.update(_reading_uses(tree, shadowed))
    if any(_may_write(node, accounted) for node in ast.walk(tree)):
        current = (current or Exports()).unsure()
    return current


def _fold(
    node: ast.stmt, current: Exports | None, shadowed: frozenset[str]
) -> tuple[Exports, ast.AST] | None:
    """`__all__` after this statement and the use of the name it accounts for,
    or None when it is not a form read here."""
    if isinstance(node, ast.Assign | ast.AnnAssign):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        target = next((t for t in targets if _is_all(t)), None)
        if node.value is None or target is None:
            return None
        value = _read(node.value, current, shadowed)
        # `__all__ = other = [...]` leaves a second name for the same list.
        return (value if len(targets) == 1 else value.unsure()), target
    if isinstance(node, ast.ImportFrom) and any(_binds_all(a) for a in node.names):
        return Exports(complete=False), next(a for a in node.names if _binds_all(a))
    if current is None:
        return None
    if isinstance(node, ast.AugAssign) and _is_all(node.target):
        if isinstance(node.op, ast.Add):
            return current.plus(_read(node.value, current, shadowed)), node.target
        return current.unsure(), node.target
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        return _fold_method(node.value, current, shadowed)
    return None


def _fold_method(
    call: ast.Call, current: Exports, shadowed: frozenset[str]
) -> tuple[Exports, ast.AST] | None:
    """The in-place methods whose effect on the members is certain.

    `remove` is not one: names are a set here, so a duplicate would be lost.
    """
    func = call.func
    if not isinstance(func, ast.Attribute) or not _is_all(func.value):
        return None
    if func.attr in _REORDERS:
        return current, func.value
    if func.attr == "extend" and len(call.args) == 1:
        return current.plus(_read(call.args[0], current, shadowed)), func.value
    if func.attr == "append" and len(call.args) == 1:
        return current.plus(_element(call.args[0], current, shadowed)), func.value
    return None


def _read(node: ast.expr, current: Exports | None, shadowed: frozenset[str]) -> Exports:
    """The string members of an expression, as far as they are literal."""
    names: set[str] = set()
    complete = True
    # A worklist, not recursion: a generated `[...] + [...] + ...` chain is as
    # deep as it is long.
    pending = [node]
    while pending:
        part = pending.pop()
        if isinstance(part, ast.List | ast.Tuple | ast.Set):
            for element in part.elts:
                if isinstance(element, ast.Starred):
                    pending.append(element.value)
                elif (name := _string(element)) is not None:
                    names.add(name)
                else:
                    complete = False
        elif isinstance(part, ast.BinOp) and isinstance(part.op, ast.Add):
            pending += [part.left, part.right]
        elif _is_all(part) and current is not None:
            names |= current.names
            complete = complete and current.complete
        elif (wrapped := _wrapped(part, shadowed)) is not None:
            pending.append(wrapped)
        else:
            complete = False
    return Exports(names=frozenset(names), complete=complete)


def _wrapped(node: ast.expr, shadowed: frozenset[str]) -> ast.expr | None:
    """The sequence inside `sorted(...)` and its kind, or None for anything else."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _SAME_MEMBERS - shadowed
        and len(node.args) == 1
    ):
        return node.args[0]
    return None


def _element(node: ast.expr, current: Exports, shadowed: frozenset[str]) -> Exports:
    return _read(ast.List(elts=[node]), current, shadowed)


def _string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_all(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == _NAME


def _bound_names(tree: ast.Module) -> frozenset[str]:
    """Names the module defines or imports, which therefore are not the builtins."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.alias):
            bound.add(node.asname or node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
    return frozenset(bound)


def _reading_uses(tree: ast.Module, shadowed: frozenset[str]) -> Iterator[ast.AST]:
    """Expressions in positions that read a sequence without keeping a handle on it."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            yield from node.comparators
        elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
            yield node.iter
        elif isinstance(node, ast.If | ast.While | ast.Assert):
            yield node.test
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            yield from (node.left, node.right)
        elif isinstance(node, ast.Starred | ast.Subscript) and isinstance(node.ctx, ast.Load):
            yield node.value
        elif isinstance(node, ast.Call):
            yield from _read_by_call(node, shadowed)
        elif isinstance(node, ast.FunctionDef) and node.name == "__dir__":
            yield from (n.value for n in ast.walk(node) if isinstance(n, ast.Return) and n.value)


def _read_by_call(call: ast.Call, shadowed: frozenset[str]) -> Iterator[ast.AST]:
    func = call.func
    if isinstance(func, ast.Name) and func.id in _READERS - shadowed:
        yield from call.args
    elif isinstance(func, ast.Attribute) and func.attr in _READING_METHODS:
        yield func.value
    elif isinstance(func, ast.Attribute) and func.attr == "join":
        yield from call.args


def _may_write(node: ast.AST, accounted: set[ast.AST]) -> bool:
    """Whether this node can change what `__all__` holds in a way not folded."""
    if node in accounted:
        return False
    if isinstance(node, ast.Name):
        return node.id == _NAME
    if isinstance(node, ast.alias):
        return _binds_all(node)
    return _binds_by_string(node) or _writes_through_namespace(node)


def _binds_by_string(node: ast.AST) -> bool:
    """Binding forms that carry the name as a plain string, not a `Name`."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return node.name == _NAME
    if isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
        return node.name == _NAME
    return isinstance(node, ast.MatchMapping) and node.rest == _NAME


def _writes_through_namespace(node: ast.AST) -> bool:
    """`globals()["__all__"]`, `setattr(m, "__all__", v)`, `exec` and the like."""
    if isinstance(node, ast.Subscript):
        return _string(node.slice) == _NAME and _is_namespace(node.value)
    if isinstance(node, ast.Attribute):
        return node.attr == _NAME and _is_this_module(node.value)
    if not isinstance(node, ast.Call):
        return False
    name = node.func.id if isinstance(node.func, ast.Name) else None
    if name == "setattr":
        return len(node.args) > 1 and _string(node.args[1]) == _NAME
    if name == "exec":
        return len(node.args) == 1 or _is_namespace(node.args[1])
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "update"
        and _is_namespace(node.func.value)
        and any(_string(n) == _NAME for a in node.args for n in ast.walk(a))
    )


def _is_namespace(node: ast.expr) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == "__dict__" and _is_this_module(node.value)
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _NAMESPACES
    )


def _is_this_module(node: ast.expr) -> bool:
    """`sys.modules[__name__]`."""
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Name)
        and node.slice.id == "__name__"
    )


def _binds_all(alias: ast.alias) -> bool:
    return (alias.asname or alias.name) == _NAME
