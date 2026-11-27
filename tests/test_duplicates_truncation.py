"""A near-miss pass that stopped at its comparison budget must say so (#777)."""

from __future__ import annotations

import json as _json
from collections import Counter
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.cli_render_git import _duplicates_to_text, _text_with_truncation
from archy.complexity import FunctionComplexity
from archy.duplicates import (
    NEAR_TRUNCATED_NOTE,
    DuplicateGroup,
    DuplicateMember,
    _Candidate,
    _cluster_by_similarity,
    compute_near_duplicates,
    with_truncation,
)
from archy.mcp_runs import run_duplicates


def _cand(name: str, size: int, bag: dict[str, int]) -> _Candidate:
    fn = FunctionComplexity(
        name=name, qualified_name=name, line=1, cyclomatic=1, shape_hash=f"s-{name}", size=size
    )
    return _Candidate(name, f"{name}.py", fn, Counter(bag))


_NEAR_PAIR = {"x.py": "alpha", "y.py": "beta"}


def _near_pair_project(root: Path) -> Path:
    """Two functions that differ by one inserted statement: one near-miss pair."""
    (root / "p").mkdir()
    (root / "p" / "__init__.py").write_text("", encoding="utf-8")
    for filename, name in _NEAR_PAIR.items():
        extra = '    print("debug", total)\n' if name == "beta" else ""
        (root / "p" / filename).write_text(
            f"def {name}(items, limit):\n"
            "    total = 0\n"
            "    for item in items:\n"
            "        if item > limit:\n"
            "            total += item * 2\n"
            "        else:\n"
            "            total -= 1\n"
            f"{extra}"
            "    result = [t for t in range(total) if t % 3 == 0]\n"
            "    return sum(result) + len(items)\n",
            encoding="utf-8",
        )
    return root


def _near_miss_surfaces(root: Path) -> tuple[dict, str, dict]:
    """(CLI JSON, CLI text, MCP `model_dump()`) for one near-miss run."""
    args = ["duplicates", str(root), "--near-miss", "--min-nodes", "10"]
    as_json = CliRunner().invoke(main, [*args, "--format", "json"])
    as_text = CliRunner().invoke(main, args)
    assert (as_json.exit_code, as_text.exit_code) == (0, 0), as_json.output + as_text.output
    payload = run_duplicates(
        root, response_format="summary", min_nodes=10, top_n=10, members=2, near_miss=True
    )
    return _json.loads(as_json.stdout), as_text.stdout, payload.model_dump()


def test_a_complete_near_miss_pass_is_not_flagged(tmp_path: Path):
    cli, text, mcp = _near_miss_surfaces(_near_pair_project(tmp_path))

    # The fixture has a pair to lose, which the truncated test below relies on.
    assert (cli["near_total"], mcp["near_total"]) == (1, 1)
    assert (cli["near_truncated"], mcp["near_truncated"]) == (False, False)
    assert "INCOMPLETE" not in text + str(cli["note"]) + str(mcp["note"])


def test_a_truncated_near_miss_pass_says_so_on_every_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """#777: `near_total: 0` from a pass that stopped early read as "no clones"."""
    defaults = compute_near_duplicates.__kwdefaults__
    assert defaults is not None
    monkeypatch.setitem(defaults, "max_comparisons", 0)
    root = _near_pair_project(tmp_path)

    with pytest.warns(UserWarning, match="token-comparison budget"):
        cli, text, mcp = _near_miss_surfaces(root)

    assert (cli["near_total"], mcp["near_total"]) == (0, 0)
    assert (cli["near_truncated"], mcp["near_truncated"]) == (True, True)
    for said in (text, cli["note"], mcp["note"]):
        assert "Near-miss results are INCOMPLETE" in said


def test_a_budget_used_up_exactly_is_not_truncation():
    """Three in-band pairs, budget three: nothing was skipped."""
    items = [_cand(name, 10, {"x": 10}) for name in ("a", "b", "c")]

    _components, _weights, truncated = _cluster_by_similarity(items, 0.5, 3)

    assert not truncated


def test_pairs_out_of_the_size_band_do_not_count_as_skipped():
    items = [_cand("a", 10, {"x": 10}), _cand("b", 100, {"x": 100})]

    _components, _weights, truncated = _cluster_by_similarity(items, 0.5, 0)

    assert not truncated


def test_a_truncated_pass_that_found_something_still_says_so():
    near = DuplicateGroup(
        shape_hash="",
        size=40,
        member_count=2,
        redundancy=40,
        category="near_miss",
        similarity=0.9,
        exact=False,
        members=(
            DuplicateMember(module="a", qualified_name="f", path="a.py", line=1),
            DuplicateMember(module="b", qualified_name="g", path="b.py", line=1),
        ),
    )

    text = _text_with_truncation(_duplicates_to_text([near], top_n=5, min_nodes=10), True)

    assert "near-miss (Type-3" in text and NEAR_TRUNCATED_NOTE in text


@pytest.mark.parametrize(
    ("note", "truncated", "expected"),
    [
        (None, False, None),
        ("Nothing found.", False, "Nothing found."),
        (None, True, NEAR_TRUNCATED_NOTE),
        ("Nothing found.", True, f"Nothing found. {NEAR_TRUNCATED_NOTE}"),
    ],
)
def test_with_truncation(note: str | None, truncated: bool, expected: str | None):
    assert with_truncation(note, truncated) == expected
