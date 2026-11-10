"""The conventions and module-view tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from archy.mcp import (
    create_server,
)
from tests.mcp_helpers import call_tool as _call_tool


def test_conventions_tool_returns_the_derived_house_style(acyclic_project: Path):
    # The wire contract an agent reads BEFORE naming a new class: the family,
    # its home module, and the derived `concentration` that says how much to
    # trust that home. `concentration` is a `computed_field` precisely because
    # `model_dump()` drops plain properties, so its presence here is the
    # regression test for that trap.
    (acyclic_project / "pkg" / "layers.py").write_text(
        "class Violation: pass\nclass ReachViolation: pass\n"
    )
    server = create_server()
    _content, structured = _call_tool(server, "archy_conventions", {"path": str(acyclic_project)})
    assert isinstance(structured, dict)
    home = next(h for h in structured["naming"] if h["module"] == "pkg.layers")
    family = next(f for f in home["families"] if f["suffix"] == "Violation")
    assert family["home_module"] == "pkg.layers"
    assert family["concentration"] == 1.0
    assert home["total"] == 2
    # Both exit channels are on the wire, separately: an agent asking "should
    # my finding gate?" must not be handed a count of bad-input exits.
    assert structured["gates"] == []
    assert structured["errors"] == []
    assert "gate_modules" in structured
    assert "gate_codes" in structured
    assert structured["models"]["value_classes"] == 0


def test_conventions_tool_threads_include_tests_to_the_census(acyclic_project: Path):
    # The parameter has to reach the surface agents actually call. A flag wired
    # to the CLI alone is this repo's most-repeated defect (AGENTS.md), and it
    # is invisible from the CLI side, so the round trip is asserted here.
    (acyclic_project / "pkg" / "test_thing.py").write_text("class FixtureViolation: pass\n")
    server = create_server()
    _c, default = _call_tool(server, "archy_conventions", {"path": str(acyclic_project)})
    _c, with_tests = _call_tool(
        server, "archy_conventions", {"path": str(acyclic_project), "include_tests": True}
    )
    assert isinstance(default, dict) and isinstance(with_tests, dict)
    assert default["partition"]["tests"] == 1
    assert with_tests["partition"]["tests"] == 0
    assert with_tests["modules_scanned"] > default["modules_scanned"]


def test_conventions_tool_rejects_an_unknown_module(acyclic_project: Path):
    # A verdict without a reason is not actionable: an unknown name must fail
    # loudly rather than return an empty view that reads as "imports nothing".
    from archy.mcp_compat import ToolError

    server = create_server()
    with pytest.raises(ToolError):
        _call_tool(
            server,
            "archy_module_view",
            {"path": str(acyclic_project), "module": "pkg.nope"},
        )


def test_conventions_tool_rejects_a_nonsense_min_family(acyclic_project: Path):
    # Tier-2 usage error: an invalid argument value raises rather than
    # returning a confusing empty report (#229 error model).
    from archy.mcp_compat import ToolError

    server = create_server()
    with pytest.raises(ToolError):
        _call_tool(
            server,
            "archy_conventions",
            {"path": str(acyclic_project), "min_family": 1},
        )
