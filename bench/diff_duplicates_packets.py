"""Blind labeling packets for the #548 study (see the pre-registration).

Each firing becomes one packet a labeler reads: the repository, the new function(s)
and the pre-existing one(s), with source, plus the frozen rubric. A packet carries
NOTHING else. Withheld by construction, because the pre-registration says labelers
are blind to them: the detector tier, similarity, size, whether the match is exact,
the PR number, the agent, the merge outcome and anything from the git oracle. Those
live only in `key.json`, which a labeler never opens.

If more than 120 firings exist, a seeded draw (seed 548) of 120 is made over the
firing ids ALONE, before any packet is built or any function is read.

    uv run python bench/diff_duplicates_packets.py LEDGER [LEDGER ...] --out DIR

archy:owns        build_packet, draw_for_labeling, flatten_firings, function_source,
                  main, write_packets
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from diff_duplicates_pr import (  # ty: ignore[unresolved-import]  (bench/ is sys.path[0])
    CLONES,
    REPO_ROOT,
    SEED,
)

LABEL_CAP = 120
_PREREG = REPO_ROOT / "docs" / "research" / "DIFF_DUPLICATES_PRECISION.md"


def flatten_firings(rows: dict[str, dict]) -> dict[str, tuple[dict, dict]]:
    """`{firing_id: (ledger_row, firing)}` with ids `<pr key>:<index>`, from `ok` rows."""
    out: dict[str, tuple[dict, dict]] = {}
    for key, row in rows.items():
        for index, firing in enumerate(row.get("firings") or []):
            out[f"{key}:{index}"] = (row, firing)
    return out


def draw_for_labeling(ids: Sequence[str], *, seed: int = SEED, cap: int = LABEL_CAP) -> list[str]:
    """All ids when there are at most `cap`, else a seeded sample of `cap`, sorted."""
    ordered = sorted(ids)
    if len(ordered) <= cap:
        return ordered
    return sorted(random.Random(seed).sample(ordered, cap))


def function_source(text: str, line: int) -> str | None:
    """Source of the function whose `def` is on `line`, decorators included, or None."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.lineno == line:
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            return "\n".join(lines[start - 1 : node.end_lineno])
    return None


def _member_source(repo_dir: Path, head: str, member: dict) -> dict:
    shown = subprocess.run(
        ["git", "-C", str(repo_dir), "show", f"{head}:{member['path']}"],
        capture_output=True,
        text=True,
        check=False,
    )
    source = function_source(shown.stdout, member["line"]) if shown.returncode == 0 else None
    if source is None:
        raise LookupError(f"cannot read {member['path']}:{member['line']} at {head[:8]}")
    return {"path": member["path"], "qualified_name": member["qualified_name"], "source": source}


def _rubric() -> str:
    text = _PREREG.read_text(encoding="utf-8")
    start = text.index("**TRUE** means")
    end = text.index("A firing is **TRUE under the primary oracle only if both")
    return text[start:end].strip()


def build_packet(firing_id: str, row: dict, firing: dict, repo_dir: Path) -> dict:
    """The blind packet for one firing. Raises LookupError if a member is unreadable."""
    members = firing["members"]
    return {
        "id": firing_id,
        "repo": row["repo"],
        "instructions": _rubric(),
        "new": [_member_source(repo_dir, row["head"], m) for m in members if m["new"]],
        "existing": [_member_source(repo_dir, row["head"], m) for m in members if not m["new"]],
    }


def _key_entry(row: dict, firing: dict) -> dict:
    return {
        "pr": row["number"],
        "repo": row["repo"],
        "agent": row["agent"],
        "merged": row["merged"],
        "base": row["base"],
        "head": row["head"],
        "category": firing["category"],
        "exact": firing["exact"],
        "similarity": firing["similarity"],
        "size": firing["size"],
        "near_miss_budget_hit": row.get("near_miss_budget_hit"),
    }


def write_packets(rows: dict[str, dict], out: Path) -> dict:
    """Draw, build and write packets plus `key.json`; return counts."""
    firings = flatten_firings(rows)
    chosen = draw_for_labeling(list(firings))
    (out / "packets").mkdir(parents=True, exist_ok=True)
    key: dict[str, dict] = {}
    built = 0
    for fid in chosen:
        row, firing = firings[fid]
        key[fid] = _key_entry(row, firing)
        repo_dir = CLONES / row["repo"].replace("/", "__")
        try:
            packet = build_packet(fid, row, firing, repo_dir)
        except LookupError as exc:
            key[fid]["unscorable_reason"] = str(exc)
            continue
        (out / "packets" / f"{fid.replace(':', '_')}.json").write_text(
            json.dumps(packet, indent=2), encoding="utf-8"
        )
        built += 1
    (out / "key.json").write_text(json.dumps(key, indent=2, sort_keys=True), encoding="utf-8")
    return {"firings": len(firings), "drawn": len(chosen), "packets": built}


def main(argv: Sequence[str] | None = None) -> int:
    from diff_duplicates_run import _ok_rows  # ty: ignore[unresolved-import]

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("ledgers", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    rows: dict[str, dict] = {}
    for path in args.ledgers:
        rows.update(_ok_rows(path))
    print(json.dumps(write_packets(rows, args.out), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
