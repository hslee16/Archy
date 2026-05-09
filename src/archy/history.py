"""JSONL persistence for archy score runs - one row per `archy score --record`.

Each row captures the score, its components, the git context (commit and
branch where available), and rough scale inputs. The file is append-only
and line-oriented so it is trivial to diff, grep, jq, and hand-merge.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from archy.score import Score


@dataclass(frozen=True)
class HistoryRow:
    timestamp: str  # ISO-8601 UTC, second precision, suffixed Z.
    commit: str | None
    branch: str | None
    overall: float
    modularity: float
    acyclicity: float
    depth: float
    equality: float
    module_count: int
    edge_count: int
    cycle_count: int
    max_depth: int
    community_count: int


def append(history_path: Path, row: HistoryRow) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_row_to_dict(row), sort_keys=True))
        fh.write("\n")


def read(history_path: Path) -> list[HistoryRow]:
    if not history_path.exists():
        return []
    rows: list[HistoryRow] = []
    for raw_line in history_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            # Malformed lines skipped rather than aborted; the file is
            # append-only and a half-flushed write should not break trend.
            continue
        row = _row_from_dict(data)
        if row is not None:
            rows.append(row)
    return rows


def row_from_score(
    score: Score,
    *,
    commit: str | None,
    branch: str | None,
    now: dt.datetime | None = None,
) -> HistoryRow:
    moment = now or dt.datetime.now(dt.timezone.utc)
    return HistoryRow(
        timestamp=moment.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        commit=commit,
        branch=branch,
        overall=score.overall,
        modularity=score.modularity,
        acyclicity=score.acyclicity,
        depth=score.depth,
        equality=score.equality,
        module_count=score.inputs.module_count,
        edge_count=score.inputs.edge_count,
        cycle_count=score.inputs.cycle_count,
        max_depth=score.inputs.max_depth,
        community_count=score.inputs.community_count,
    )


def git_metadata(path: Path) -> tuple[str | None, str | None]:
    """Best-effort git context. Returns (commit_sha, branch_name) or (None, None)."""
    if not path.exists():
        return None, None
    commit = _git(path, "rev-parse", "HEAD")
    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        # Detached HEAD - leave branch unset rather than misreporting it.
        branch = None
    return commit, branch


# --- internals ----------------------------------------------------------------


def _git(path: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


def _row_to_dict(row: HistoryRow) -> dict:
    return {
        "timestamp": row.timestamp,
        "commit": row.commit,
        "branch": row.branch,
        "score": {
            "overall": row.overall,
            "modularity": row.modularity,
            "acyclicity": row.acyclicity,
            "depth": row.depth,
            "equality": row.equality,
        },
        "inputs": {
            "module_count": row.module_count,
            "edge_count": row.edge_count,
            "cycle_count": row.cycle_count,
            "max_depth": row.max_depth,
            "community_count": row.community_count,
        },
    }


def _row_from_dict(data: object) -> HistoryRow | None:
    top = _as_str_keyed(data)
    if top is None:
        return None
    score = _as_str_keyed(top.get("score"))
    inputs = _as_str_keyed(top.get("inputs"))
    timestamp = top.get("timestamp")
    if score is None or inputs is None or not isinstance(timestamp, str):
        return None
    try:
        return HistoryRow(
            timestamp=timestamp,
            commit=_optional_str(top.get("commit")),
            branch=_optional_str(top.get("branch")),
            overall=_as_float(score["overall"]),
            modularity=_as_float(score["modularity"]),
            acyclicity=_as_float(score["acyclicity"]),
            depth=_as_float(score["depth"]),
            equality=_as_float(score["equality"]),
            module_count=_as_int(inputs["module_count"]),
            edge_count=_as_int(inputs["edge_count"]),
            cycle_count=_as_int(inputs["cycle_count"]),
            max_depth=_as_int(inputs["max_depth"]),
            community_count=_as_int(inputs["community_count"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _as_float(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raise TypeError(f"expected number, got {type(value).__name__}")


def _as_int(value: object) -> int:
    if isinstance(value, int):
        return value
    raise TypeError(f"expected int, got {type(value).__name__}")


def _as_str_keyed(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, object] = {}
    for key, val in value.items():
        if not isinstance(key, str):
            return None
        out[key] = val
    return out


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else None
