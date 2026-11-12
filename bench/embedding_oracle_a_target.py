"""Recount A's capped volume for #587: the target the B cutoff is calibrated to match.

The ledger-only figure in the pre-registration (536 capped pairs over 1,049 measured PRs,
0.511 per PR) is provisional: the ledger has no per-member node counts and no base-and-head
existence flags. This recounts it, COUNTS ONLY, and records the result; it does not fix the
target (the post-pilot addendum does) and reads no label and no embedding.

The provisional rule reproduces the pre-registered numbers exactly (1,309 new members
uncapped, 536 capped): one pair per new member per firing, a PR counting at most K.

For the PRs that hit the cap the recount re-parses base and head and applies the real rules
of `expand_a_firing`: the partner is the existing member with the highest token overlap
(archy's near-miss Jaccard) that exists at BOTH base and head, a new member with none is
dropped, pairs from different firings with the same pair id are one pair, and the cap keeps
the largest new functions. A PR under the cap keeps its ledger count, which can only be too
high (existence and de-duplication only remove pairs), so the recount is an UPPER bound for
those PRs; `--reparse all` removes that caveat at the price of cloning every firing PR.

archy:owns        FileInfo, PrRecount, a_target, ledger_count, main, recount_pr
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from embedding_oracle_pairs import K, Member, Pair, PrRef, cap_pairs, expand_a_firing

from archy.complexity import compute_function_complexity, extract_token_bags
from archy.duplicates import _jaccard  # the near-miss tier's own scorer

ROOT = Path(__file__).resolve().parent.parent / "bench" / "cache" / "emb587"
DATA = Path(__file__).resolve().parent.parent / "bench" / "diff_duplicates_data"


@dataclass(frozen=True)
class FileInfo:
    """One file at one side: `{qualified name: (nodes, shape, line)}` plus token bags by line."""

    funcs: dict[str, tuple[int, str, int]]
    bags: dict[int, Counter[str]]


InfoAt = Callable[[str, str], "FileInfo | None"]  # (side, path) -> the file, or None if absent


@dataclass(frozen=True)
class PrRecount:
    pr: str
    new_per_firing: int
    pairs: int
    dropped_no_partner: int
    kept: int
    kept_nodes: tuple[int, ...]


def ledger_rows(data: Path) -> dict[str, dict[str, object]]:
    """The #548 ledger rows of measured PRs (`status == ok` with a `firings` list)."""
    rows: dict[str, dict[str, object]] = {}
    for path in sorted(data.glob("precision.*.jsonl")):
        for line in path.read_text(encoding="utf-8").split("\n"):
            if not line:
                continue
            row = json.loads(line)
            if row.get("status") == "ok" and "firings" in row:
                rows[row["key"]] = row
    return rows


def new_members(row: dict[str, object]) -> int:
    """New members summed over firings (a new member in two firings counts twice)."""
    return sum(1 for g in row["firings"] for m in g["members"] if m["new"])


def ledger_count(row: dict[str, object]) -> int:
    """The provisional capped count: the new members, a PR counting at most K."""
    return min(K, new_members(row))


def _member(m: dict[str, object], head: FileInfo | None) -> Member:
    nodes, shape, _line = (head.funcs.get(m["qualified_name"]) if head else None) or (
        0,
        "",
        m["line"],
    )
    return Member(
        path=m["path"], qualname=m["qualified_name"], is_new=m["new"], nodes=nodes, shape=shape
    )


def _firing_pairs(ref: PrRef, firing: dict[str, object], info_at: InfoAt) -> tuple[list[Pair], int]:
    line = {(m["path"], m["qualified_name"]): m["line"] for m in firing["members"]}
    members = [_member(m, info_at("head", m["path"])) for m in firing["members"]]

    def bag(m: Member) -> Counter[str]:
        info = info_at("head", m.path)
        return info.bags.get(line[(m.path, m.qualname)], Counter()) if info else Counter()

    def exists_at_both(m: Member) -> bool:
        base, head = info_at("base", m.path), info_at("head", m.path)
        return bool(base and head and m.qualname in base.funcs and m.qualname in head.funcs)

    return expand_a_firing(
        ref, members, overlap=lambda a, b: _jaccard(bag(a), bag(b)), exists_at_both=exists_at_both
    )


def recount_pr(row: dict[str, object], info_at: InfoAt) -> PrRecount:
    """Real pair expansion for one PR: partners, existence, de-duplication, then the cap."""
    ref = PrRef(pr=row["key"], repo=row["repo"])
    pooled: dict[str, Pair] = {}
    dropped = 0
    for firing in row["firings"]:
        pairs, lost = _firing_pairs(ref, firing, info_at)
        dropped += lost
        pooled.update({p.pair_id: p for p in pairs})
    kept = cap_pairs(list(pooled.values()))
    return PrRecount(
        row["key"],
        new_members(row),
        len(pooled),
        dropped,
        len(kept),
        tuple(sorted(p.new_nodes for p in kept)),
    )


def _wants(row: dict[str, object], reparse: str) -> bool:
    if reparse == "all":
        return bool(row["firings"])
    return reparse == "capped" and new_members(row) > K


def a_target(
    rows: dict[str, dict[str, object]],
    resolver: Callable[[dict[str, object]], InfoAt | None],
    reparse: str,
) -> dict[str, object]:
    """Provisional and recounted totals over every measured PR, and the per-PR recounts."""
    recounts: list[PrRecount] = []
    unresolved: dict[str, str] = {}
    total = 0
    for key, row in sorted(rows.items()):
        provisional = ledger_count(row)
        wanted = _wants(row, reparse)
        info_at = resolver(row) if wanted else None
        if wanted and info_at is None:
            unresolved[key] = "could not re-parse; ledger count kept"
        if info_at is None:
            total += provisional
            continue
        rc = recount_pr(row, info_at)
        recounts.append(rc)
        total += rc.kept
    return _summary(rows, total, recounts, unresolved)


def _summary(
    rows: dict[str, dict[str, object]],
    total: int,
    recounts: Iterable[PrRecount],
    unresolved: dict[str, str],
) -> dict[str, object]:
    n = len(rows)
    provisional = sum(ledger_count(r) for r in rows.values())
    return {
        "measured_prs": n,
        "uncapped_new_members": sum(new_members(r) for r in rows.values()),
        "provisional_capped_pairs": provisional,
        "provisional_per_pr": provisional / n,
        "recount_capped_pairs": total,
        "recount_per_pr": total / n,
        "prs_with_any_firing": sum(1 for r in rows.values() if r["firings"]),
        "prs_at_cap": sum(1 for r in rows.values() if new_members(r) > K),
        "reparsed": [rc.__dict__ for rc in recounts],
        "unresolved": unresolved,
        "frozen": False,
    }


# --- the git-backed resolver ---------------------------------------------------------


def _parse(source: bytes) -> FileInfo:
    funcs = {
        f.qualified_name: (f.size, f.shape_hash, f.line)
        for f in compute_function_complexity(source)
    }
    return FileInfo(funcs=funcs, bags=extract_token_bags(source))


def git_info_at(repo_dir: Path, base: str, head: str) -> InfoAt:
    """Files read from the object database with `git show`; a path absent at a side is None."""
    cache: dict[tuple[str, str], FileInfo | None] = {}
    sha = {"base": base, "head": head}

    def info_at(side: str, path: str) -> FileInfo | None:
        if (side, path) not in cache:
            show = subprocess.run(
                ["git", "show", f"{sha[side]}:{path}"],
                cwd=repo_dir,
                capture_output=True,
                check=False,
            )
            cache[(side, path)] = _parse(show.stdout) if show.returncode == 0 else None
        return cache[(side, path)]

    return info_at


def _resolver(row: dict[str, object]) -> InfoAt | None:
    sys.path.append(str(Path(__file__).resolve().parent))
    from embedding_oracle_extract import _clone_and_pin  # ty: ignore[unresolved-import]

    repo_dir = _clone_and_pin(row)
    return None if isinstance(repo_dir, str) else git_info_at(repo_dir, row["base"], row["head"])


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--reparse", choices=("none", "capped", "all"), default="capped")
    a = ap.parse_args(argv)
    result = a_target(ledger_rows(DATA), _resolver, a.reparse)
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "a_target.json").write_text(
        json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("reparsed",)}, indent=1, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
