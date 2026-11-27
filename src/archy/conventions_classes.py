"""Which class a name refers to, from where it is written (#758).

Two unrelated `Error` classes are two classes. A bare name says which one only
together with the module it appears in, so a class is `(module, name)` here and
every reference is resolved through that module's own namespace
(`conventions_namespace`).

A name nothing explains resolves to nothing: guessing is what merged them. The
known misses, each a link left out: a submodule that is a package attribute only
through a star import (`nx.reportviews.X`), and imports that spell a namespace
package archy names its modules without.

It reads one final namespace per module, so it can pick the wrong class where
order or scope decides: a star import after an explicit import of the same name,
a star source whose `__all__` hides it, a name rebound after the class statement
that used it, or one shadowed inside a class body.

archy:owns        ClassIndex
archy:mirrored-by ClassIndex -> archy.conventions, archy.conventions_docs,
                  archy.conventions_families
"""

from __future__ import annotations

import ast
import builtins
from collections.abc import Iterable
from typing import Protocol

from archy.conventions_namespace import Namespace, _dotted, module_namespace
from archy.graph import resolve_relative_import

ClassKey = tuple[str, str]
# The deepest re-export chain followed: longer than any real one, and short of
# the recursion limit.
_MAX_DEPTH = 50
_BUILTINS = frozenset(dir(builtins))


class _Facts(Protocol):
    """What is read of a collected module, stated here so that resolving names
    does not depend on the collector."""

    qualname: str
    is_package: bool
    body: list[ast.stmt]
    classes: list[ast.ClassDef]


def _relative(module: str | None, level: int, package: str) -> str:
    return resolve_relative_import(module, level, package) or ""


class ClassIndex:
    """Every class the project defines, and what each module's names point to."""

    def __init__(self, facts: Iterable[_Facts]) -> None:
        self.nodes: dict[ClassKey, ast.ClassDef] = {}
        self._spaces: dict[str, Namespace] = {}
        for f in facts:
            self._spaces[f.qualname] = module_namespace(
                f.body, f.qualname, is_package=f.is_package, relative=_relative
            )
            for cls in f.classes:
                self.nodes.setdefault((f.qualname, cls.name), cls)

    def resolve(self, module: str, dotted: str) -> ClassKey | None:
        """The project class `dotted` means inside `module`, or None.

        None for a class from outside the project and for a builtin.
        """
        head, _, rest = dotted.partition(".")
        space = self._spaces.get(module, Namespace())
        if head in space.names:
            targets = (space.names[head], *space.alternates.get(head, ()))
            found = (
                self._follow(f"{target}.{rest}" if rest else target, set())
                for target in targets
                if target is not None
            )
            return next((key for key in found if key is not None), None)
        if rest:
            return None
        return self._from_stars(module, head, set()) or self._nested(module, head)

    def base_of(self, module: str, cls: ast.ClassDef, base: ast.expr) -> ClassKey | None:
        """The project class a `class` statement's base names."""
        dotted = _dotted(base)
        if not dotted:
            return None
        if dotted != cls.name:
            return self.resolve(module, dotted)
        # `class Error(Error)` derives from what the name meant before it.
        space = self._spaces.get(module, Namespace())
        if dotted in space.replaced:
            target = space.replaced[dotted]
            return None if target is None else self._follow(target, set())
        return self._from_stars(module, dotted, set())

    def _follow(self, full: str, seen: set[str], depth: int = 0) -> ClassKey | None:
        """`pkg.mod.Name`, through any re-exports on the way to the definition.

        `seen` stops cycles and revisits across a star fan-out; `depth` bounds
        one chain, since a wide package visits far more modules than it nests.
        """
        if full in seen or depth >= _MAX_DEPTH:
            return None
        seen.add(full)
        module, _, name = full.rpartition(".")
        if module not in self._spaces:
            return self._member_of_class(module, name, seen, depth)
        names = self._spaces[module].names
        if name not in names:
            return self._from_stars(module, name, seen, depth)
        target = names[name]
        if target == full:
            return (module, name) if (module, name) in self.nodes else None
        return None if target is None else self._follow(target, seen, depth + 1)

    def _member_of_class(
        self, owner: str, name: str, seen: set[str], depth: int
    ) -> ClassKey | None:
        """`mod.Outer.Inner`: a class nested in a class."""
        found = self._follow(owner, seen, depth + 1) if "." in owner else None
        if found is None or (found[0], name) not in self.nodes:
            return None
        return found[0], name

    def _from_stars(
        self, module: str, name: str, seen: set[str], depth: int = 0
    ) -> ClassKey | None:
        """`name` as a module taken `*` from provides it; the last star wins."""
        stars = self._spaces.get(module, Namespace()).stars
        found = (self._follow(f"{source}.{name}", seen, depth + 1) for source in reversed(stars))
        return next((key for key in found if key is not None), None)

    def _nested(self, module: str, name: str) -> ClassKey | None:
        """A class defined in a function or class body of this module, which
        module scope does not name. Never in place of a builtin."""
        if name in _BUILTINS or (module, name) not in self.nodes:
            return None
        return module, name
