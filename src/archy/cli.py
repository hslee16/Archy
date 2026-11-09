"""Click-based command-line interface for archy.

The commands live in the `cli_*` modules, grouped by what they do; importing them
here registers each one on the `main` group from `archy.cli_app`. This file is the
`archy` console-script entry point (`archy.cli:main`) and keeps the names other
code has always imported from it.
"""

from __future__ import annotations

import sys

from archy import (  # noqa: F401 - importing each module registers its commands on `main`
    cli_check,
    cli_conventions,
    cli_git,
    cli_graph,
    cli_history,
    cli_install,
)
from archy.cli_app import main
from archy.cli_history import _parse_edge_spec
from archy.cli_render import (
    _coupling_to_text,
    _diff_to_text,
    _duplicates_to_text,
    _hotspots_to_text,
    _simulate_to_text,
)

__all__ = [
    "_coupling_to_text",
    "_diff_to_text",
    "_duplicates_to_text",
    "_hotspots_to_text",
    "_parse_edge_spec",
    "_simulate_to_text",
    "main",
]

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
