"""Blind one-pair packets for #587's pooled labeling.

Each packet shows ONE new function (at head) and ONE existing function (at base), the
frozen rubric, and nothing else: no similarity, tier, generator, arm or merge outcome. The
packet id is a hash of the pair id, so it carries no order and no generator. The output
directory is the one `diff_duplicates_labeler.py` reads (`packets/*.json` plus `key.json`
with the head commit per id); the key also records, per pair, which sets drew it, and stays
out of the labeler's view.

A pair whose function cannot be located is not scored: it is written to the key with its
reason and gets no packet, and the counts report it.

archy:owns        build_pair_packet, main, packet_id, source_by_indent, write_packets
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from diff_duplicates_packets import (  # ty: ignore[unresolved-import]  (bench/ is sys.path[0])
    _rubric,
    function_source,
)
from diff_duplicates_pr import CLONES  # ty: ignore[unresolved-import]
from embedding_oracle_pairs import Pair  # ty: ignore[unresolved-import]

from archy.complexity import compute_function_complexity

ROOT = Path(__file__).resolve().parent.parent / "bench" / "cache" / "emb587"


def packet_id(pair_id: str) -> str:
    """An opaque, order-free id for one pair."""
    return hashlib.sha256(pair_id.encode("utf-8")).hexdigest()[:20]


def _show(repo_dir: Path, rev: str, path: str):
    import subprocess

    shown = subprocess.run(
        ["git", "-C", str(repo_dir), "show", f"{rev}:{path}"], capture_output=True, check=False
    )
    return shown.stdout if shown.returncode == 0 else None


def source_by_indent(text: str, line: int) -> str | None:
    """The def at 1-based `line` and its decorators, found by layout, not by `ast`.

    The fallback for a file the running interpreter's `ast` cannot parse (newer syntax
    than this Python) but archy's tree-sitter parser can. The header ends where its
    brackets close; the body is the lines indented deeper than the `def`, blank lines
    kept, up to the first line that is not.
    """
    lines = text.splitlines()
    if not 1 <= line <= len(lines):
        return None
    first = lines[line - 1]
    if not first.lstrip().startswith(("def ", "async def ")):
        return None
    indent = len(first) - len(first.lstrip())
    start = line - 1
    while (
        start > 0
        and lines[start - 1].strip().startswith("@")
        and _indent(lines[start - 1]) == indent
    ):
        start -= 1
    i, depth = line - 1, 0
    while i < len(lines):
        depth += sum(lines[i].count(c) for c in "([{") - sum(lines[i].count(c) for c in ")]}")
        if depth <= 0 and lines[i].rstrip().endswith(":"):
            break
        i += 1
    end = i
    for j in range(i + 1, len(lines)):
        if lines[j].strip() and _indent(lines[j]) <= indent:
            break
        if lines[j].strip():
            end = j
    return "\n".join(lines[start : end + 1])


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _source(repo_dir: Path, rev: str, path: str, qualname: str) -> str:
    raw = _show(repo_dir, rev, path)
    if raw is None:
        raise LookupError(f"cannot open {path} at {rev[:8]}")
    lines = [f.line for f in compute_function_complexity(raw) if f.qualified_name == qualname]
    if len(lines) != 1:
        raise LookupError(f"{qualname} matches {len(lines)} functions in {path} at {rev[:8]}")
    text = raw.decode("utf-8", "replace")
    source = function_source(text, lines[0]) or source_by_indent(text, lines[0])
    if source is None:
        raise LookupError(f"cannot read {qualname} in {path} at {rev[:8]}")
    return source


def build_pair_packet(pair: Pair, base: str, head: str, repo_dir: Path, rubric: str) -> dict:
    """The blind packet for one pair. Raises LookupError if either function is unreadable."""
    return {
        "id": packet_id(pair.pair_id),
        "repo": pair.repo,
        "instructions": rubric,
        "new": [
            {
                "path": pair.new_path,
                "qualified_name": pair.new_qualname,
                "source": _source(repo_dir, head, pair.new_path, pair.new_qualname),
            }
        ],
        "existing": [
            {
                "path": pair.existing_path,
                "qualified_name": pair.existing_qualname,
                "source": _source(repo_dir, base, pair.existing_path, pair.existing_qualname),
            }
        ],
    }


def write_packets(
    sets: Mapping[str, Sequence[Pair]], revs: Mapping[str, tuple[str, str]], out: Path
) -> dict[str, int]:
    """Packets for the union of `sets` (name -> pairs), `key.json` mapping id to its record.

    `revs` maps a PR key to (base, head). A pair in several sets is one packet.
    """
    rubric = _rubric()
    by_id: dict[str, Pair] = {}
    member_of: dict[str, list[str]] = {}
    for name, pairs in sets.items():
        for p in pairs:
            by_id.setdefault(p.pair_id, p)
            member_of.setdefault(p.pair_id, []).append(name)
    (out / "packets").mkdir(parents=True, exist_ok=True)
    key: dict[str, dict] = {}
    built = 0
    for pid in sorted(by_id):
        p = by_id[pid]
        base, head = revs[p.pr]
        entry = {
            "pair_id": pid,
            "pr": p.pr,
            "repo": p.repo,
            "base": base,
            "head": head,
            "generators": list(p.generators),
            "sets": sorted(member_of[pid]),
            "exact_tier": p.exact_tier,
        }
        key[packet_id(pid)] = entry
        try:
            packet = build_pair_packet(p, base, head, CLONES / p.repo.replace("/", "__"), rubric)
        except LookupError as exc:
            entry["unscorable_reason"] = str(exc)
            continue
        (out / "packets" / f"{packet['id']}.json").write_text(
            json.dumps(packet, indent=2), encoding="utf-8"
        )
        built += 1
    (out / "key.json").write_text(json.dumps(key, indent=2, sort_keys=True), encoding="utf-8")
    return {"pairs": len(by_id), "packets": built, "unscorable": len(by_id) - built}


def main(argv: Sequence[str] | None = None) -> int:
    from embedding_oracle_evaluation import evaluation_rows  # ty: ignore[unresolved-import]
    from embedding_oracle_pool import ARMS, read_pairs  # ty: ignore[unresolved-import]

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "labels")
    args = parser.parse_args(argv)
    revs = {str(r["key"]): (r["base"], r["head"]) for r in evaluation_rows()}
    sets = {"primary": read_pairs(ROOT / "label_pool.evaluation.jsonl")}
    for m in ARMS:
        sets[f"arm:{m}"] = read_pairs(ROOT / f"label_arm.{m}.evaluation.jsonl")
    print(json.dumps(write_packets(sets, revs, args.out), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
