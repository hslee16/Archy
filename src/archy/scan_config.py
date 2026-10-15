"""What archy.yaml says about the scan itself: which directories, which roots, how many modules.

The translation of a DISCOVERED archy.yaml's `exclude:`, `roots:` and
`max_modules:` into scan arguments, shared by the CLI and the MCP server so the
two surfaces cannot see different module sets (#395: they used to carry
byte-identical copies). The `check` paths still build the same arguments inline,
from a config they have already loaded (possibly an explicit `--config`); those
two sites are the remaining copies #395 tracks.

It lives in its own module, beside the two boundaries, rather than in
`archy.layers`: the translation needs `archy.graph`'s defaults, and making the
policy layer import the graph layer lengthened archy's longest import chain
from 7 to 9 for the sake of two small functions.

archy:owns        graph_kwargs, resolve_max_modules
archy:mirrored-by graph_kwargs -> archy.cli, archy.mcp,
                  resolve_max_modules -> archy.cli, archy.mcp
"""

from __future__ import annotations

from pathlib import Path

from archy.graph import DEFAULT_IGNORED_DIRS, effective_max_modules
from archy.layers import discover_config, load_config


def graph_kwargs(path: Path) -> dict:
    """`build_graph` / `discover_modules` kwargs from the archy.yaml governing `path`.

    Missing config means `{}`; malformed config is a real bug the user wants to
    see, so `LayerConfigError` propagates.
    """
    config_path = discover_config(path)
    if config_path is None:
        return {}
    config = load_config(config_path)
    return {
        "ignored_dirs": DEFAULT_IGNORED_DIRS | frozenset(config.exclude),
        "extra_roots": config.roots,
    }


def resolve_max_modules(path: Path) -> int | None:
    """The scan-size guard for `path`: archy.yaml's `max_modules`, else the default."""
    config_path = discover_config(path)
    configured = load_config(config_path).max_modules if config_path is not None else None
    return effective_max_modules(configured)
