"""The `diff --strict` ratchet rule (#521), on hand-built cycles.

Each case states the baseline and current cycles by hand, so the expected
verdict is worked out here rather than produced by the code under test.
"""

from __future__ import annotations

from archy.cycles import Cycle, CycleEdge
from archy.layers import ForbidRule, Violation
from archy.ratchet import evaluate_strict


def _cycle(*edges: tuple[str, str]) -> Cycle:
    members = sorted({m for edge in edges for m in edge})
    return Cycle(
        modules=tuple(members),
        edges=tuple(CycleEdge(source=s, target=t, lines=(3,)) for s, t in edges),
    )


_ABC = _cycle(("a", "b"), ("b", "c"), ("c", "a"))
_DE = _cycle(("d", "e"), ("e", "d"))
_MODULES = ("a", "b", "c", "d", "e", "x", "y")


def _verdict(baseline, current, *, violations=((), ()), modules=_MODULES):
    return evaluate_strict(
        baseline_label="archy-baseline.json",
        baseline_modules=modules,
        baseline_cycles=baseline,
        baseline_violations=violations[0],
        current_cycles=current,
        current_violations=violations[1],
        module_paths={m: f"pkg/{m}.py" for m in (*_MODULES, "new", "renamed")},
        ungoverned=(),
        regenerate="archy snapshot . --findings-only --out archy-baseline.json",
    )


def test_an_unchanged_cycle_is_carried():
    verdict = _verdict([_ABC], [_ABC])

    assert verdict.passed is True
    assert verdict.carried_cycles == 1


def test_splitting_a_baseline_cycle_passes():
    """The acceptance case: a 3-cycle becomes a 2-cycle plus an acyclic node.

    Under the old frozenset key this read as "resolved + added", so a gate on
    `cycles.added` would have blocked exactly the paydown it should reward.
    """
    verdict = _verdict([_ABC], [_cycle(("a", "b"), ("b", "a"))])

    assert verdict.passed is True
    assert verdict.findings == ()


def test_a_fresh_cycle_among_existing_acyclic_modules_fails():
    verdict = _verdict([_ABC], [_ABC, _cycle(("x", "y"), ("y", "x"))])

    assert verdict.passed is False
    [finding] = verdict.findings
    assert finding.modules == ("x", "y")
    assert "pulls x, y into a cycle" in finding.detail
    assert verdict.carried_cycles == 1


def test_a_fresh_cycle_among_new_files_fails():
    """No member is known to the baseline: ignoring unknown modules leaves the
    empty set, which is a subset of everything and must not read as carried."""
    verdict = _verdict([_ABC], [_cycle(("new", "renamed"), ("renamed", "new"))])

    [finding] = verdict.findings
    assert "among modules the baseline never saw" in finding.detail


def test_a_new_file_joining_a_baseline_cycle_passes():
    verdict = _verdict([_ABC], [_cycle(("a", "b"), ("b", "new"), ("new", "c"), ("c", "a"))])

    assert verdict.passed is True


def test_renaming_a_member_of_a_baseline_cycle_passes():
    verdict = _verdict([_ABC], [_cycle(("a", "renamed"), ("renamed", "c"), ("c", "a"))])

    assert verdict.passed is True


def test_a_new_edge_between_members_of_one_cycle_passes():
    """The decided case (#521's open design point). `a -> c` is a new import,
    but both ends already sit in one baseline cycle, so the tangle did not grow.
    The fixture's current cycle really does carry an edge the baseline lacks."""
    current = _cycle(("a", "b"), ("b", "c"), ("c", "a"), ("a", "c"))
    assert ("a", "c") in {(e.source, e.target) for e in current.edges}
    assert ("a", "c") not in {(e.source, e.target) for e in _ABC.edges}

    assert _verdict([_ABC], [current]).passed is True


def test_an_existing_module_pulled_into_a_cycle_fails():
    """`x` existed and was acyclic; a new file joining would pass, `x` must not."""
    grown = _cycle(("a", "b"), ("b", "c"), ("c", "x"), ("x", "a"))

    [finding] = _verdict([_ABC], [grown]).findings

    assert "pulls x into a cycle" in finding.detail
    # Only the edges the baseline lacks close it; `a -> b` and `b -> c` do not.
    assert [(e.source, e.target) for e in finding.edges] == [("c", "x"), ("x", "a")]


def test_a_new_edge_inside_the_old_tangle_is_not_named_as_closing():
    """`a -> c` is new but both ends sat in the baseline cycle, so it did not
    close anything; only the edges through `x` did. Seen on django, where the
    finding named an unrelated new import beside the real cause."""
    grown = _cycle(("a", "b"), ("b", "c"), ("c", "a"), ("a", "c"), ("c", "x"), ("x", "a"))

    [finding] = _verdict([_ABC], [grown]).findings

    assert [(e.source, e.target) for e in finding.edges] == [("c", "x"), ("x", "a")]


def test_a_large_cycle_is_summarized_by_size_in_the_detail():
    """A grown legacy tangle has hundreds of members; the detail gives the
    count and the cause, and `modules` keeps the full list."""
    ring = [f"m{i:02d}" for i in range(12)]
    big = _cycle(*zip(ring, ring[1:] + ring[:1], strict=True))

    [finding] = _verdict([], [big], modules=tuple(ring)).findings

    assert "12 modules" in finding.detail
    assert "m05" not in finding.detail.split("Closing")[0]
    assert len(finding.modules) == 12


def test_merging_two_baseline_cycles_fails():
    merged = _cycle(("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "e"), ("e", "a"))

    [finding] = _verdict([_ABC, _DE], [merged]).findings

    assert "merges cycles the baseline kept separate" in finding.detail
    assert "c -> d at pkg/c.py:3" in finding.detail


def test_a_new_layer_violation_fails_and_a_carried_one_passes():
    rule = ForbidRule(from_layer="core", to_layer="cli")
    old = Violation(rule=rule, source="a", target="b", lines=(4,))
    new = Violation(rule=rule, source="x", target="y", lines=(7, 9))

    verdict = _verdict([], [], violations=((old,), (old, new)))

    [finding] = verdict.findings
    assert finding.kind == "violation"
    assert finding.edges[0].locations == ("pkg/x.py:7", "pkg/x.py:9")
    assert "new layer violation (core -> cli): x -> y at pkg/x.py:7, pkg/x.py:9" in (finding.detail)
    assert verdict.carried_violations == 1


def test_a_baseline_without_a_module_list_refuses_the_verdict():
    """Without the list a new file cannot be told from an existing module, so
    neither outcome can be trusted; a refused verdict fails."""
    verdict = _verdict([_ABC], [_ABC], modules=())

    assert verdict.passed is False
    assert verdict.refused is not None
    assert "records no module list" in verdict.reason


def test_passed_and_reason_survive_model_dump():
    """MCP sends `model_dump()`; a plain property would vanish from the wire."""
    dumped = _verdict([_ABC], [_DE]).model_dump()

    assert dumped["passed"] is False
    assert "new import cycle" in dumped["reason"]
    assert "0 cycle(s) and 0 violation(s) carried" in dumped["reason"]


def test_a_new_self_import_in_a_formerly_cyclic_module_fails():
    """`a` sat in the baseline `a <-> b` cycle; that cycle was broken and `a`
    now imports itself. Its only member is in a baseline cycle, so the subset
    test alone would carry it, but the self-edge is new."""
    self_loop = Cycle(modules=("a",), edges=(CycleEdge(source="a", target="a", lines=(1,)),))
    baseline = _cycle(("a", "b"), ("b", "a"))

    verdict = _verdict([baseline], [self_loop])

    assert verdict.passed is False
    assert verdict.findings[0].modules == ("a",)


def test_a_self_import_the_baseline_had_is_carried():
    self_loop = Cycle(modules=("a",), edges=(CycleEdge(source="a", target="a", lines=(1,)),))

    assert _verdict([self_loop], [self_loop]).passed is True


def test_a_baseline_violation_the_rules_no_longer_cover_refuses():
    """Patterns gone dead (a bare `pkg.core` for `pkg.core.**`, or a package
    renamed under the config): the violation would silently drop out and new
    ones go unseen. With the rules still covering it, the same input passes."""
    rule = ForbidRule(from_layer="core", to_layer="cli")
    old = Violation(rule=rule, source="a", target="b", lines=(4,))

    def run(ungoverned: tuple[Violation, ...]):
        return evaluate_strict(
            baseline_label="b.json",
            baseline_modules=_MODULES,
            baseline_cycles=[],
            baseline_violations=[old],
            current_cycles=[],
            current_violations=[],
            module_paths={},
            ungoverned=ungoverned,
            regenerate="archy snapshot app --findings-only --out b.json",
        )

    assert run(()).passed is True
    dead = run((old,))
    assert dead.passed is False
    assert "no longer cover 1 baseline violation(s)" in dead.reason
    assert "a -> b, core -> cli" in dead.reason
    assert "archy snapshot app --findings-only --out b.json" in dead.reason
