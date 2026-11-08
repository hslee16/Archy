#!/usr/bin/env python
"""Diff archy's file-level import graph against `ruff analyze graph` (#532).

    uv run python bench/ruff_differential.py                 # corpus clones
    uv run python bench/ruff_differential.py --repo PATH ... # explicit trees

Two engines that share no code build an import graph of the same tree. Where
they disagree, one of them has a resolver bug, the other has a by-design
difference, or the tree needs `roots:`. A green suite never found the first
kind: on one corpus repo archy saw 27 internal edges to ruff's 911 (the
sibling-root shadowing bug).

This is a triage tool, never a CI gate. `ruff analyze graph` is experimental
and changes between releases, so the ruff version is recorded in the output and
no figure from one run should be compared with another run's.

## Method

- archy: `archy graph . --format json --internal-only`, node ids mapped to
  repo-relative paths through each node's `path`.
- ruff: `ruff analyze graph .` run FROM the repo root (an absolute path
  argument returns `{}` under a parent `.gitignore`).
- Both reduced to a set of (source file, target file) pairs, self edges dropped.
- A one-sided edge is classified against a small allow-list of by-design
  divergences, each with its reason. Anything unexplained is a candidate bug:
  file it as its own ticket, do not widen the allow-list to hide it.

The allow-list is deliberately narrow and structural. It never looks at counts,
so an engine that drops most of a repo's edges cannot be explained away by it.

A repo that fails to build on either side is recorded and the loop continues.
There is no checkpointing: a pass is minutes, not hours.

archy:owns        ArchyRefused, classify, clone_corpus, compare, load_corpus, main,
                  run_archy, run_repo, run_ruff
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "bench/contract_repos.json"
# Its own directory: `contract_prevalence.py` reuses any existing clone and needs
# full history, so shallow clones here would silently corrupt that bench.
CLONES = REPO_ROOT / "bench/cache/ruff_differential_clones"
MAX_KB = 100_000

# A one-sided gap above this ratio must be allow-listed or filed (#532).
GAP_RATIO = 2.0

Edge = tuple[str, str]


class ArchyRefused(Exception):
    """archy declined the tree on purpose; not a divergence to triage."""


EXPLAINED = {
    "init_targeting": (
        "ruff points an import at a package's `__init__.py` where archy points at "
        "the submodule it names (or the reverse); the same source reaches the same "
        "package in both graphs"
    ),
    "implicit_init": (
        "archy reports an edge from a module to an ancestor package's `__init__.py` "
        "(for instance `from pkg import Name` inside `pkg`, where `Name` is a symbol "
        "rather than a submodule); ruff reports no such edge"
    ),
    "script_sibling": (
        "a script in a directory with no `__init__.py` imports a sibling file by "
        "bare name (`from _common import x`); archy skips top-level siblings"
    ),
}


def _dir(path: str) -> str:
    return str(PurePosixPath(path).parent)


def _is_init(path: str) -> bool:
    return PurePosixPath(path).name == "__init__.py"


def _within(path: str, directory: str) -> bool:
    """Whether `path` sits at or below `directory` (whole components, not a prefix)."""
    return directory == "." or path.startswith(directory + "/")


def compare(archy: set[Edge], ruff: set[Edge]) -> tuple[set[Edge], set[Edge], set[Edge]]:
    """(both, only_ruff, only_archy), self edges already excluded by callers."""
    return archy & ruff, ruff - archy, archy - ruff


def classify(edge: Edge, side: str, other: set[Edge], tree_files: set[str]) -> str | None:
    """The allow-list reason for a one-sided edge, or None when unexplained.

    `side` is the engine that REPORTED the edge ("ruff" or "archy"); `other` is
    the other engine's full edge set; `tree_files` is every file either engine
    saw, used to tell whether a directory is a package.
    """
    source, target = edge
    package = _dir(target)
    if side == "archy" and _is_init(target) and _within(source, package):
        # `from pkg import Name` inside pkg resolves to the package; the edge is to an ancestor.
        return "implicit_init"
    if _is_init(target):
        if any(s == source and _within(t, package) for s, t in other):
            return "init_targeting"
    elif any(s == source and _is_init(t) and _dir(t) == package for s, t in other):
        return "init_targeting"
    if (
        side == "ruff"
        and _dir(source) == _dir(target)
        and not _is_init(target)
        and f"{_dir(source)}/__init__.py" not in tree_files
    ):
        return "script_sibling"
    return None


def run_archy(tree: Path) -> set[Edge]:
    """Internal file-level edges from archy's CLI, as repo-relative path pairs."""
    done = subprocess.run(
        [
            str(Path(sys.executable).parent / "archy"),
            "graph",
            ".",
            "--format",
            "json",
            "--internal-only",
        ],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode:
        # #531/#850: two files mapping to one qualname is a deliberate refusal.
        if "Run archy on each member separately" in done.stderr:
            raise ArchyRefused(done.stderr.strip().splitlines()[0])
        raise RuntimeError(done.stderr.strip()[-300:])
    data = json.loads(done.stdout)
    root = tree.resolve()
    path_of = {}
    for node in data["nodes"]:
        if node.get("path") and not node.get("external"):
            path_of[node["id"]] = Path(node["path"]).resolve().relative_to(root).as_posix()
    edges = {
        (path_of[e["source"]], path_of[e["target"]])
        for e in data["edges"]
        if e["source"] in path_of and e["target"] in path_of
    }
    return {e for e in edges if e[0] != e[1]}


def run_ruff(tree: Path) -> tuple[set[Edge], str]:
    """Edges from `ruff analyze graph`, plus the ruff version that produced them."""
    version = subprocess.run(
        [sys.executable, "-m", "ruff", "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    out = subprocess.run(
        [sys.executable, "-m", "ruff", "analyze", "graph", "."],
        cwd=tree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    data = json.loads(out)
    # ruff resolves some targets (pytest `conftest`) to an ABSOLUTE path outside the
    # tree, here archy's own checkout, because the clones live under it. That is
    # a property of where the clone sits, not of either resolver: drop it.
    edges = {
        (PurePosixPath(src).as_posix(), PurePosixPath(dst).as_posix())
        for src, dsts in data.items()
        for dst in dsts
        if not PurePosixPath(dst).is_absolute()
    }
    return {e for e in edges if e[0] != e[1]}, version


def run_repo(tree: Path) -> dict:
    """One row: counts, per-reason explained gaps, and the unexplained edges."""
    archy = run_archy(tree)
    ruff, version = run_ruff(tree)
    both, only_ruff, only_archy = compare(archy, ruff)
    files = {p for e in archy | ruff for p in e}
    row: dict = {
        "repo": tree.resolve().name,
        "ruff_version": version,
        "archy_edges": len(archy),
        "ruff_edges": len(ruff),
        "both": len(both),
        "explained": {},
        "unexplained_ruff_only": [],
        "unexplained_archy_only": [],
    }
    for side, edges, other in (("ruff", only_ruff, archy), ("archy", only_archy, ruff)):
        for edge in sorted(edges):
            reason = classify(edge, side, other, files)
            if reason:
                row["explained"][reason] = row["explained"].get(reason, 0) + 1
            else:
                row[f"unexplained_{side}_only"].append(list(edge))
    ratio = max(len(ruff), 1) / max(len(archy), 1)
    row["ruff_over_archy"] = round(ratio, 2)
    row["flagged"] = ratio > GAP_RATIO or ratio < 1 / GAP_RATIO
    return row


def clone_corpus() -> None:
    """Depth-1 clone of every corpus repo under MAX_KB; existing trees are kept."""
    CLONES.mkdir(parents=True, exist_ok=True)
    for entry in json.loads(CORPUS.read_text())["repos"]:
        target = CLONES / entry["repo"].replace("/", "__")
        if target.exists() or entry["size_kb"] > MAX_KB:
            continue
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--depth",
                "1",
                f"https://github.com/{entry['repo']}.git",
                str(target),
            ],
            capture_output=True,
        )


def load_corpus() -> list[Path]:
    """Corpus clones already on disk; fetching is `--clone`."""
    names = [r["repo"].replace("/", "__") for r in json.loads(CORPUS.read_text())["repos"]]
    return [CLONES / n for n in names if (CLONES / n).is_dir()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, action="append", help="tree to compare")
    parser.add_argument("--json", action="store_true", help="emit rows as JSON lines")
    parser.add_argument("--clone", action="store_true", help="fetch the corpus first")
    args = parser.parse_args(argv)
    if args.clone:
        clone_corpus()
    trees = args.repo or load_corpus()
    if not trees:
        print(f"no trees: pass --repo, or clone the corpus into {CLONES}", file=sys.stderr)
        return 2
    failed = 0
    for tree in trees:
        try:
            row = run_repo(tree)
        except ArchyRefused as exc:
            row = {"repo": tree.resolve().name, "refused": str(exc)[:300]}
        except Exception as exc:  # one bad repo must not end the pass
            failed += 1
            row = {"repo": tree.resolve().name, "error": f"{type(exc).__name__}: {exc}"[:300]}
        print(json.dumps(row) if args.json else _text(row))
    return 1 if failed == len(trees) else 0


def _text(row: dict) -> str:
    if "refused" in row:
        return f"{row['repo']}: REFUSED by archy ({row['refused']})"
    if "error" in row:
        return f"{row['repo']}: ERROR {row['error']}"
    flag = " FLAGGED" if row["flagged"] else ""
    return (
        f"{row['repo']}: archy={row['archy_edges']} ruff={row['ruff_edges']} "
        f"both={row['both']} explained={row['explained']} "
        f"unexplained(ruff/archy)={len(row['unexplained_ruff_only'])}/"
        f"{len(row['unexplained_archy_only'])}{flag}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
