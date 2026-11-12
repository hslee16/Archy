"""Per-PR function units for the #587 oracle: which functions are new, which may be neighbours.

Reads the extractor's Mac-side unit index (`<shard>.units.jsonl`, one row per function
per side). The join key is `(pr, side, path, qualname, occurrence)`: same-named functions in
one file get occurrence 0, 1, ...; a head row that was not added carries an id that is NOT
in the shard, so no join here is ever made on a head row's id. Three rules of the
pre-registration live here and nowhere else:

* a NEW function is a head function whose `def` line is inside an added hunk;
* a NEIGHBOUR is a base function whose `counterpart` flag is set: the same path and
  qualified name exists in the head tree at ANY size (a function the PR deleted, renamed or
  moved is not one: a move would pair at similarity near 1.0 and be the refactor itself);
* the base functions lost to that condition are counted, as their own drop reason.

Pure logic over rows; no model, no clone, no label.

archy:owns        PrUnits, UnitFn, UnitIndexError, check_schema, group_by_pr,
                  load_units, split_pr
archy:mirrored-by UnitFn -> bench.embedding_oracle_generate,
                  bench.embedding_oracle_generate_run
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

REQUIRED = ("occurrence", "counterpart")


class UnitIndexError(ValueError):
    """The unit index is not the schema this reader joins on (an older extractor wrote it)."""


@dataclass(frozen=True)
class UnitFn:
    """One function, as the unit index records it."""

    id: str
    pr: str
    repo: str
    path: str
    qualname: str
    nodes: int
    shape: str
    occurrence: int = 0

    @property
    def name_key(self) -> tuple[str, str, int]:
        return (self.path, self.qualname, self.occurrence)


@dataclass(frozen=True)
class PrUnits:
    """A PR's new functions and the existing functions that may be their neighbours."""

    pr: str
    repo: str
    new: tuple[UnitFn, ...]
    existing: tuple[UnitFn, ...]
    lost_not_at_head: int


def _fn(row: dict[str, object]) -> UnitFn:
    return UnitFn(
        id=row["id"],
        pr=row["pr"],
        repo=row["repo"],
        path=row["path"],
        qualname=row["qualname"],
        nodes=row["nodes"],
        shape=row["shape"],
        occurrence=row["occurrence"],
    )


def group_by_pr(rows: Iterable[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[row["pr"]].append(row)
    return dict(grouped)


def load_units(path: Path) -> dict[str, list[dict[str, object]]]:
    """The unit index of one shard, grouped by PR key."""
    # never splitlines(): U+2028 and form feed split rows
    lines = path.read_text(encoding="utf-8").split("\n")
    return group_by_pr(json.loads(line) for line in lines if line)


def check_schema(rows: list[dict[str, object]]) -> None:
    """Refuse an index written by an extractor that does not emit the join key."""
    for r in rows:
        missing = [k for k in REQUIRED if k not in r]
        if missing:
            raise UnitIndexError(
                f"unit row lacks {missing}: regenerate the index with the current extractor"
            )


def split_pr(pr: str, rows: list[dict[str, object]]) -> PrUnits:
    """New functions, valid neighbours, and how many base functions the PR took away.

    `rows` are every unit row of one PR. A head row with `in_added` true is new. A base row
    is a neighbour only if its `counterpart` flag is set, whether or not that function is
    itself in an added hunk at head. A new function never pairs with its own base version
    (the generator drops that candidate by `name_key`, see `embedding_oracle_generate`).
    """
    check_schema(rows)
    base = [r for r in rows if r["side"] == "base"]
    existing = [r for r in base if r["counterpart"]]
    new = [r for r in rows if r["side"] == "head" and r["in_added"] is True]
    repo = rows[0]["repo"] if rows else ""
    return PrUnits(
        pr=pr,
        repo=repo,
        new=tuple(sorted(map(_fn, new), key=lambda f: f.name_key)),
        existing=tuple(sorted(map(_fn, existing), key=lambda f: f.name_key)),
        lost_not_at_head=len(base) - len(existing),
    )
