"""Builders and helpers shared by the `tests/test_layers_*.py` modules."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from archy.layers import (
    LayerConfig,
    load_config,
)


def _g(*edges: tuple[str, str, tuple[int, ...]]) -> nx.DiGraph:
    g: nx.DiGraph = nx.DiGraph()
    for u, v, lines in edges:
        g.add_edge(u, v, lines=lines)
    return g


def _cfg(tmp_path: Path, body: str) -> LayerConfig:
    path = tmp_path / "archy.yaml"
    path.write_text(body)
    return load_config(path)


def _reach_graph(*edges: tuple[str, str], nodes: tuple[str, ...] = ()) -> nx.DiGraph:
    """A graph whose nodes carry the `external=False` marker real scans set."""
    g: nx.DiGraph = nx.DiGraph()
    for node in nodes:
        g.add_node(node, external=False)
    for u, v in edges:
        g.add_node(u, external=False)
        g.add_node(v, external=False)
        g.add_edge(u, v, lines=(1,))
    return g


_REGISTRY_CONFIG = (
    "layers: {}\nforbid: []\n"
    "required:\n"
    "  - source: 'commands.**'\n"
    "    must_reach: core.database.model_registry\n"
    "    reason: standalone entrypoints need the full mapper registry\n"
)


_ENTRYPOINTS_CONFIG = (
    "layers: {}\nforbid: []\n"
    "required:\n"
    "  - source: '@entrypoints'\n"
    "    within: 'app.**'\n"
    "    must_reach: app.core.registry\n"
    "    reason: entrypoints need the mapper registry\n"
)


def _entrypoint_graph(*edges: tuple[str, str], entrypoints: dict[str, str]) -> nx.DiGraph:
    """A reach graph carrying the derived set `assemble_graph` would attach."""
    graph = _reach_graph(*edges, nodes=tuple(entrypoints))
    graph.graph["entrypoints"] = entrypoints
    return graph
