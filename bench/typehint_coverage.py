"""Bench experiment: type-hint coverage as a candidate 6th score axis.

For each project in `projects.yaml`, walks every `.py` file with
tree-sitter, identifies public functions and methods (name does not
start with `_` unless it's a dunder), and computes per-project annotation
coverage:

    coverage = sum_over_public_functions(annotated_positions)
             / sum_over_public_functions(total_positions)

A "position" is a parameter (excluding `self` / `cls`) or the return-type
slot. A position is "annotated" iff the parameter has a `: T` annotation
or the function has a `-> T` return-type annotation. `*args` / `**kwargs`
count as positions and are annotated iff they carry an explicit
annotation.

Also computes Pearson correlation of project coverage against archy's
five score axes (modularity, acyclicity, depth, equality, complexity)
to evaluate the orthogonality criterion the docs/research/AXIS_REVIEW.md
framework requires before any axis-promotion decision.

This is the empirical input to docs/research/AXIS_REVIEW.md's "next 6th-axis
candidate" recommendation. Output should drive the ship/no-ship
decision, not pre-suppose it.

Usage:
    uv run --with networkx --with pyyaml --with pydantic --with tree-sitter \
        --with tree-sitter-python python bench/typehint_coverage.py

archy:owns        ProjectCoverage, main
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import tree_sitter_python
from pydantic import BaseModel, ConfigDict
from tree_sitter import Language, Node, Parser

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import REPO_ROOT, clone_or_update, load_manifest, pearson

PY_LANG = Language(tree_sitter_python.language())
PARSER = Parser(PY_LANG)


class ProjectCoverage(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    public_functions: int
    annotated_positions: int
    total_positions: int
    coverage: float


def _is_public(name: str) -> bool:
    """True iff `name` looks like a public Python API.

    Convention: a leading single underscore means private. Dunders
    (`__init__`, `__repr__`, etc.) are part of the public API.
    Name-mangled private (`__private`) reads as private.
    """
    if not name.startswith("_"):
        return True
    return name.startswith("__") and name.endswith("__")


def _text(node: Node, source: bytes) -> str:
    """Decode a node's source span; central helper so the byte-slice idiom isn't repeated."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _name_of(func_node: Node, source: bytes) -> str:
    name_node = func_node.child_by_field_name("name")
    if name_node is None:
        return "<anon>"
    return _text(name_node, source)


def _count_function(func_node: Node, source: bytes) -> tuple[int, int] | None:
    """Return (annotated_positions, total_positions) for `func_node`, or None to skip.

    Returns None when the function should not count toward project
    coverage at all (private name). Counts include the return slot.
    """
    name = _name_of(func_node, source)
    if not _is_public(name):
        return None

    annotated = 0
    total = 0

    params_node = func_node.child_by_field_name("parameters")
    if params_node is not None:
        for param in params_node.named_children:
            # self / cls are method-receiver conventions and not part of
            # the typed surface; skip them entirely.
            if param.type == "identifier":
                ident = _text(param, source)
                if ident in ("self", "cls"):
                    continue
                total += 1
                continue
            if param.type == "default_parameter":
                # `x=1` - position is annotated iff the param has a type;
                # default_parameter has a `name` field but no type slot.
                total += 1
                continue
            if param.type in ("typed_parameter", "typed_default_parameter"):
                # typed_parameter / typed_default_parameter are the only param nodes
                # that carry a type, so these are the positions that count as annotated.
                annotated += 1
                total += 1
                continue
            if param.type == "list_splat_pattern":
                # bare `*args` carries no type, so it counts toward total but not annotated.
                total += 1
                continue
            if param.type == "dictionary_splat_pattern":
                # bare `**kwargs`, likewise: a position, but unannotated.
                total += 1
                continue
            if param.type == "typed_splat_pattern":
                # tree-sitter-python represents `*args: T` and `**kwargs: T`
                # differently across versions; handle defensively.
                annotated += 1
                total += 1
                continue
            # Fallback: count as a position; treat unrecognized as unannotated.
            total += 1

    # Return-type slot is always one position (even if the function
    # returns implicitly).
    total += 1
    if func_node.child_by_field_name("return_type") is not None:
        annotated += 1

    return annotated, total


def _walk(
    node: Node,
    source: bytes,
    out: list[tuple[int, int]],
    inside_function: bool,
) -> None:
    if node.type == "function_definition":
        # Nested functions are skipped entirely. They're usually closures
        # or local helpers, not part of the typed public surface.
        if inside_function:
            return
        result = _count_function(node, source)
        if result is not None:
            out.append(result)
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.named_children:
                _walk(child, source, out, inside_function=True)
        return
    if node.type == "class_definition":
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.named_children:
                _walk(child, source, out, inside_function=False)
        return
    for child in node.named_children:
        _walk(child, source, out, inside_function=inside_function)


def _walk_file(path: Path) -> list[tuple[int, int]]:
    try:
        source = path.read_bytes()
    except OSError:
        return []
    tree = PARSER.parse(source)
    out: list[tuple[int, int]] = []
    _walk(tree.root_node, source, out, inside_function=False)
    return out


def _project_coverage(name: str, src: Path) -> ProjectCoverage:
    annotated = 0
    total = 0
    n_funcs = 0
    for py in src.rglob("*.py"):
        # Skip the same noise the bench would skip: tests, dot-dirs,
        # vendored caches. Matches `loc()` in run.py.
        parts = set(py.parts)
        if any(p in parts for p in ("tests", "test", "_test", ".venv", "__pycache__")):
            continue
        for ann, tot in _walk_file(py):
            annotated += ann
            total += tot
            n_funcs += 1
    coverage = (annotated / total) if total else 0.0
    return ProjectCoverage(
        name=name,
        public_functions=n_funcs,
        annotated_positions=annotated,
        total_positions=total,
        coverage=coverage,
    )


def _archy_score(src: Path) -> dict:
    out = subprocess.check_output(
        ["uv", "run", "archy", "score", "--format", "json", str(src)],
        cwd=REPO_ROOT,
    )
    return json.loads(out)


def main() -> int:
    rows: list[dict] = []
    for proj in load_manifest():
        name = proj["name"]
        print(f"# {name}", file=sys.stderr)
        try:
            root = clone_or_update(proj)
            src = root / proj["src_dir"]
            if not src.exists():
                print(f"#   SRC MISSING: {src}", file=sys.stderr)
                continue
            cov = _project_coverage(name, src)
            score = _archy_score(src)
        except Exception as exc:
            print(f"#   SKIPPED ({type(exc).__name__}: {exc})", file=sys.stderr)
            continue
        comp = score["components"]
        rows.append(
            {
                "name": name,
                "public_functions": cov.public_functions,
                "annotated": cov.annotated_positions,
                "total": cov.total_positions,
                "coverage": cov.coverage,
                "modularity": comp["modularity"],
                "acyclicity": comp["acyclicity"],
                "depth": comp["depth"],
                "equality": comp["equality"],
                "complexity": comp["complexity"],
            }
        )

    rows.sort(key=lambda r: -r["coverage"])

    print("# Type-hint coverage on the 27-project bench\n")
    print(
        "Coverage = annotated_positions / total_positions over public "
        "functions. A position is a parameter (excluding self/cls) or the "
        "return slot.\n"
    )
    print("## Per-project coverage\n")
    print("| project | public functions | annotated | total | coverage |")
    print("| --- | ---: | ---: | ---: | ---: |")
    for r in rows:
        print(
            f"| {r['name']} | {r['public_functions']:,} | "
            f"{r['annotated']:,} | {r['total']:,} | {r['coverage']:.3f} |"
        )

    covs = [r["coverage"] for r in rows]
    sorted_c = sorted(covs)
    n = len(sorted_c)
    print(
        f"\nN = {n}; min = {min(covs):.3f}; median = {sorted_c[n // 2]:.3f}; "
        f"max = {max(covs):.3f}; mean = {sum(covs) / n:.3f}.\n"
    )

    print("## Pearson r against the existing 5 axes\n")
    print("Lower absolute value = more orthogonal. OECD redundancy threshold = |r| > 0.7.\n")
    print("| axis | r vs coverage |")
    print("| --- | ---: |")
    for axis in ("modularity", "acyclicity", "depth", "equality", "complexity"):
        vals = [r[axis] for r in rows]
        print(f"| {axis} | {pearson(covs, vals):+.3f} |")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
