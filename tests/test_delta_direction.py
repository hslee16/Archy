"""Corpus-scale score-delta DIRECTION floor (issue #178).

The existing direction tests in test_diff.py use 2-node toy graphs where a
single cycle dominates `tangle_ratio`. They cannot catch a scoring-formula
regression that flips or flattens the `acyclicity` / cycle-diff response on a
real-world-sized graph. These tests assert that floor on synthetic graphs large
enough to dilute a single edge (the vendored corpus under bench/replay_cache is
gitignored, so CI relies on synthetic graphs here; the full corpus run lives in
bench/delta_direction.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx
import pytest

# bench/ is not a package; add it to the path to reuse the harness primitives.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))

import delta_direction as dd


def test_inject_two_cycle_creates_exactly_one_2cycle():
    g = dd.synthetic_dag(50)
    assert nx.is_directed_acyclic_graph(g)
    g1, (u, v) = dd.inject_two_cycle(g)
    new_sccs = [s for s in nx.strongly_connected_components(g1) if len(s) >= 2]
    assert len(new_sccs) == 1
    assert new_sccs[0] == {u, v}


@pytest.mark.parametrize("n", [120, 600])
def test_acyclicity_strictly_drops_on_injected_cycle_at_scale(n: int):
    # check_cycle_direction asserts internally (acyclicity sign + cycles.added /
    # resolved counts and module pairs, both directions). A returned row means
    # every assertion held.
    row = dd.check_cycle_direction(dd.synthetic_dag(n), label=f"test-{n}")
    assert row["d_acyclicity"] < 0
    assert row["modules"] == n


def test_overall_dilution_is_measured_not_asserted():
    # The pinned #178 decision: on a large graph `overall` may rise on a real
    # regression (intended dilution). The harness must report it as a float and
    # must NOT let that flip the asserted acyclicity signal.
    small = dd.check_cycle_direction(dd.synthetic_dag(120), label="test-small")
    large = dd.check_cycle_direction(dd.synthetic_dag(1500), label="test-large")
    assert isinstance(large["d_overall"], float)
    assert large["d_acyclicity"] < 0  # the guaranteed signal holds regardless
    # The dilution: a single edge carries less of its acyclicity magnitude into
    # `overall` on the larger graph.
    assert large["attenuation"] < small["attenuation"]


def test_layer_violation_direction():
    result = dd.check_violation_direction()
    assert result["rule"] == "l0->l1"
