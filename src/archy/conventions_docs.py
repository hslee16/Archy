"""What a project's documentation names, and which public family members it leaves out."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path

from archy.conventions_collect import _ModuleFacts
from archy.conventions_models import BaseFamily, DocGap, ExportGap, RegistryEntry

# Directive targets: reStructuredText `.. autoclass:: Thing` and the
# mkdocstrings `::: pkg.Thing` form. Both name a symbol unambiguously.
_DOC_DIRECTIVE = re.compile(
    r"^\s*(?:\.\.\s+(?:auto)?(?:class|exception|function|data|attribute|method|module)::"
    r"|:::)\s*([\w.]+)",
    re.M,
)


# Anything inside a code span. Deliberately generous: see DocGap's docstring.
_DOC_CODE_SPAN = re.compile(r"`{1,2}\s*([A-Za-z_][\w.-]*)\s*`{1,2}")


# Bare lowercase-hyphenated slugs anywhere in the prose. Strictness is scaled
# to how ambiguous a token is: `Path` or `Command` in a sentence says nothing,
# so identifiers must appear in a code span or a directive, but `arg-type` is
# not an English word and a bare-word match is safe. Without this, `mypy` --
# which documents each of its 79 codes as `[arg-type]` in a section heading --
# reads as documenting 13 of them.
_DOC_SLUG = re.compile(r"(?<![\w-])([a-z][a-z0-9]*(?:-[a-z0-9]+)+)(?![\w-])")


# A bracketed token is markup, not prose, so an ambiguous single word is safe
# inside one. `mypy` heads each section `Check argument types [arg-type]`, and
# six of its codes are ordinary English words -- `override`, `abstract`,
# `syntax` -- which no hyphen rule can reach and which were being reported as
# undocumented while their own section headings named them.
_DOC_BRACKET = re.compile(r"\[([A-Za-z_][\w.-]*)\]")


_DOC_SUFFIXES = (".rst", ".md")


_DOC_MAX_BYTES = 2_000_000


# A partial-coverage gap is only worth reporting where the surface already
# governs most of the family. One member mentioned or exported in passing is
# not a promise about the rest, so `_doc_gaps` and `_export_gaps` share this
# floor rather than each carrying its own copy of the same policy.
_PARTIAL_COVERAGE_FLOOR = 0.5


def _is_partial_coverage(covered: int, total: int, *, min_count: int) -> bool:
    """Is this family covered enough to be worth reporting a gap on, and not fully?

    Three conditions, and `_doc_gaps` and `_export_gaps` need all three to agree
    (#415/#416): enough members covered to be a promise rather than a passing
    mention, not already complete (a complete family has no gap), and above the
    shared floor. They already shared the constant, which is what made the drift
    risk real: two copies of the arithmetic could disagree about how the one
    number is applied while still looking governed by it.

    `min_count` is the only part that differs: 2 for exports, `min_documented`
    for docs.
    """
    if covered < min_count or covered == total:
        return False
    return covered / total >= _PARTIAL_COVERAGE_FLOOR


def _harvest_docs(root: Path, ignored_dirs: frozenset[str]) -> tuple[dict[str, set[str]], int]:
    """Identifiers named in each documentation directory, and the file count.

    Keyed on the top-level directory so the report can say *where* a family
    is documented; a project with `docs/` and a separate `website/` should
    not have a gap in one hidden by coverage in the other.
    """
    found: dict[str, set[str]] = defaultdict(set)
    seen = 0
    for path, rel in _doc_files(root, ignored_dirs):
        text = _read_doc(path)
        if text is None:
            continue
        seen += 1
        key = rel.parts[0] if len(rel.parts) > 1 else "."
        found[key] |= _doc_names(text)
    return dict(found), seen


def _doc_files(root: Path, ignored_dirs: frozenset[str]) -> Iterator[tuple[Path, Path]]:
    """Documentation files under `root` as (path, path relative to root), skipping ignored dirs."""
    for path in sorted(root.rglob("*")):
        if path.suffix not in _DOC_SUFFIXES or not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in ignored_dirs or part.startswith(".") for part in rel.parts[:-1]):
            continue
        yield path, rel


def _read_doc(path: Path) -> str | None:
    """The file's text, or None when it is too large or cannot be read."""
    try:
        if path.stat().st_size > _DOC_MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _doc_names(text: str) -> set[str]:
    """Every identifier a documentation file names, by directive, code span, slug or bracket."""
    names: set[str] = set()
    for match in _DOC_DIRECTIVE.finditer(text):
        names.add(match.group(1).rsplit(".", 1)[-1])
    for match in _DOC_CODE_SPAN.finditer(text):
        names.add(match.group(1).rsplit(".", 1)[-1])
    for match in _DOC_SLUG.finditer(text):
        names.add(match.group(1))
    for match in _DOC_BRACKET.finditer(text):
        names.add(match.group(1).rsplit(".", 1)[-1])
    return names


def _doc_groups(
    families: tuple[BaseFamily, ...], registries: tuple[RegistryEntry, ...]
) -> list[tuple[str, tuple[str, ...]]]:
    groups: list[tuple[str, tuple[str, ...]]] = [(f.base, f.members) for f in families]
    # A registry's values are documented by the string they are declared with,
    # not by the constant that holds them: `mypy` documents `arg-type`, and
    # splits its codes across two files by whether they are on by default, so
    # for that project the docs *are* the gate convention.
    groups += [(f"{r.constructor}(...)", r.literal_names) for r in registries if r.literal_names]
    return groups


def _best_doc_gap(
    name: str, public: tuple[str, ...], docs: dict[str, set[str]], min_documented: int
) -> DocGap | None:
    # One row per family, from the directory that documents it best. A
    # project with a `docs/` tree and a README covers most families in
    # both, and reporting each twice buries the real gaps under near
    # duplicates that differ only in which stray mention they caught.
    best: DocGap | None = None
    for doc_root, names in sorted(docs.items()):
        documented = [m for m in public if m in names]
        if not _is_partial_coverage(len(documented), len(public), min_count=min_documented):
            continue
        if best is None or len(documented) > best.documented:
            best = DocGap(
                doc_root=doc_root,
                family=name,
                documented=len(documented),
                defined=len(public),
                missing=tuple(sorted(set(public) - names)),
            )
    return best


def _doc_gaps(
    families: tuple[BaseFamily, ...],
    registries: tuple[RegistryEntry, ...],
    docs: dict[str, set[str]],
    *,
    min_documented: int = 2,
) -> tuple[DocGap, ...]:
    """Public family members a documentation directory does not name."""
    out: list[DocGap] = []
    for name, members in _doc_groups(families, registries):
        # Private names are not a documentation defect; a project is entitled
        # to leave `_PydanticGeneralMetadata` out of its docs on purpose.
        public = tuple(m for m in members if not m.startswith("_"))
        if len(public) < min_documented:
            continue
        best = _best_doc_gap(name, public, docs, min_documented)
        if best is not None:
            out.append(best)
    out.sort(key=lambda g: (-(g.defined - g.documented), g.doc_root, g.family))
    return tuple(out)


def _export_gaps(
    facts: Iterable[_ModuleFacts], families: tuple[BaseFamily, ...]
) -> tuple[ExportGap, ...]:
    """Family members missing from an ``__all__`` that already lists their siblings."""
    exports = {f.qualname: f.exports for f in facts if f.exports is not None}
    if not exports:
        return ()
    out: list[ExportGap] = []
    for family in families:
        for module, names in exports.items():
            listed = [m for m in family.members if m in names]
            if not _is_partial_coverage(len(listed), len(family.members), min_count=2):
                continue
            out.append(
                ExportGap(
                    export_module=module,
                    family=family.base,
                    exported=len(listed),
                    defined=len(family.members),
                    missing=tuple(sorted(set(family.members) - set(names))),
                )
            )
    out.sort(key=lambda g: (-(g.defined - g.exported), g.export_module))
    return tuple(out)
