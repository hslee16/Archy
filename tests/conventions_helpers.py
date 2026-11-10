"""Builders and helpers shared by the `tests/test_conventions_*.py` modules."""

from __future__ import annotations

import tempfile
from pathlib import Path

from archy.conventions import (
    ConventionsReport,
    NamingFamily,
)


def _families(report: ConventionsReport) -> list[NamingFamily]:
    """Flatten the by-home-module naming report back to a list of families."""
    return [family for home in report.naming for family in home.families]


def _tmp(source: str) -> Path:
    """One module in a throwaway package, for cases reduced from a real project."""
    root = Path(tempfile.mkdtemp())
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(source)
    return root


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for name, source in files.items():
        target = pkg / name
        # A key may name a subpackage path (`sub/__init__.py`), which the
        # module-view cases need and a flat write cannot create.
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    return tmp_path


def _new_project(files: dict[str, str]) -> Path:
    """`_project` in a scratch directory, for cases that do not take `tmp_path`."""
    return _project(Path(tempfile.mkdtemp()), files)


_TIED_FAMILY = {
    "base.py": "class Widget:\n    pass\n",
    "zeta.py": (
        "from pkg.base import Widget\n"
        "class AlphaWidget(Widget): pass\n"
        "class BravoWidget(Widget): pass\n"
    ),
    "alpha.py": (
        "from pkg.base import Widget\n"
        "class CharlieWidget(Widget): pass\n"
        "class DeltaWidget(Widget): pass\n"
    ),
}


_HOME_UNDER_SEED = """
import sys
from pathlib import Path
from archy.conventions import compute_conventions
report = compute_conventions(Path(sys.argv[1]))
family = next(b for b in report.bases if b.base == "Widget")
print(family.home_module, family.home_count)
"""


def _documented_project(tmp_path: Path, doc: str) -> Path:
    root = _project(
        tmp_path,
        {
            "errors.py": (
                "class Base(Exception): pass\n"
                "class First(Base): pass\n"
                "class Second(Base): pass\n"
                "class Undocumented(Base): pass\n"
            )
        },
    )
    docs = root / "docs"
    docs.mkdir()
    (docs / "api.rst").write_text(doc)
    return root
