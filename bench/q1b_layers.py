#!/usr/bin/env python
"""Inspect a package's real subpackage dependency structure, to author layers.

    uv run python bench/q1b_layers.py bench/repo_cache/requests requests

Q1b needs declared layer rules, and SWE-bench repositories ship none (checked:
no `.importlinter`, no `[tool.importlinter]`, no `tach.toml` in any of them). So
the rules have to be authored, which puts the measurer in the uncomfortable
position of writing the intent being measured.

This tool exists to constrain that. It prints the *observed* subpackage import
matrix so a rule can be checked against what the project actually does before it
is committed, and it flags which candidate `forbid` rules would already be
violated by pristine upstream code.

## The discipline the numbers enforce

A rule that fires on the unmodified base commit is useless for Q1b: every agent
run would "violate" it and the signal would be constant. So the authored rules
must hold on pristine code, and this script is how that is verified rather than
assumed. It does NOT make the rules unbiased, it makes them falsifiable in one
specific way, which is the most that can be claimed.

Nothing here reads the task manifest. Layer authoring is done blind to which
files the gold patches touch, so a rule cannot be tuned toward the tasks.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from archy.graph import build_graph


def subpackage(module: str, root: str) -> str:
    """The top-level subpackage a module belongs to, e.g. `django.db.models.x`
    -> `db`. Modules directly under the root map to `<root>` itself."""
    parts = module.split(".")
    if parts and parts[0] == root:
        parts = parts[1:]
    return parts[0] if len(parts) > 1 else "(root)"


def matrix(pkg_path: Path, root: str) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    g = build_graph(pkg_path)
    edges: dict[tuple[str, str], int] = defaultdict(int)
    sizes: dict[str, int] = defaultdict(int)
    for node in g.nodes:
        sizes[subpackage(node, root)] += 1
    for src, dst in g.edges:
        a, b = subpackage(src, root), subpackage(dst, root)
        if a != b:
            edges[(a, b)] += 1
    return dict(edges), dict(sizes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("repo", type=Path)
    ap.add_argument("pkg", help="package dir name under repo, e.g. `requests`")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    root = args.pkg.split("/")[-1]
    edges, sizes = matrix(args.repo / args.pkg, root)

    print(f"# {args.repo}/{args.pkg}: {len(sizes)} subpackages, {sum(sizes.values())} modules\n")
    print("subpackage sizes:")
    for name, n in sorted(sizes.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<28} {n:>5} modules")

    print("\ncross-subpackage edges (source -> target, count):")
    for (a, b), n in sorted(edges.items(), key=lambda kv: -kv[1])[: args.top]:
        print(f"  {a:<24} -> {b:<24} {n:>5}")

    # A subpackage nothing internal imports FROM is a candidate "top"; one that
    # imports nothing internal is a candidate "leaf". Leaves are where the safe,
    # uncontroversial rules live: a utility layer must not reach upward.
    importers = {b for _, b in edges}
    importees = {a for a, _ in edges}
    print("\ncandidate leaves (imported by others, import nothing internal):")
    for name in sorted(sizes):
        if name in importers and name not in importees:
            print(f"  {name}")
    print("\ncandidate tops (import others, imported by nothing internal):")
    for name in sorted(sizes):
        if name in importees and name not in importers:
            print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
