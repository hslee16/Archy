"""Derived module headers: put the fact at the point of reading (#428).

🔴 THIS EXISTS BECAUSE OF A MEASUREMENT, NOT A HUNCH. Over 132 agent
transcripts on a pinned tree, `read` fired in 132/132 runs with a median of 9.5
calls BEFORE the first edit, while `archy` fired in 84/132 with a median of
**zero** before it. On `archy-02` alone the model ran archy 22.5 times per cell
and essentially all of it after the design decision was already made. Every
archy surface is pull, and three separate pushes measured against that corpus
returned 0 of 89, 0 of 24 and 0 of 38.

`read` is the only channel that fires in the phase where the gaps are, and archy
does not own that tool. It owns what is in the file.

**Every field is DERIVED, never hand-authored.** That is the whole design and
the only thing that makes the intervention measurable: if the text comes from
the tree, what changed is *delivery* (the fact moved to where it is read) and
not *content* (a new fact the model could not otherwise have had). A
hand-written header would test a different question, and would rot, which is
what `--check` exists to prevent.

What a header does NOT claim: that any of this helps. The arm that answers that
runs on the local rig, and a prior seeding arm on this card set LOST to its
control. `docs/WHAT_DIDNT_WORK.md` gets the result either way.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from archy.conventions import ConventionsReport
from archy.graph import Module

MARKER = "archy:"

# Field order is the order a reader needs them, not alphabetical: what does this
# module own, what moves with it, and does a finding here fail a build.
_FIELDS = ("owns", "mirrored-by", "gates")


class ModuleHeader(BaseModel):
    """One module's derived header, before it is rendered or written."""

    model_config = ConfigDict(frozen=True)

    module: str
    path: str
    owns: tuple[str, ...] = ()
    mirrored_by: tuple[str, ...] = ()
    gates: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        """Nothing derived means nothing to say.

        A header emitted for every module including the ones with no owned
        symbols, no mirrors and no gates is the "line printed on every clean
        run" failure: a reader learns to skip the block, and then skips it on
        the module where it mattered.
        """
        return not (self.owns or self.mirrored_by or self.gates)


def _public_top_level(source: str) -> tuple[str, ...]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    out = [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and not node.name.startswith("_")
    ]
    return tuple(sorted(out))


def compute_headers(
    report: ConventionsReport, root: Path, modules: Iterable[Module]
) -> tuple[ModuleHeader, ...]:
    """Derive one header per scanned module from what `conventions` already computed.

    Takes the report rather than recomputing it, so the header can never
    disagree with what `archy conventions` prints: one census, two renderings.

    `modules` comes from `discover_modules`, which is the only thing that knows
    where a dotted name actually lives. Reconstructing the path from the name
    was the first cut and it was wrong twice over: it cannot see a `src/` layout,
    and an empty name resolved to the project root.
    """
    # `discover_modules` returns absolute paths while `root` is whatever the
    # user typed, commonly `.`, so the two have to be reconciled before either
    # is used as a key or printed as a relative path.
    root = root.resolve()
    paths = {m.qualname: m.path.resolve() for m in modules}
    by_module: dict[str, list[str]] = {}
    for family in report.surfaces:
        # `consumer` is the shape that matters here: one definition several
        # modules import. `helper` and `mirrored` families are within or across
        # modules that already move together by name.
        if family.kind == "consumer":
            by_module.setdefault(family.module, []).append(
                f"{family.stem} -> {', '.join(family.surfaces)}"
            )
        elif family.kind == "mirrored":
            for surface in family.surfaces:
                by_module.setdefault(surface, []).append(
                    f"{family.stem} also in {', '.join(s for s in family.surfaces if s != surface)}"
                )

    gates_by_module: dict[str, list[str]] = {}
    for gate in report.gates:
        label = f"exit {gate.code}" if gate.code is not None else "non-zero exit"
        if gate.control:
            label += f" ({gate.control})"
        gates_by_module.setdefault(gate.module, []).append(label)

    headers: list[ModuleHeader] = []
    for module in sorted({*by_module, *gates_by_module, *paths}):
        path = paths.get(module)
        if path is None or not path.is_file():
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        headers.append(
            ModuleHeader(
                module=module,
                path=str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
                owns=_public_top_level(source),
                mirrored_by=tuple(sorted(set(by_module.get(module, ())))),
                gates=tuple(sorted(set(gates_by_module.get(module, ())))),
            )
        )
    return tuple(h for h in headers if not h.is_empty())


def render_header(header: ModuleHeader, *, width: int = 88) -> str:
    """The block as it appears in the file, without the docstring quotes.

    Fixed-width label column so the three fields line up under each other: this
    is read in a source file next to code, not in a terminal.
    """
    label = max(len(f) for f in _FIELDS) + len(MARKER) + 1
    lines: list[str] = []
    for field, values in (
        ("owns", header.owns),
        ("mirrored-by", header.mirrored_by),
        ("gates", header.gates),
    ):
        if not values:
            continue
        head = f"{MARKER}{field}".ljust(label)
        body = ", ".join(values)
        # Wrap continuations under the value column, never under the label, so
        # a long list still reads as one field.
        indent = " " * label
        while len(head) + len(body) > width and ", " in body:
            cut = body.rfind(", ", 0, width - len(head))
            if cut <= 0:
                break
            lines.append(f"{head}{body[:cut]},")
            head = indent
            body = body[cut + 2 :]
        lines.append(f"{head}{body}")
    return "\n".join(lines)


def _docstring_span(source: str) -> tuple[int, int] | None:
    """Character span of the module docstring's body, or None if there is none."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    if not tree.body:
        return None
    first = tree.body[0]
    if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)):
        return None
    if not isinstance(first.value.value, str):
        return None
    lines = source.splitlines(keepends=True)
    start = sum(len(x) for x in lines[: first.lineno - 1])
    end = sum(len(x) for x in lines[: (first.end_lineno or first.lineno)])
    return start, end


def existing_block(source: str) -> str | None:
    """The archy block already in this file's docstring, if any.

    Reads the docstring VALUE through the AST rather than slicing the raw text.
    Slicing looked equivalent and was not: on a module whose docstring is only
    the block, the opening quotes sit on the same line as the first field, so a
    line filter for the marker missed it and `--check` called a file stale
    immediately after writing it.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    doc = ast.get_docstring(tree, clean=False)
    if doc is None:
        return None
    kept = [line.strip() for line in doc.splitlines() if line.strip().startswith(MARKER)]
    return "\n".join(kept) if kept else None


def apply_header(source: str, block: str) -> str:
    """Put `block` at the end of the module docstring, replacing any block already there.

    Appending to the existing docstring rather than replacing it: the prose a
    human wrote about the module is the part archy cannot derive, and a
    generator that deletes it would be trading the fact it can compute for one
    it cannot.
    """
    span = _docstring_span(source)
    if span is None:
        # No docstring at all: give the module one that is only the block.
        return f'"""{block}\n"""\n\n{source}' if source.strip() else f'"""{block}\n"""\n'

    doc = source[span[0] : span[1]]
    quote = '"""' if '"""' in doc else "'''"
    body = doc.split(quote, 2)
    if len(body) < 3:
        return source
    prose_lines = [line for line in body[1].splitlines() if not line.strip().startswith(MARKER)]
    while prose_lines and not prose_lines[-1].strip():
        prose_lines.pop()
    prose = "\n".join(prose_lines)
    rebuilt = f"{body[0]}{quote}{prose}\n\n{block}\n{quote}{body[2]}"
    return source[: span[0]] + rebuilt + source[span[1] :]
