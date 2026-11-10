"""Tests for graph: hints."""

from __future__ import annotations

import difflib
from pathlib import Path

from archy.graph import (
    _SUGGEST_MIN_SIMILARITY,
    build_graph,
    hint_unresolved,
    resolve_modules,
    suggest_modules,
)
from tests.graph_helpers import _KNOWN


def test_resolve_modules_routes_qualnames_and_paths(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("")
    (pkg / "b.py").write_text("")
    graph = build_graph(tmp_path)

    resolved, unresolved = resolve_modules(
        graph,
        ["pkg.a", str(pkg / "b.py")],
        project_root=tmp_path,
    )
    assert resolved == ["pkg.a", "pkg.b"]
    assert unresolved == []


def test_resolve_modules_deduplicates_preserving_order(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("")
    (pkg / "b.py").write_text("")
    graph = build_graph(tmp_path)

    # pkg.a appears three times via two surface forms (qualname + path).
    # Dedup must keep first-seen order, so the path repetition does not push
    # pkg.b in front of the duplicate-but-already-seen pkg.a.
    resolved, _ = resolve_modules(
        graph,
        ["pkg.a", str(pkg / "a.py"), "pkg.a", "pkg.b"],
        project_root=tmp_path,
    )
    assert resolved == ["pkg.a", "pkg.b"]


def test_resolve_modules_accepts_absolute_path(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("")
    graph = build_graph(tmp_path)

    # Absolute paths bypass project_root resolution entirely.
    resolved, unresolved = resolve_modules(
        graph,
        [str((pkg / "a.py").resolve())],
        project_root=tmp_path / "wrong",  # deliberately wrong; abs path wins
    )
    assert resolved == ["pkg.a"]
    assert unresolved == []


def test_resolve_modules_does_not_match_external_qualnames(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("import requests\n")
    graph = build_graph(tmp_path)
    assert "requests" in graph.nodes  # sanity: the external node exists

    # External qualnames aren't legal focus targets; they should fall through
    # to the path-resolution branch and end up in `unresolved` (since
    # 'requests' isn't a real file path under the project either).
    resolved, unresolved = resolve_modules(graph, ["requests"], project_root=tmp_path)
    assert resolved == []
    assert unresolved == ["requests"]


def test_suggest_modules_offers_a_misspelling():
    """`pkg.scoer` vs `pkg.score`: shared `pkg.sco` (7) plus `e` (1) is 8 matched
    characters of 9 + 9, ratio 16/18 = 0.89. `pkg.scorer_utils` shares `pkg.sco`
    and `er` (9 of 9 + 16), ratio 18/25 = 0.72, under the 0.8 floor."""
    assert not any("pkg.scoer" in n for n in _KNOWN)  # only the fuzzy leg can answer
    assert suggest_modules(_KNOWN, "pkg.scoer") == ["pkg.score"]


def test_suggest_modules_offers_every_module_containing_a_bare_name():
    assert suggest_modules(_KNOWN, "cli") == ["pkg.cli", "tests.test_cli"]


def test_suggest_modules_puts_containing_names_before_misspellings():
    """`m.scorf` is a misspelling of `m.score` (12/14 = 0.86) and sorts first by
    name, so only the tiering can put `z.m.score`, which contains it, ahead."""
    known = ("m.scorf", "z.m.score")
    assert sorted(known)[0] == "m.scorf"
    assert suggest_modules(known, "m.score") == ["z.m.score", "m.scorf"]


def test_suggest_modules_does_not_dilute_a_clear_winner_with_prefix_siblings():
    """`tests.test_dsmm` is 28/29 = 0.97 from `tests.test_dsm`; `tests.test_mcp` shares
    `tests.test_` and an `m` (24/29 = 0.83). The sibling clears the floor but is more
    than 0.1 behind the winner, so it is not offered."""
    known = ("tests.test_dsm", "tests.test_mcp")
    siblings_ratio = difflib.SequenceMatcher(None, "tests.test_dsmm", "tests.test_mcp").ratio()
    assert siblings_ratio >= _SUGGEST_MIN_SIMILARITY  # the floor alone would offer it
    assert suggest_modules(known, "tests.test_dsmm") == ["tests.test_dsm"]


def test_suggest_modules_keeps_misspellings_that_are_equally_close():
    """`m.scorf` and `m.scorg` are each 12/14 = 0.86 from `m.score`: neither wins."""
    assert suggest_modules(("m.scorg", "m.scorf"), "m.score") == ["m.scorf", "m.scorg"]


def test_suggest_modules_does_not_offer_unrelated_siblings():
    """`pkg.cli` vs `pkg.dsm` share only `pkg.` and a letter or two: 8/14 = 0.57."""
    assert suggest_modules(["pkg.dsm"], "pkg.cli") == []


def test_suggest_modules_has_nothing_to_say_when_nothing_is_close():
    assert suggest_modules(_KNOWN, "pkg.zzzzzzzz") == []


def test_suggest_modules_empty_ref_matches_nothing():
    """The empty string is a substring of every name, which would suggest them all."""
    assert suggest_modules(_KNOWN, "") == []


def test_suggest_modules_caps_at_limit_in_name_order():
    known = [f"pkg.x{i}" for i in (3, 1, 4, 2, 5)]
    assert suggest_modules(known, "x", limit=3) == ["pkg.x1", "pkg.x2", "pkg.x3"]


def test_suggest_modules_does_not_depend_on_input_order():
    assert suggest_modules(reversed(_KNOWN), "cli") == suggest_modules(_KNOWN, "cli")
    assert suggest_modules(reversed(_KNOWN), "pkg.scoer") == suggest_modules(_KNOWN, "pkg.scoer")


def test_hint_unresolved_answers_modules_with_modules_and_paths_with_paths(tmp_path: Path):
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "score.py").write_text("")
    graph = build_graph(tmp_path)
    hints = hint_unresolved(graph, ["app.scoer", "app/scoer.py", "zzzzzzzz"], base=tmp_path)
    assert [(h.ref, h.did_you_mean) for h in hints] == [
        ("app.scoer", ("app.score",)),
        ("app/scoer.py", ("app/score.py",)),
    ]  # input order kept; the reference with no close match gets no entry


def test_hint_unresolved_lists_a_repeated_reference_once(tmp_path: Path):
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "score.py").write_text("")
    graph = build_graph(tmp_path)
    assert len(hint_unresolved(graph, ["app.scoer", "app.scoer"])) == 1


def test_hint_unresolved_does_not_offer_external_modules(tmp_path: Path):
    """`requestz` is 14/16 = 0.88 from the third-party `requests`, which is in the graph
    as an external node but is not a module the caller can name in this project."""
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "m.py").write_text("import requests\n")
    graph = build_graph(tmp_path)
    assert graph.nodes["requests"]["external"]  # the near miss exists, as an external
    assert hint_unresolved(graph, ["requestz"]) == ()
