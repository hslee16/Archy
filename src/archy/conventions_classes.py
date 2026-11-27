"""Which class a name refers to, from where it is written (#758).

Two unrelated `Error` classes are two classes. A bare name says which one only
together with the module it appears in, so a class is `(module, name)` here and
every reference is resolved through that module's own imports. Not followed: a
submodule that exists as a package attribute only because a star import put it
there (`nx.reportviews.X`). The cost is a missing link, never a merged family.

archy:owns        ClassIndex
archy:mirrored-by ClassIndex -> archy.conventions, archy.conventions_docs,
                  archy.conventions_families
"""

from __future__ import annotations

import ast
from collections.abc import Iterable

from archy.conventions_collect import _dotted, _ModuleFacts

ClassKey = tuple[str, str]


class ClassIndex:
    """Every class the project defines, and what each module's names point to."""

    def __init__(self, facts: Iterable[_ModuleFacts]) -> None:
        self.nodes: dict[ClassKey, ast.ClassDef] = {}
        self._bound: dict[str, dict[str, str]] = {}
        # The modules each one takes `*` from.
        self._stars: dict[str, list[str]] = {}
        self._by_name: dict[str, set[ClassKey]] = {}
        for f in facts:
            self._bound[f.qualname] = _bindings(f)
            self._stars[f.qualname] = [base for base, names in f.import_froms if not names]
            for cls in f.classes:
                key = (f.qualname, cls.name)
                self.nodes.setdefault(key, cls)
                self._by_name.setdefault(cls.name, set()).add(key)

    def resolve(self, module: str, dotted: str) -> ClassKey | None:
        """The project class `dotted` means inside `module`, or None.

        None for a class from outside the project, and for a name that nothing
        says which of several classes it is: guessing is what merged them.
        """
        head, _, rest = dotted.partition(".")
        if not rest and (module, head) in self.nodes:
            return module, head
        target = self._bound.get(module, {}).get(head)
        if target is not None:
            return self._follow(f"{target}.{rest}" if rest else target, set())
        if rest:
            return self._follow(dotted, set())
        return self._from_stars(module, head, set()) or self._only(head)

    def base_of(self, module: str, cls: ast.ClassDef, base: ast.expr) -> ClassKey | None:
        """The project class a `class` statement's base names."""
        dotted = _dotted(base)
        if not dotted:
            return None
        if dotted == cls.name:
            # `class Error(Error)` derives from the imported one, never itself.
            target = self._bound.get(module, {}).get(dotted)
            return None if target is None else self._follow(target, set())
        return self.resolve(module, dotted)

    def _follow(self, full: str, seen: set[str]) -> ClassKey | None:
        """`pkg.mod.Name`, through any re-exports on the way to the definition."""
        if full in seen:
            return None
        seen.add(full)
        module, _, name = full.rpartition(".")
        if (module, name) in self.nodes:
            return module, name
        target = self._bound.get(module, {}).get(name)
        if target is not None:
            return self._follow(target, seen)
        return self._from_stars(module, name, seen)

    def _from_stars(self, module: str, name: str, seen: set[str]) -> ClassKey | None:
        """`name` as one of the modules `module` takes `*` from provides it."""
        found = (self._follow(f"{source}.{name}", seen) for source in self._stars.get(module, ()))
        return next((key for key in found if key is not None), None)

    def _only(self, name: str) -> ClassKey | None:
        """A name no import explains, if only one class has it."""
        found = self._by_name.get(name, set())
        return next(iter(found)) if len(found) == 1 else None


def _bindings(facts: _ModuleFacts) -> dict[str, str]:
    """Local name to the dotted thing an import statement bound it to."""
    bound = dict(facts.module_aliases)
    for base, names in facts.import_froms:
        bound.update({local: f"{base}.{name}" for name, local in names})
    return bound
