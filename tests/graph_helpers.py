"""Builders and helpers shared by the `tests/test_graph_*.py` modules."""

from __future__ import annotations

from pathlib import Path

import networkx as nx


def _mkpkg(parent: Path, name: str, init_text: str = "") -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "__init__.py").write_text(init_text)
    return d


def _internal_nodes(g: nx.DiGraph) -> set[str]:
    return {n for n, d in g.nodes(data=True) if not d.get("external")}


def _edge(data: dict, src: str, dst: str) -> dict:
    return next(e for e in data["edges"] if e["source"] == src and e["target"] == dst)


def _tiny_pkg_project(root: Path) -> Path:
    """pkg/ with a.py importing b.py. Returns the project root."""
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg import b\n")
    (pkg / "b.py").write_text("x = 1\n")
    return root


def _write_flat_package(root: Path, name: str, n_modules: int) -> None:
    """Create `root/<name>/` with `n_modules` import-free leaf modules."""
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    for i in range(n_modules):
        (pkg / f"mod{i}.py").write_text("x = 1\n")


def _src_layout_with_cycle(tmp_path: Path, *, decoy: bool) -> Path:
    """src/mypkg/core/db.py and src/mypkg/api/views.py import each other.

    With `decoy`, an empty repo-root `benchmarks/` package sorts before `src`.
    """
    pkg = tmp_path / "src" / "mypkg"
    for sub in ("core", "api"):
        (pkg / sub).mkdir(parents=True)
        (pkg / sub / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "core" / "db.py").write_text("from mypkg.api import views\n")
    (pkg / "api" / "views.py").write_text("from mypkg.core import db\n")
    if decoy:
        (tmp_path / "benchmarks").mkdir()
        (tmp_path / "benchmarks" / "__init__.py").write_text("")
    return tmp_path


_KNOWN = ("pkg.score", "pkg.scorer_utils", "pkg.cli", "tests.test_cli", "pkg.dsm")


def _src_layout(tmp_path: Path) -> Path:
    """`src/` is a package (has __init__.py) but the code imports `infra.*`, not `src.infra.*`."""
    (tmp_path / "src" / "infra").mkdir(parents=True)
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "src" / "infra" / "__init__.py").write_text("")
    (tmp_path / "src" / "infra" / "cfg.py").write_text("def helper() -> int:\n    return 1\n")
    return tmp_path
