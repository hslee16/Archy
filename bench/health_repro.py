#!/usr/bin/env python
"""Can archy reproduce CodeScene's function-level smells? (#870)

The protocol, thresholds and detector definitions are fixed in
``docs/research/HEALTH_REPRODUCIBILITY_PREREG.md``, committed before this script
was run over the corpus. Nothing here may change a threshold; a result that
misses one is written up as a miss.

Two halves:

- :func:`smells_in_source` is the archy side: five detectors over Python's
  ``ast`` (cyclomatic complexity comes from ``archy.complexity``), returning the
  flagged functions keyed by (name, start line).
- :func:`cs_flags` reads ``cs review --output-format json`` and resolves each
  finding to the same (name, start line) key, so the two sides compare as sets.

A project is one unit of work. Each completed unit appends one JSON row to the
ledger in a single write; only ``status == "ok"`` counts as done, so a stalled or
crashed unit is retried on the next run and never scored as agreeing.

Usage:
    uv run python bench/health_repro.py --ledger LEDGER.jsonl --only archy anyio
    uv run python bench/health_repro.py --ledger LEDGER.jsonl --report

archy:owns        cs_flags, jaccard, main, report, run_project, smells_in_source,
                  spearman
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path

from archy.complexity import compute_function_complexity

# Thresholds read from `cs rules-config list-thresholds --language python`.
CC_AT_LEAST = 9
MAX_ARGS = 4
MAX_CODE_LINES = 70
NESTING_AT_LEAST = 4
BOOL_OPS_AT_LEAST = 2

SMELLS = (
    "Complex Method",
    "Excess Number of Function Arguments",
    "Large Method",
    "Deep, Nested Complexity",
    "Complex Conditional",
)
EXCLUDED_SEGMENTS = {"tests", "test", "docs", "examples", "migrations", "vendor"}
MATCH_WINDOW = 3  # decorators start a function on a different line in the two tools
PER_PROJECT_SECONDS = 30 * 60

# smell -> {(name, start_line), ...}
Flags = dict[str, set[tuple[str, int]]]
Func = ast.FunctionDef | ast.AsyncFunctionDef


def _functions(tree: ast.Module) -> list[tuple[Func, bool]]:
    """Top-level functions and methods of top-level classes, as `cs` sees them.

    Functions nested in other functions are skipped: `cs` did not flag a
    five-parameter nested function in calibration. The flag says whether the
    function is a method.
    """
    found: list[tuple[Func, bool]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found.append((node, False))
        elif isinstance(node, ast.ClassDef):
            found.extend(
                (m, True)
                for m in node.body
                if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)
            )
    return found


def _parameter_count(fn: Func, *, is_method: bool) -> int:
    a = fn.args
    positional = [*a.posonlyargs, *a.args]
    if is_method and positional and positional[0].arg in {"self", "cls"}:
        positional = positional[1:]
    return len(positional) + len(a.kwonlyargs) + (a.vararg is not None) + (a.kwarg is not None)


def _code_lines(fn: Func, lines: list[str]) -> int:
    """Lines from the `def` to the end that hold code: not blank, not comment-only."""
    span = lines[fn.lineno - 1 : fn.end_lineno]
    return sum(1 for ln in span if ln.strip() and not ln.strip().startswith("#"))


def _is_elif(parent: ast.If, child: ast.AST) -> bool:
    # An `elif` is an `If` that is the sole `orelse` entry and sits at the same
    # column as its parent `if`; `else:` then a nested `if` sits deeper.
    return (
        isinstance(child, ast.If)
        and parent.orelse == [child]
        and child.col_offset == parent.col_offset
    )


def _depth(node: ast.AST, current: int = 0) -> int:
    """Deepest nesting of `if`, `for`, `while`; `with`, `try` and `elif` add none."""
    deepest = current
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue
        counts = isinstance(child, ast.If | ast.For | ast.AsyncFor | ast.While)
        if counts and isinstance(node, ast.If) and _is_elif(node, child):
            counts = False
        deepest = max(deepest, _depth(child, current + 1 if counts else current))
    return deepest


def _bool_ops(expr: ast.AST) -> int:
    return sum(len(n.values) - 1 for n in ast.walk(expr) if isinstance(n, ast.BoolOp))


def _complex_condition(fn: Func) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.If | ast.While) and _bool_ops(n.test) >= BOOL_OPS_AT_LEAST:
            return True
    return False


def smells_in_source(source: str) -> Flags:
    """The five smells archy can derive, as {smell: {(name, start_line)}}."""
    tree = ast.parse(source)
    lines = source.splitlines()
    cc = {
        (f.name, f.line): f.cyclomatic for f in compute_function_complexity(source.encode("utf-8"))
    }
    out: Flags = {s: set() for s in SMELLS}
    for fn, is_method in _functions(tree):
        key = (fn.name, fn.lineno)
        if any(
            v >= CC_AT_LEAST
            for (name, line), v in cc.items()
            if name == fn.name and abs(line - fn.lineno) <= MATCH_WINDOW
        ):
            out["Complex Method"].add(key)
        if _parameter_count(fn, is_method=is_method) > MAX_ARGS:
            out["Excess Number of Function Arguments"].add(key)
        if _code_lines(fn, lines) > MAX_CODE_LINES:
            out["Large Method"].add(key)
        if _depth(fn) >= NESTING_AT_LEAST:
            out["Deep, Nested Complexity"].add(key)
        if _complex_condition(fn):
            out["Complex Conditional"].add(key)
    return out


def _resolve(
    table: list[tuple[str, int, int]], name: str, start: int | None, inside: int | None
) -> tuple[str, int] | None:
    """Map a `cs` finding to the (name, start_line) of a function in `table`."""
    same = [(n, s, e) for n, s, e in table if n == name]
    if start is not None:
        near = [(n, s, e) for n, s, e in same if abs(s - start) <= MATCH_WINDOW]
        if near:
            return near[0][0], near[0][1]
    if inside is not None:
        holds = [(n, s, e) for n, s, e in same if s <= inside <= e]
        if holds:
            return holds[0][0], holds[0][1]
    return None


def cs_flags(cs_review: list[dict], tables: dict[str, list[tuple[str, int, int]]]) -> dict:
    """{path: Flags} from `cs review --output-format json`, plus unresolved findings."""
    flags: dict[str, Flags] = {}
    unresolved: list[tuple[str, str, str]] = []
    for entry in cs_review:
        path = entry["path"]
        per_file: Flags = {s: set() for s in SMELLS}
        for issue in entry.get("review", []):
            if issue["category"] not in per_file:
                continue
            for f in issue.get("functions", []):
                qualified, _, tail = f["title"].partition(":")
                name = qualified.rsplit(".", 1)[-1]  # `cs` titles methods `Class.method`
                inside = int(tail) if tail.isdigit() else None
                key = _resolve(tables.get(path, []), name, f.get("start-line"), inside)
                if key is None:
                    unresolved.append((path, issue["category"], f["title"]))
                else:
                    per_file[issue["category"]].add(key)
        flags[path] = per_file
    return {"flags": flags, "unresolved": unresolved}


def jaccard(a: set, b: set) -> float | None:
    union = a | b
    return None if not union else len(a & b) / len(union)


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation with average ranks for ties; None if undefined."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return None if den == 0 else num / den


def _function_table(source: str) -> list[tuple[str, int, int]]:
    return [
        (fn.name, fn.lineno, fn.end_lineno or fn.lineno) for fn, _ in _functions(ast.parse(source))
    ]


def _source_files(root: Path) -> list[Path]:
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "*.py"], capture_output=True, text=True, check=False
    ).stdout.split()
    return [root / p for p in tracked if not (set(Path(p).parts[:-1]) & EXCLUDED_SEGMENTS)]


def _cs_version() -> str:
    out = subprocess.run(["cs", "version"], capture_output=True, text=True, check=False)
    return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""


def _serializable(flags: dict[str, Flags]) -> dict[str, dict[str, list[tuple[str, int]]]]:
    """Sets become sorted lists so the ledger row is JSON and diffs cleanly."""
    return {p: {s: sorted(v) for s, v in f.items()} for p, f in flags.items()}


def run_project(name: str, root: Path) -> dict:
    """One unit: archy and `cs` flags for every source file of one project."""
    started = time.time()
    files = _source_files(root)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write("\n".join(str(f) for f in files))
        listing = Path(fh.name)
    try:
        proc = subprocess.run(
            ["cs", "review", "--file-list", str(listing), "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=PER_PROJECT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"project": name, "status": "stalled", "reason": "cs review timed out"}
    finally:
        listing.unlink(missing_ok=True)
    try:
        review = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"project": name, "status": "error", "reason": proc.stderr[-300:]}
    scores: dict[str, float] = {}
    mine: dict[str, Flags] = {}
    tables: dict[str, list[tuple[str, int, int]]] = {}
    dropped = 0
    for entry in review:
        path = entry["path"]
        if entry.get("score") is None:
            dropped += 1  # unscorable: dropped, never counted as clean
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")
            mine[path] = smells_in_source(text)
            tables[path] = _function_table(text)
        except (SyntaxError, UnicodeDecodeError, OSError):
            dropped += 1
            continue
        scores[path] = entry["score"]
    theirs = cs_flags([e for e in review if e["path"] in scores], tables)
    return {
        "project": name,
        "status": "ok",
        "cs_version": _cs_version(),
        "seconds": round(time.time() - started, 1),
        "files": len(scores),
        "dropped": dropped,
        "scores": scores,
        "archy": _serializable(mine),
        "cs": _serializable(theirs["flags"]),
        "unresolved": theirs["unresolved"],
    }


def _append(ledger: Path, row: dict) -> None:
    # One write per row, newline included, so a crash cannot merge two rows.
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _done(ledger: Path) -> set[str]:
    if not ledger.exists():
        return set()
    rows = (json.loads(line) for line in ledger.read_text().splitlines() if line.strip())
    return {r["project"] for r in rows if r.get("status") == "ok"}


def report(rows: Iterable[dict], exclude: Iterable[str] = ()) -> str:
    """The pooled result. `exclude` drops the pilot projects, which the
    pre-registration keeps out of the result."""
    skip = set(exclude)
    rows = [r for r in rows if r.get("status") == "ok" and r["project"] not in skip]
    lines = [f"projects: {len(rows)}   files: {sum(r['files'] for r in rows)}"]
    pooled: dict[str, tuple[set, set]] = {s: (set(), set()) for s in SMELLS}
    xs: list[float] = []
    ys: list[float] = []
    for r in rows:
        for path in r["archy"]:
            for s in SMELLS:
                for fn in r["archy"][path][s]:
                    pooled[s][0].add((r["project"], path, *fn))
                for fn in r["cs"][path][s]:
                    pooled[s][1].add((r["project"], path, *fn))
            xs.append(float(sum(len(v) for v in r["archy"][path].values())))
            ys.append(r["scores"][path])
    for s in SMELLS:
        a, c = pooled[s]
        j = jaccard(a, c)
        lines.append(
            f"{s:38} archy {len(a):5}  cs {len(c):5}  both {len(a & c):5}  "
            f"jaccard {'n/a' if j is None else f'{j:.3f}'}"
        )
    rho = spearman(xs, ys)
    lines.append(f"file-level spearman (archy smell count vs cs score): {rho}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--only", nargs="*", default=None, help="project names to run")
    ap.add_argument("--exclude", nargs="*", default=[], help="pilot projects: skip and unreport")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)
    if args.report:
        rows = [json.loads(ln) for ln in args.ledger.read_text().splitlines() if ln.strip()]
        print(report(rows, args.exclude))
        return 0
    # `bench/` is not a package, so `_common` imports only once this directory is on
    # `sys.path`; and the `--report` path above must work without the clone machinery.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import clone_or_update, load_manifest

    done = _done(args.ledger)
    try:
        for proj in load_manifest():
            name = proj["name"]
            if (args.only and name not in args.only) or name in done or name in args.exclude:
                continue
            root = clone_or_update(proj)
            if root is None:
                _append(args.ledger, {"project": name, "status": "error", "reason": "clone failed"})
                continue
            try:
                _append(args.ledger, run_project(name, root))
            except Exception as exc:
                _append(args.ledger, {"project": name, "status": "error", "reason": repr(exc)})
    except KeyboardInterrupt:
        print(f"interrupted; re-run with --ledger {args.ledger} to resume", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
