"""What each name means at a module's top level, in source order (#758).

Only module scope binds here: an import inside a function says nothing about
what a class statement at the top sees. Where two branches bind one name, the
first (the `if` body, the `try` body) is tried first and the others kept as
alternates, except under `if TYPE_CHECKING`, where the `else` is the one that
runs.

archy:owns        Namespace, module_namespace
archy:mirrored-by _dotted -> archy.conventions_classes, archy.conventions_families,
                  archy.conventions_gates
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable
from functools import singledispatch

from pydantic import BaseModel, Field

# `(module, level, package) -> dotted base`, "" when it walks past the root.
RelativeResolver = Callable[[str | None, int, str], str]


def _dotted(node: ast.expr) -> str:
    """Render `a.b.c` / `a` from an expression, or "" for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


class Namespace(BaseModel):
    """Local name to the dotted thing it means; None when that cannot be followed."""

    names: dict[str, str | None] = Field(default_factory=dict)
    # Other things a branch could make the name mean, tried when the first
    # leads nowhere (a C extension with a Python fallback).
    alternates: dict[str, list[str]] = Field(default_factory=dict)
    # What a name meant before a `class` statement took it over.
    replaced: dict[str, str | None] = Field(default_factory=dict)
    # Modules taken `*` from, in order.
    stars: list[str] = Field(default_factory=list)


class _Scope(BaseModel):
    """Where a statement binds: the module, and whether an earlier branch has priority."""

    module: str
    package: str
    relative: RelativeResolver
    space: Namespace
    # Inside `if` or `try`, so an earlier binding may still be the one that runs.
    conditional: bool = False
    fill_only: bool = False

    def bind(self, name: str, target: str | None) -> None:
        names = self.space.names
        if self.conditional and name in names:
            kept, other = (names[name], target) if self.fill_only else (target, names[name])
            names[name] = kept
            if other is not None and other != kept:
                self.space.alternates.setdefault(name, []).append(other)
            return
        names[name] = target
        self.space.alternates.pop(name, None)

    def branch(self, *, fill_only: bool) -> _Scope:
        update = {"conditional": True, "fill_only": self.fill_only or fill_only}
        return self.model_copy(update=update)


def module_namespace(
    body: list[ast.stmt], module: str, *, is_package: bool, relative: RelativeResolver
) -> Namespace:
    parent = module.rsplit(".", 1)[0] if "." in module else ""
    package = module if is_package else parent
    scope = _Scope(module=module, package=package, relative=relative, space=Namespace())
    _bind_all(body, scope)
    return scope.space


def _bind_all(statements: Iterable[ast.stmt], scope: _Scope) -> None:
    for statement in statements:
        _bind(statement, scope)


@singledispatch
def _bind(node: ast.AST, scope: _Scope) -> None:
    """Statements that bind nothing a base could name."""


@_bind.register(ast.ClassDef)
def _bind_class(node: ast.ClassDef, scope: _Scope) -> None:
    if node.name in scope.space.names:
        scope.space.replaced.setdefault(node.name, scope.space.names[node.name])
    scope.bind(node.name, f"{scope.module}.{node.name}")


@_bind.register(ast.Import)
def _bind_import(node: ast.Import, scope: _Scope) -> None:
    for alias in node.names:
        top = alias.name.split(".")[0]
        scope.bind(alias.asname or top, alias.name if alias.asname else top)


@_bind.register(ast.ImportFrom)
def _bind_import_from(node: ast.ImportFrom, scope: _Scope) -> None:
    base = scope.relative(node.module, node.level, scope.package)
    for alias in node.names:
        if alias.name == "*":
            scope.space.stars.append(base)
        else:
            scope.bind(alias.asname or alias.name, f"{base}.{alias.name}" if base else None)


@_bind.register(ast.Assign)
@_bind.register(ast.AnnAssign)
def _bind_assignment(node: ast.Assign | ast.AnnAssign, scope: _Scope) -> None:
    if node.value is None:
        return
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    target = _aliased(node.value, scope.space)
    for name in (t.id for t in targets if isinstance(t, ast.Name)):
        # `Setting = Meta("Setting", (Setting,), {})` remakes the class in place.
        own = scope.space.names.get(name) == f"{scope.module}.{name}"
        if not (own and isinstance(node.value, ast.Call)):
            scope.bind(name, target)


def _aliased(value: ast.expr, space: Namespace) -> str | None:
    """`Base = errors.Error` follows; anything else, builtins included, does not."""
    head, _, rest = _dotted(value).partition(".")
    target = space.names.get(head)
    if target is None:
        return None
    return f"{target}.{rest}" if rest else target


@_bind.register(ast.If)
@_bind.register(ast.For)
@_bind.register(ast.While)
def _bind_branches(node: ast.If, scope: _Scope) -> None:
    # A loop, not recursion: a generated `elif` chain nests as deep as it is long.
    here = scope
    while True:
        first, other = node.body, node.orelse
        if _is_type_checking(node):
            first, other = other, first
        _bind_all(first, here.branch(fill_only=False))
        here = here.branch(fill_only=True)
        if len(other) != 1 or not isinstance(other[0], ast.If):
            _bind_all(other, here)
            return
        node = other[0]


def _is_type_checking(node: ast.AST) -> bool:
    test = getattr(node, "test", None)
    return test is not None and _dotted(test).rsplit(".", 1)[-1] == "TYPE_CHECKING"


@_bind.register(ast.With)
def _bind_with(node: ast.With, scope: _Scope) -> None:
    _bind_all(node.body, scope)


@_bind.register(ast.Try)
def _bind_try(node: ast.Try, scope: _Scope) -> None:
    _bind_all([*node.body, *node.orelse], scope.branch(fill_only=False))
    fallback = scope.branch(fill_only=True)
    for handler in node.handlers:
        _bind_all(handler.body, fallback)
    _bind_all(node.finalbody, scope)
