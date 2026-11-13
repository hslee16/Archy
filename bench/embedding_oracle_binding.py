"""What ties a ledger and a cutoff to the exact inputs they were made from, for #587.

A ledger row is only meaningful for the vectors, configuration and unit index it was scored
from. Each row therefore carries a BINDING (vectors sha256, the configuration, the unit-index
sha256), and every reader refuses a ledger whose rows are bound to anything else, so a re-embed
or a re-extraction can never be mixed with rows from before it. The same module guards the
cutoff file: a target must be a positive finite number, a frozen cutoff is never overwritten,
and only a frozen cutoff may be applied.

archy:owns        binding_of, check_binding, check_not_frozen, check_target,
                  freeze_cutoff, ok_rows, require_frozen
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from embedding_oracle_vectors import VectorError, VectorSet, file_sha256


def binding_of(vs: VectorSet, units_path: Path) -> dict[str, object]:
    """The inputs a scored row depends on."""
    return {
        "vectors_sha256": vs.sha256,
        "config": json.loads(json.dumps(vs.config)),
        "units_sha256": file_sha256(units_path),
    }


def ok_rows(path: Path) -> list[dict[str, object]]:
    """Every `status == ok` row of a ledger file (a torn tail is skipped, as `Ledger` does)."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        try:
            row = json.loads(line) if line else None
        except json.JSONDecodeError:
            row = None
        if row and row.get("status") == "ok":
            rows.append(row)
    return rows


def check_binding(rows: Iterable[dict[str, object]], binding: dict[str, object]) -> None:
    """Refuse a ledger holding any row scored from different vectors, configuration or units."""
    bad = sum(1 for r in rows if r.get("binding") != binding)
    if bad:
        raise VectorError(
            f"{bad} ledger rows were scored from other vectors, another configuration or "
            "another unit index; archive the ledger and score again"
        )


def check_target(value: float) -> float:
    """A volume target is a positive finite number; nan, zero, negative and inf are refused."""
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"target must be a positive finite number, not {value}")
    return value


def check_not_frozen(path: Path) -> None:
    frozen = path.exists() and json.loads(path.read_text(encoding="utf-8")).get("frozen")
    if frozen is True:
        raise VectorError("this cutoff is frozen; it is never overwritten")


def require_frozen(cut: dict[str, object]) -> None:
    """Applying a cutoff follows the addendum that fixes it; before that it is only a trial."""
    if cut.get("frozen") is not True:
        raise VectorError("this cutoff is not frozen: freeze it from the addendum first")


def freeze_cutoff(path: Path, addendum: str) -> None:
    """Mark a cutoff frozen, naming the dated addendum that fixed it. Refuses to freeze twice."""
    if not addendum.strip():
        raise VectorError("freezing needs the addendum that fixes the cutoff")
    check_not_frozen(path)
    cut = json.loads(path.read_text(encoding="utf-8"))
    cut.update(frozen=True, frozen_by=addendum, frozen_utc=datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(cut, indent=1, sort_keys=True) + "\n", encoding="utf-8")
