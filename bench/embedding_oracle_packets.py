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
import subprocess
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
    first = lines[line - 1] if 1 <= line <= len(lines) else ""
    if not first.lstrip().startswith(("def ", "async def ")):
        return None
    indent = _indent(first)
    header_end = _header_end(lines, line - 1)
    return "\n".join(
        lines[_decorator_start(lines, line - 1, indent) : _body_end(lines, header_end, indent) + 1]
    )


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _is_decorator(line: str, indent: int) -> bool:
    return line.strip().startswith("@") and _indent(line) == indent


def _decorator_start(lines: Sequence[str], at: int, indent: int) -> int:
    """The first line of the decorators stacked directly above the def at `at`."""
    while at > 0 and _is_decorator(lines[at - 1], indent):
        at -= 1
    return at


def _header_end(lines: Sequence[str], at: int) -> int:
    """The line where the def header's brackets are closed and it ends in a colon."""
    depth = 0
    for i in range(at, len(lines)):
        depth += sum(lines[i].count(c) for c in "([{") - sum(lines[i].count(c) for c in ")]}")
        if depth <= 0 and lines[i].rstrip().endswith(":"):
            return i
    return len(lines) - 1


def _body_end(lines: Sequence[str], header_end: int, indent: int) -> int:
    """The last non-blank line indented deeper than the def, after its header."""
    end = header_end
    for j in range(header_end + 1, len(lines)):
        if not lines[j].strip():
            continue
        if _indent(lines[j]) <= indent:
            break
        end = j
    return end


def _def_line(raw: bytes, path: str, qualname: str, rev: str) -> int:
    """The one line archy's parser puts `qualname` on, or a LookupError naming why not."""
    lines = [f.line for f in compute_function_complexity(raw) if f.qualified_name == qualname]
    if len(lines) != 1:
        raise LookupError(f"{qualname} matches {len(lines)} functions in {path} at {rev[:8]}")
    return lines[0]


def _source(repo_dir: Path, rev: str, path: str, qualname: str) -> str:
    raw = _show(repo_dir, rev, path)
    if raw is None:
        raise LookupError(f"cannot open {path} at {rev[:8]}")
    line = _def_line(raw, path, qualname, rev)
    text = raw.decode("utf-8", "replace")
    source = function_source(text, line) or source_by_indent(text, line)
    if source is None:
        raise LookupError(f"cannot read {qualname} in {path} at {rev[:8]}")
    return source


def build_pair_packet(pair: Pair, revs: tuple[str, str], repo_dir: Path, rubric: str) -> dict:
    """The blind packet for one pair, `revs` being (base, head). Raises LookupError if either
    function is unreadable."""
    base, head = revs
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
    by_id, member_of = _union(sets)
    (out / "packets").mkdir(parents=True, exist_ok=True)
    key: dict[str, dict] = {}
    built = 0
    for pid in sorted(by_id):
        p = by_id[pid]
        base, head = revs[p.pr]
        entry = _key_entry(p, base, head, member_of[pid])
        key[packet_id(pid)] = entry
        built += _write_packet(p, entry, rubric, out)
    (out / "key.json").write_text(json.dumps(key, indent=2, sort_keys=True), encoding="utf-8")
    return {"pairs": len(by_id), "packets": built, "unscorable": len(by_id) - built}


def _union(
    sets: Mapping[str, Sequence[Pair]],
) -> tuple[dict[str, Pair], dict[str, list[str]]]:
    """Every distinct pair by id, and the names of the sets that drew it."""
    by_id: dict[str, Pair] = {}
    member_of: dict[str, list[str]] = {}
    for name, pairs in sets.items():
        for p in pairs:
            by_id.setdefault(p.pair_id, p)
            member_of.setdefault(p.pair_id, []).append(name)
    return by_id, member_of


def _key_entry(p: Pair, base: str, head: str, sets: Sequence[str]) -> dict:
    return {
        "pair_id": p.pair_id,
        "pr": p.pr,
        "repo": p.repo,
        "base": base,
        "head": head,
        "generators": list(p.generators),
        "sets": sorted(sets),
        "exact_tier": p.exact_tier,
    }


def _write_packet(p: Pair, entry: dict, rubric: str, out: Path) -> int:
    """Write the packet for `p`; 1 if written, 0 if a function could not be located (the
    reason goes into `entry`)."""
    repo_dir = CLONES / p.repo.replace("/", "__")
    try:
        packet = build_pair_packet(p, (entry["base"], entry["head"]), repo_dir, rubric)
    except LookupError as exc:
        entry["unscorable_reason"] = str(exc)
        return 0
    (out / "packets" / f"{packet['id']}.json").write_text(
        json.dumps(packet, indent=2), encoding="utf-8"
    )
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    # Deferred: only the CLI needs the draw and the pool, and importing this module for
    # `packet_id` or `write_packets` should not load them.
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
