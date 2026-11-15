"""The pooled-labeling plumbing of #587: budget and arm draws, blind packets, the report.

Every expected number is worked by hand in the test that asserts it.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import embedding_oracle_packets as pk  # ty: ignore[unresolved-import]  (path set above)
import embedding_oracle_pool as pool  # ty: ignore[unresolved-import]  (path set above)
import embedding_oracle_report as rep  # ty: ignore[unresolved-import]  (path set above)
from embedding_oracle_pairs import Pair  # ty: ignore[unresolved-import]  (path set above)


def _pair(i: int, gens=("B",), shape=("n", "e")) -> Pair:
    return Pair(
        pr="1",
        repo="o/r",
        new_path="a.py",
        new_qualname=f"new{i}",
        new_nodes=50,
        new_shape=shape[0],
        existing_path="b.py",
        existing_qualname=f"old{i}",
        existing_shape=shape[1],
        generators=gens,
    )


# --- draws -------------------------------------------------------------------------------


def test_budget_draw_keeps_every_pair_at_or_under_the_cap() -> None:
    pairs = [_pair(i) for i in range(5)]
    assert [p.pair_id for p in pool.budget_draw(pairs, cap=5)] == sorted(p.pair_id for p in pairs)


def test_budget_draw_over_the_cap_is_the_seeded_sample_of_sorted_ids() -> None:
    pairs = [_pair(i) for i in range(12)]
    ids = sorted(p.pair_id for p in pairs)
    expected = sorted(random.Random(587).sample(ids, 4))
    got = [p.pair_id for p in pool.budget_draw(pairs, cap=4)]
    assert got == expected
    assert len(set(got)) == 4
    # Not the first four: the draw is a sample, so the fixture must not make them coincide.
    assert got != ids[:4]


def test_budget_draw_does_not_depend_on_input_order() -> None:
    pairs = [_pair(i) for i in range(12)]
    shuffled = list(reversed(pairs))
    assert [p.pair_id for p in pool.budget_draw(pairs, 4)] == [
        p.pair_id for p in pool.budget_draw(shuffled, 4)
    ]


def test_arm_draw_excludes_pairs_already_in_the_primary_pool() -> None:
    arm = [_pair(i) for i in range(6)]
    in_pool = {arm[0].pair_id, arm[3].pair_id}
    got = {p.pair_id for p in pool.arm_draw(arm, in_pool, cap=100)}
    assert got == {p.pair_id for p in arm} - in_pool


def test_arm_draw_caps_what_remains_after_the_exclusion() -> None:
    arm = [_pair(i) for i in range(10)]
    got = pool.arm_draw(arm, {arm[0].pair_id}, cap=3)
    assert len(got) == 3
    assert arm[0].pair_id not in {p.pair_id for p in got}


def test_plan_pool_merges_an_a_pair_and_a_b_pair_with_one_id() -> None:
    """A's pair 2 (from the expander) and B's pair 2 share an id: one entry, tagged A and B."""
    a2, b2, b5 = _pair(2, ("A",)), _pair(2, ("B",)), _pair(5, ("B",))
    merged = pool.pool_pairs([a2], [b2, b5])
    assert [p.generators for p in merged] == [("A", "B"), ("B",)]
    assert pool.budget_draw(merged, cap=300) == merged


def test_plan_pool_lists_a_firing_pr_it_could_not_reparse() -> None:
    rows = [{"key": "9", "firings": [{"members": []}]}, {"key": "8", "firings": []}]
    plan = pool.plan_pool(rows, lambda row: None, [])
    assert plan.unresolved_prs == ["9"]


# --- packets -----------------------------------------------------------------------------


def test_packet_id_is_opaque_and_stable() -> None:
    a, b = pk.packet_id("x"), pk.packet_id("y")
    assert a != b and a == pk.packet_id("x")
    assert len(a) == 20 and "x" not in a


@pytest.mark.parametrize(
    ("text", "line", "want"),
    [
        # a plain def: the body ends at the first line that is not indented deeper
        ("def f():\n    return 1\n\nx = 2\n", 1, "def f():\n    return 1"),
        # decorators above the def line are kept
        ("@dec\n@dec2(1)\ndef f():\n    pass\n", 3, "@dec\n@dec2(1)\ndef f():\n    pass"),
        # a header spanning lines: the closing `) -> int:` sits at the def's own indent
        (
            "def f(\n    a,\n) -> int:\n    return a\ny = 1\n",
            1,
            "def f(\n    a,\n) -> int:\n    return a",
        ),
        # a method: ends before the next sibling at the same indent
        (
            "class C:\n    def m(self):\n        return 1\n\n    def n(self):\n        return 2\n",
            2,
            "    def m(self):\n        return 1",
        ),
        # a decorator-looking line at another indent is not this def's decorator
        ("  @odd\n    def f():\n        pass\n", 2, "    def f():\n        pass"),
        # blank lines inside the body are kept, trailing ones are not
        (
            "def f():\n    a = 1\n\n    return a\n\n\nz = 0\n",
            1,
            "def f():\n    a = 1\n\n    return a",
        ),
    ],
)
def test_source_by_indent(text: str, line: int, want: str) -> None:
    assert pk.source_by_indent(text, line) == want


def test_source_by_indent_refuses_a_line_that_is_not_a_def() -> None:
    assert pk.source_by_indent("x = 1\ndef f():\n    pass\n", 1) is None
    assert pk.source_by_indent("def f():\n    pass\n", 9) is None


def _repo(tmp: Path) -> tuple[Path, str, str]:
    import subprocess

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp / "b.py").write_text("def old(x):\n    return x + 1\n")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    (tmp / "a.py").write_text("@deco\ndef new(y):\n    return y + 2\n")
    (tmp / "b.py").write_text("def old(x):\n    return x + 99\n")  # changed at head
    git("add", ".")
    git("commit", "-q", "-m", "head")
    return tmp, base, git("rev-parse", "HEAD")


def test_the_packet_is_blind_and_shows_new_at_head_and_existing_at_base(tmp_path: Path) -> None:
    repo, base, head = _repo(tmp_path)
    pair = Pair(
        pr="1", repo="o/r", new_path="a.py", new_qualname="new", new_nodes=9, new_shape="n",
        existing_path="b.py", existing_qualname="old", existing_shape="e",
        similarity=0.99, generators=("A", "B"),
    )  # fmt: skip
    packet = pk.build_pair_packet(pair, base, head, repo, "RUBRIC")
    assert set(packet) == {"id", "repo", "instructions", "new", "existing"}
    assert packet["new"] == [
        {"path": "a.py", "qualified_name": "new", "source": "@deco\ndef new(y):\n    return y + 2"}
    ]
    assert packet["existing"][0]["source"] == "def old(x):\n    return x + 1"
    blob = json.dumps(packet)
    assert "0.99" not in blob and '"A"' not in blob and "generators" not in blob
    assert packet["id"] == pk.packet_id(pair.pair_id)


def test_write_packets_records_sets_and_drops_an_unlocatable_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "repo").mkdir()
    repo, base, head = _repo(tmp_path / "repo")
    good = Pair(
        pr="1", repo="o/r", new_path="a.py", new_qualname="new", new_nodes=9, new_shape="n",
        existing_path="b.py", existing_qualname="old", existing_shape="e", generators=("B",),
    )  # fmt: skip
    gone = good.model_copy(update={"new_qualname": "missing"})
    monkeypatch.setattr(pk, "CLONES", repo.parent)  # the clone dir is `<CLONES>/o__r`
    (repo.parent / "o__r").symlink_to(repo)
    out = tmp_path / "out"
    counts = pk.write_packets({"primary": [good, gone], "arm:x": [good]}, {"1": (base, head)}, out)
    assert counts == {"pairs": 2, "packets": 1, "unscorable": 1}
    key = json.loads((out / "key.json").read_text())
    entry = key[pk.packet_id(good.pair_id)]
    assert entry["sets"] == ["arm:x", "primary"] and entry["head"] == head
    assert "unscorable_reason" in key[pk.packet_id(gone.pair_id)]
    assert [f.name for f in (out / "packets").iterdir()] == [f"{pk.packet_id(good.pair_id)}.json"]


# --- report ------------------------------------------------------------------------------


def test_kappa_by_hand() -> None:
    """Four items: sonnet TRUE,TRUE,FALSE,FALSE; opus TRUE,FALSE,FALSE,FALSE.

    observed = 3/4. sonnet is TRUE 2/4, opus TRUE 1/4, so chance agreement is
    (2/4)(1/4) + (2/4)(3/4) = 1/8 + 3/8 = 1/2. kappa = (3/4 - 1/2) / (1 - 1/2) = 1/2.
    """
    a = {"1": "TRUE", "2": "TRUE", "3": "FALSE", "4": "FALSE"}
    b = {"1": "TRUE", "2": "FALSE", "3": "FALSE", "4": "FALSE"}
    assert rep.cohen_kappa(a, b) == pytest.approx(0.5)


def test_kappa_ignores_unscorable_and_is_none_without_variance() -> None:
    a = {"1": "TRUE", "2": "UNSCORABLE"}
    b = {"1": "TRUE", "2": "FALSE"}
    assert rep.cohen_kappa(a, b) is None  # one item left, both TRUE: expected agreement is 1
    assert rep.cohen_kappa({}, {}) is None


def test_load_labels_keeps_only_ok_rows(tmp_path: Path) -> None:
    f = tmp_path / "l.jsonl"
    f.write_text(
        json.dumps({"id": "a", "label": "TRUE", "status": "ok"})
        + "\n"
        + json.dumps({"id": "b", "error": "stall", "status": "error"})
        + "\n"
    )
    assert rep.load_labels(f) == {"a": "TRUE"}


def test_report_is_strict_and_counts_by_hand() -> None:
    """Primary pairs: p0 (A only), p1 (B only), p2 (A and B), p3 (B only, exact tier).

    Strict truth needs both labelers: p0 TRUE, p1 FALSE (sonnet FALSE), p2 TRUE, p3 TRUE.
    B pairs are p1, p2, p3: 2 TRUE of 3, so B precision 2/3 over all and 1/2 without the
    exact-tier pair. TRUE non-exact units: p0 (found by A), p2 (found by A and B), so two
    units, none found only by B.
    """
    p0 = _pair(0, ("A",))
    p1 = _pair(1, ("B",))
    p2 = _pair(2, ("A", "B"))
    p3 = _pair(3, ("B",), shape=("same", "same"))
    key = {f"k{i}": {"pair_id": p.pair_id} for i, p in enumerate([p0, p1, p2, p3])}
    sonnet = {"k0": "TRUE", "k1": "FALSE", "k2": "TRUE", "k3": "TRUE"}
    opus = {"k0": "TRUE", "k1": "TRUE", "k2": "TRUE", "k3": "TRUE"}
    sets = {"primary": [p0, p1, p2, p3], **{f"arm:{m}": [] for m in rep.ARMS}}
    out = rep.report(key, {"sonnet": sonnet, "opus": opus}, sets, {})
    primary = out["primary"]
    assert primary["b_precision"]["true"] == 2 and primary["b_precision"]["scorable"] == 3
    assert primary["b_precision_without_exact"]["scorable"] == 2
    assert primary["b_precision_without_exact"]["true"] == 1
    assert primary["share_headline"]["true_units"] == 2
    assert primary["share_headline"]["only_b"] == 0
    assert primary["share_with_exact"]["true_units"] == 3  # p3's unit joins; it is only-by-B
    assert primary["share_with_exact"]["only_b"] == 1
    assert primary["a_precision"] == pytest.approx(1.0)  # p0 and p2 both TRUE
    assert out["packets_labeled_by_both"] == 4


def test_a_name_carried_by_two_functions_in_a_file_is_not_located(tmp_path: Path) -> None:
    """`dup` is defined twice in one file: which one the pair means is unknowable, so the
    packet is refused rather than built from a guess."""
    repo, _base, _head = _repo(tmp_path)
    (repo / "c.py").write_text("def dup():\n    return 1\n\ndef dup():\n    return 2\n")
    import subprocess

    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "c"], check=True)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    with pytest.raises(LookupError, match="matches 2 functions"):
        pk._source(repo, head, "c.py", "dup")
    with pytest.raises(LookupError, match="matches 0 functions"):
        pk._source(repo, head, "c.py", "absent")


def test_plan_summary_counts_pairs_both_generators_emit() -> None:
    a, b = _pair(1, ("A",)), _pair(1, ("B",))
    plan = pool.plan_pool([], lambda row: None, [b, _pair(2, ("B",))])
    merged = pool.PoolPlan([a], plan.b_pairs, pool.pool_pairs([a], plan.b_pairs), [], [])
    summary = pool._plan_summary(merged)
    assert summary["pool"] == 2 and summary["emitted_by_both"] == 1
    assert summary["a_pairs"] == 1 and summary["b_pairs"] == 2
