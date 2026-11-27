"""Which uses of `__all__` only read it, and which may change it (#755).

Support for `conventions_exports`, which folds the forms it can follow and asks
here about every other appearance of the name.

archy:owns        accounted_uses, binds_all, bound_names, called_name, is_all,
                  may_write, string_value, this_module_names
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from functools import singledispatch

ALL_NAME = "__all__"
# Wrappers that keep a sequence's members, as in `__all__ = sorted(__all__)`.
SAME_MEMBERS = frozenset({"list", "tuple", "sorted", "set"})
# Builtins that read a sequence and keep no reference a caller could write through.
_READERS = SAME_MEMBERS | {
    "len",
    "iter",
    "enumerate",
    "frozenset",
    "print",
    "isinstance",
    "reversed",
    "zip",
}
_READING_METHODS = frozenset({"index", "count", "copy"})
_COPYING_METHODS = frozenset({"join", "extend", "fromkeys"})
# Calls that hand out the module's namespace, so a key can rebind `__all__`.
_NAMESPACES = frozenset({"globals", "locals", "vars"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
# Nodes whose `name` field is a plain string that binds.
_STRING_BINDERS = (*_FUNCTIONS, ast.ClassDef, ast.ExceptHandler, ast.MatchAs, ast.MatchStar)


def is_all(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == ALL_NAME


def string_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def binds_all(alias: ast.alias) -> bool:
    return (alias.asname or alias.name) == ALL_NAME


def called_name(call: ast.Call) -> str | None:
    """`f` for `f(...)`, None for `a.f(...)` and anything else."""
    return call.func.id if isinstance(call.func, ast.Name) else None


def accounted_uses(tree: ast.Module, shadowed: frozenset[str]) -> set[ast.AST]:
    """The nodes anywhere in the module that mention `__all__` only to read it."""
    this_module = this_module_names(tree)
    accounted: set[ast.AST] = set()
    for node in ast.walk(tree):
        accounted.update(_read_positions(node, shadowed))
        accounted.update(_asked_about(node, this_module))
    return accounted


# ---- names the module binds -------------------------------------------------


def bound_names(tree: ast.Module) -> frozenset[str]:
    """Names the module binds anywhere, which therefore may not be the builtins.

    Methods are left out: `class C: def set(...)` shadows nothing outside it.
    """
    methods = {
        member
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for member in node.body
        if isinstance(member, _FUNCTIONS)
    }
    return frozenset(
        name
        for node in ast.walk(tree)
        if node not in methods and (name := _bound_by(node)) is not None
    )


@singledispatch
def _bound_by(node: ast.AST) -> str | None:
    return None


@_bound_by.register(ast.FunctionDef)
@_bound_by.register(ast.AsyncFunctionDef)
@_bound_by.register(ast.ClassDef)
def _bound_by_definition(node: ast.FunctionDef) -> str:
    return node.name


@_bound_by.register(ast.alias)
def _bound_by_import(node: ast.alias) -> str:
    return node.asname or node.name


@_bound_by.register(ast.arg)
def _bound_by_parameter(node: ast.arg) -> str:
    return node.arg


@_bound_by.register(ast.Name)
def _bound_by_store(node: ast.Name) -> str | None:
    return node.id if isinstance(node.ctx, ast.Store) else None


# ---- positions that read a sequence without keeping a handle on it ----------


@singledispatch
def _read_positions(node: ast.AST, shadowed: frozenset[str]) -> Iterable[ast.AST]:
    return ()


@_read_positions.register(ast.Compare)
def _read_compared(node: ast.Compare, shadowed: frozenset[str]) -> Iterable[ast.AST]:
    return (node.left, *node.comparators)


@_read_positions.register(ast.For)
@_read_positions.register(ast.AsyncFor)
@_read_positions.register(ast.comprehension)
def _read_iterated(node: ast.For, shadowed: frozenset[str]) -> Iterable[ast.AST]:
    return (node.iter,)


@_read_positions.register(ast.If)
@_read_positions.register(ast.While)
@_read_positions.register(ast.Assert)
@_read_positions.register(ast.IfExp)
def _read_tested(node: ast.If, shadowed: frozenset[str]) -> Iterable[ast.AST]:
    return (node.test,)


@_read_positions.register(ast.UnaryOp)
def _read_negated(node: ast.UnaryOp, shadowed: frozenset[str]) -> Iterable[ast.AST]:
    return (node.operand,) if isinstance(node.op, ast.Not) else ()


@_read_positions.register(ast.BinOp)
def _read_added(node: ast.BinOp, shadowed: frozenset[str]) -> Iterable[ast.AST]:
    return (node.left, node.right) if isinstance(node.op, ast.Add) else ()


@_read_positions.register(ast.Starred)
@_read_positions.register(ast.Subscript)
def _read_unpacked(node: ast.Starred, shadowed: frozenset[str]) -> Iterable[ast.AST]:
    return (node.value,) if isinstance(node.ctx, ast.Load) else ()


@_read_positions.register(ast.Call)
def _read_by_call(node: ast.Call, shadowed: frozenset[str]) -> Iterable[ast.AST]:
    if called_name(node) in _READERS - shadowed:
        return node.args
    if not isinstance(node.func, ast.Attribute):
        return ()
    if node.func.attr in _READING_METHODS:
        return (node.func.value,)
    return node.args if node.func.attr in _COPYING_METHODS else ()


@_read_positions.register(ast.FunctionDef)
def _read_returned_by_dir(node: ast.FunctionDef, shadowed: frozenset[str]) -> Iterable[ast.AST]:
    if node.name != "__dir__":
        return ()
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Return) and n.value]


# ---- the string "__all__" ---------------------------------------------------


@singledispatch
def _asked_about(node: ast.AST, this_module: frozenset[str]) -> Iterable[ast.AST]:
    """The places a `"__all__"` string only asks about the name."""
    return ()


@_asked_about.register(ast.Compare)
def _asked_by_comparison(node: ast.Compare, this_module: frozenset[str]) -> Iterable[ast.AST]:
    for operand in (node.left, *node.comparators):
        if isinstance(operand, ast.Tuple | ast.List | ast.Set):
            yield from ast.walk(operand)
        else:
            yield operand


@_asked_about.register(ast.Call)
def _asked_by_call(node: ast.Call, this_module: frozenset[str]) -> Iterable[ast.AST]:
    """The name argument of a `hasattr` or `getattr` that cannot hand out our list."""
    func = called_name(node)
    if func not in {"getattr", "hasattr"} or not node.args:
        return ()
    # Another module's `__all__` may be read; this one's would be a handle.
    if func == "getattr" and _is_this_module(node.args[0], this_module):
        return ()
    return node.args[1:2]


# ---- what may change it -----------------------------------------------------


def may_write(node: ast.AST, this_module: frozenset[str]) -> bool:
    """Whether a node nothing accounted for can change what `__all__` holds."""
    return (
        _mentions_all(node)
        or _binds_by_string(node)
        or _writes_through_namespace(node, this_module)
    )


@singledispatch
def _mentions_all(node: ast.AST) -> bool:
    return False


@_mentions_all.register(ast.Name)
def _mentions_by_name(node: ast.Name) -> bool:
    return node.id == ALL_NAME


@_mentions_all.register(ast.alias)
def _mentions_by_import(node: ast.alias) -> bool:
    return binds_all(node)


@_mentions_all.register(ast.Constant)
def _mentions_by_string(node: ast.Constant) -> bool:
    return node.value == ALL_NAME


@_mentions_all.register(ast.keyword)
def _mentions_by_keyword(node: ast.keyword) -> bool:
    return node.arg == ALL_NAME


def _binds_by_string(node: ast.AST) -> bool:
    """Binding forms that carry the name as a plain string, not a `Name`."""
    if isinstance(node, _STRING_BINDERS):
        return node.name == ALL_NAME
    return isinstance(node, ast.MatchMapping) and node.rest == ALL_NAME


def _writes_through_namespace(node: ast.AST, this_module: frozenset[str]) -> bool:
    """`sys.modules[__name__].__all__`, and `exec` into this module's namespace."""
    if isinstance(node, ast.Attribute):
        return node.attr == ALL_NAME and _is_this_module(node.value, this_module)
    return isinstance(node, ast.Call) and _execs_here(node)


def _execs_here(call: ast.Call) -> bool:
    # No positional argument means this is some other function called `exec`.
    if called_name(call) != "exec" or not call.args:
        return False
    return len(call.args) == 1 or _is_namespace(call.args[1])


def _is_namespace(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and called_name(node) in _NAMESPACES


def _is_this_module(node: ast.expr, aliases: frozenset[str]) -> bool:
    """`sys.modules[...]` keyed by `__name__` or a literal, or a name bound to it.

    A literal key may name another module; treating it as this one only costs an
    "incomplete".
    """
    if isinstance(node, ast.Name):
        return node.id in aliases
    if not isinstance(node, ast.Subscript):
        return False
    if isinstance(node.slice, ast.Name):
        return node.slice.id == "__name__"
    return _is_modules_table(node.value) and string_value(node.slice) is not None


def _is_modules_table(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "modules"


def this_module_names(tree: ast.Module) -> frozenset[str]:
    """Names assigned from `sys.modules[__name__]`."""
    return frozenset(
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and _is_this_module(node.value, frozenset())
        for target in node.targets
        if isinstance(target, ast.Name)
    )
