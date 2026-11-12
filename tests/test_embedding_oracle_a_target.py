"""The A-side recount for #587: provisional rule, real expansion, existence, cap, de-duplication.

Synthetic ledger rows and in-memory files only. Expected values are worked by hand, and each
test asserts its fixture reaches the branch (a dropped member, a cap hit) before asserting.
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

from embedding_oracle_a_target import (  # ty: ignore[unresolved-import]  (added to sys.path above)
    FileInfo,
    _firing_pairs,
    a_target,
    git_info_at,
    ledger_count,
    ledger_rows,
    new_members,
    recount_pr,
)
from embedding_oracle_pairs import PrRef  # ty: ignore[unresolved-import]


def member(path: str, qual: str, new: bool, line: int = 1) -> dict[str, object]:
    return {"path": path, "qualified_name": qual, "new": new, "line": line}


def row(key: str, firings: list[list[dict[str, object]]]) -> dict[str, object]:
    return {
        "key": key,
        "repo": "o/r",
        "base": "b",
        "head": "h",
        "firings": [{"members": m} for m in firings],
    }


def info(funcs: dict[str, int], bags: dict[int, Counter[str]] | None = None) -> FileInfo:
    return FileInfo(
        {q: (n, f"shape-{q}", i + 1) for i, (q, n) in enumerate(funcs.items())}, bags or {}
    )


def world(base: dict[str, FileInfo], head: dict[str, FileInfo]):
    return lambda side, path: (base if side == "base" else head).get(path)


def big_firing(n: int) -> list[dict[str, object]]:
    """n new members (lines 1..n) plus one old member `old` at line 100, all in a.py."""
    return [member("a.py", f"new{i:02d}", True, i + 1) for i in range(n)] + [
        member("a.py", "old", False, 100)
    ]


def big_world(n: int, old_at_base: bool = True, old_at_head: bool = True):
    head = {f"new{i:02d}": 30 + i for i in range(n)} | ({"old": 50} if old_at_head else {})
    base = {"old": 50} if old_at_base else {"other": 50}
    return world({"a.py": info(base)}, {"a.py": info(head)})


def test_the_provisional_count_is_per_firing_and_capped_per_pr() -> None:
    twice = row("1", [[member("a.py", "n", True), member("a.py", "o", False)]] * 2)
    assert (
        new_members(twice) == 2 and ledger_count(twice) == 2
    )  # the same new member in two firings counts twice
    assert ledger_count(row("2", [big_firing(12)])) == 10  # 12 new members, capped at K = 10
    assert ledger_count(row("3", [])) == 0


def test_real_expansion_pairs_each_new_member_and_caps_by_nodes() -> None:
    rc = recount_pr(row("1", [big_firing(12)]), big_world(12))
    assert (
        rc.new_per_firing == 12 and rc.pairs == 12 and rc.dropped_no_partner == 0
    )  # reached: all paired
    assert rc.kept == 10
    assert rc.kept_nodes == tuple(range(32, 42))  # nodes 30 and 31 are the two smallest, cut


def test_a_new_member_whose_partner_is_missing_at_base_is_dropped_not_kept() -> None:
    rc = recount_pr(row("1", [big_firing(12)]), big_world(12, old_at_base=False))
    assert rc.dropped_no_partner == 12 and rc.pairs == 0 and rc.kept == 0


def test_a_partner_missing_at_head_is_dropped_too() -> None:
    rc = recount_pr(row("1", [big_firing(3)]), big_world(3, old_at_head=False))
    assert rc.dropped_no_partner == 3 and rc.kept == 0


def test_the_partner_is_the_existing_member_with_the_highest_token_overlap() -> None:
    members = [
        member("a.py", "n", True, 1),
        member("a.py", "far", False, 2),
        member("a.py", "near", False, 3),
    ]
    bags = {
        1: Counter("aab"),
        2: Counter("xyz"),
        3: Counter("aab"),
    }  # n and near identical (Jaccard 1), far disjoint (0)
    fi = FileInfo({"n": (40, "s", 1), "far": (40, "s", 2), "near": (40, "s", 3)}, bags)
    pairs, dropped = _firing_pairs(
        PrRef(pr="1", repo="o/r"), {"members": members}, world({"a.py": fi}, {"a.py": fi})
    )
    assert dropped == 0 and len(pairs) == 1
    assert pairs[0].existing_qualname == "near"


def test_pairs_from_two_firings_with_the_same_id_are_one_pair() -> None:
    f = [member("a.py", "new00", True, 1), member("a.py", "old", False, 100)]
    r = row("1", [f, f])
    assert ledger_count(r) == 2  # provisional rule: counted twice
    assert recount_pr(r, big_world(1)).kept == 1  # real rule: one pair id


def test_node_counts_move_which_pairs_survive_but_never_how_many() -> None:
    small = recount_pr(row("1", [big_firing(12)]), big_world(12))
    flipped = world(
        {"a.py": info({"old": 50})},
        {"a.py": info({f"new{i:02d}": 100 - i for i in range(12)} | {"old": 50})},
    )
    other = recount_pr(row("1", [big_firing(12)]), flipped)
    assert small.kept == other.kept == 10
    assert small.kept_nodes != other.kept_nodes


def test_a_target_reparses_only_what_it_was_asked_to() -> None:
    capped, plain = row("1", [big_firing(12)]), row("2", [big_firing(3)])
    calls: list[str] = []

    def resolver(r: dict[str, object]):
        calls.append(str(r["key"]))
        return big_world(12, old_at_base=False)  # every pair is dropped when it is re-parsed

    none = a_target({"1": capped, "2": plain}, resolver, "none")
    assert calls == [] and none["recount_capped_pairs"] == 13  # 10 + 3, the ledger count
    got = a_target({"1": capped, "2": plain}, resolver, "capped")
    assert calls == ["1"]  # reached the cap branch for PR 1 only
    assert (
        got["provisional_capped_pairs"] == 13 and got["recount_capped_pairs"] == 3
    )  # PR 1 -> 0, PR 2 kept 3
    assert got["prs_at_cap"] == 1 and got["measured_prs"] == 2
    every = a_target({"1": capped, "2": plain}, resolver, "all")
    assert calls == ["1", "1", "2"] and every["recount_capped_pairs"] == 0


def test_an_unresolvable_pr_keeps_its_ledger_count_and_is_reported() -> None:
    got = a_target({"1": row("1", [big_firing(12)])}, lambda r: None, "capped")
    assert got["recount_capped_pairs"] == 10
    assert list(got["unresolved"]) == ["1"]


def test_git_info_at_reads_each_side_and_reports_an_absent_path(tmp_path: Path) -> None:
    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    git("init", "-q")
    (tmp_path / "m.py").write_text(
        "def kept(x):\n    return x + 1\n\n\ndef gone(x):\n    return x * 2\n"
    )
    git("add", "-A")
    git("-c", "user.name=c", "-c", "user.email=c@c", "commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "m.py").write_text("def kept(x):\n    return x + 1\n")
    (tmp_path / "n.py").write_text("def added(x):\n    return x - 1\n")
    git("add", "-A")
    git("-c", "user.name=c", "-c", "user.email=c@c", "commit", "-q", "-m", "head")
    at = git_info_at(tmp_path, base, git("rev-parse", "HEAD"))
    base_m, head_m = at("base", "m.py"), at("head", "m.py")
    assert base_m is not None and head_m is not None  # both sides parsed
    assert set(base_m.funcs) == {"kept", "gone"} and set(head_m.funcs) == {"kept"}
    assert at("base", "n.py") is None and at("head", "n.py") is not None


def test_a_ledger_row_holding_u2028_is_read_whole(tmp_path: Path) -> None:
    import json

    rows = [
        {"key": "1", "status": "ok", "firings": [], "repo": "a\u2028b/c"},
        {"key": "2", "status": "ok", "firings": [], "repo": "d/e"},
    ]
    (tmp_path / "precision.x.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    assert (
        len((tmp_path / "precision.x.jsonl").read_text(encoding="utf-8").splitlines()) > 2
    )  # reached
    assert sorted(ledger_rows(tmp_path)) == ["1", "2"]
