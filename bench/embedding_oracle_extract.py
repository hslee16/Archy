"""Function-text extractor and shard writer for the #587 embedding oracle.

Reads no label and runs no model. For the PILOT (#548's 20 pilot PRs) and the
CALIBRATION subset (40 measured PRs, at most 5 per repository, drawn first with
`random.Random(587)` over the sorted PR keys) it parses the base and head trees with
archy's own parser, keeps the functions that clear the pre-registered floor and the
source-only filter, and writes text-only shards for a single CUDA GPU host. The
evaluation subset is NOT extractable here: the CLI has no way to name it.

Per PR it ships (a) every function whose def line falls in an added hunk (head tree) and
(b) every function that existed at base, outside the added hunks by construction. The
unit index also records the head functions that were NOT added, so the pair rules can
test existence at both base and head, but their text is not shipped.

Shard contract with the embedding job (text in, vectors out):

    bench/cache/emb587/pilot.jsonl, calibration.jsonl
        one `{"id": sha256(normalised text), "text": normalised text}` per line,
        unique ids, sorted by id, UTF-8, "\\n" newlines.
    bench/cache/emb587/SHARDS.json
        top-level "shards" map keyed pilot / calibration: file sha256, row count, PR keys,
        texts per PR, drops; and the extractor git sha.
    bench/cache/emb587/<shard>.units.jsonl
        STAYS ON THE MAC: which PR, side, path and qualified name map to which id.

`normalise` is the single definition of the embedded text. A text that occurs more than
once is shipped once.

archy:owns        Fn, audit_shard, check_disjoint, draw_calibration, extract_pr,
                  extract_tree, function_texts, git_sha, main, measured_rows,
                  normalise, reason_class, run_shard, text_id, write_shard
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import warnings
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

# bench/ is sys.path[0] when run as a script; the tests append it.
from diff_duplicates_precision import (  # ty: ignore[unresolved-import]  (bench/ is sys.path[0])
    MIN_NODES,
    _in_added,
    _path_excluded,
    _rel,
    added_line_ranges,
)
from pydantic import BaseModel, ConfigDict
from tree_sitter import Parser

from archy.complexity import PY_LANGUAGE, _walk_function_defs
from archy.graph import ScanTooLargeError, parse_project
from archy.scan_config import resolve_max_modules

SEED = 587
CALIBRATION_N = 40
CALIBRATION_REPO_CAP = 5
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "bench" / "diff_duplicates_data"
PILOT_LEDGER = REPO_ROOT / "bench" / "diff_duplicates_pilot.jsonl"
OUT = REPO_ROOT / "bench" / "cache" / "emb587"
SHARD_NAMES = ("pilot", "calibration")
NO_NEW_FUNCTION = "no new function clears the floor"


def normalise(text: str) -> str:
    """The embedded text: verbatim source, line endings made LF, trailing newlines cut.

    Nothing else. No identifier folding, no dedent, no docstring removal, no
    whitespace collapsing (pre-registration, "Generators": docstring included).
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def text_id(text: str) -> str:
    """sha256 of the normalised UTF-8 text; the id vectors are returned under."""
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


def function_texts(source: bytes) -> dict[int, str]:
    """`{1-indexed def line: whole source lines, def line through last line}`.

    Keyed like `FunctionComplexity.line` (the `def` line, not the decorator line). Whole
    lines, so a method keeps its indentation: verbatim, not dedented.
    """
    tree = Parser(PY_LANGUAGE).parse(source)
    lines = source.decode("utf-8", errors="replace").splitlines(keepends=True)
    out: dict[int, str] = {}

    def visit(node, _scope: tuple[str, ...]) -> None:
        first, last = node.start_point[0], node.end_point[0]
        out[first + 1] = normalise("".join(lines[first : last + 1]))

    _walk_function_defs(tree.root_node, source, visit)
    return out


class Fn(BaseModel):
    """One function of one tree, as the unit index records it."""

    model_config = ConfigDict(frozen=True)

    side: str
    path: str
    qualname: str
    line: int
    nodes: int
    shape: str
    id: str
    in_added: bool | None = None


class _Sized(NamedTuple):
    qualname: str
    line: int
    nodes: int
    shape: str


def _sized_source_functions(root: Path) -> Iterator[tuple[str, str, list[_Sized]]]:
    """`(relative path, absolute path, functions)` per source-only file with any function
    above the floor. The floor, the non-empty shape and the module walk are the ones
    `archy.duplicates._sized_functions` uses, so this and generator A agree on which
    functions exist."""
    modules, results = parse_project(root, max_modules=resolve_max_modules(root))
    path_by_qual = {m.qualname: str(m.path) for m in modules}
    for qual, result in sorted(results.items()):
        abs_path = path_by_qual.get(qual)
        if abs_path is None:
            continue
        rel = _rel(abs_path, root)
        if _path_excluded(rel):
            continue
        keep = [
            _Sized(f.qualified_name, f.line, f.size, f.shape_hash)
            for f in result.functions
            if f.shape_hash and f.size >= MIN_NODES
        ]
        if keep:
            yield rel, abs_path, keep


def extract_tree(
    root: Path, added: Mapping[str, list[tuple[int, int]]] | None
) -> tuple[list[Fn], dict[str, str]]:
    """Every function of `root` that clears the 30-node floor, in source-only paths.

    `added` (head only) marks the functions whose def line is added. Returns the
    functions and `{id: text}` for them.
    """
    side = "base" if added is None else "head"
    out: list[Fn] = []
    found: dict[str, str] = {}
    for rel, abs_path, keep in _sized_source_functions(root):
        texts = function_texts(Path(abs_path).read_bytes())
        for f in (f for f in keep if f.line in texts):
            fn_id = text_id(texts[f.line])
            found[fn_id] = texts[f.line]
            marked = None if added is None else _in_added(rel, f.line, added)
            out.append(
                Fn(
                    side=side,
                    path=rel,
                    qualname=f.qualname,
                    line=f.line,
                    nodes=f.nodes,
                    shape=f.shape,
                    id=fn_id,
                    in_added=marked,
                )
            )
    return out, found


# --- the draw ----------------------------------------------------------------------


def measured_rows(ledgers: Iterable[Path]) -> dict[str, dict]:
    """`{PR key: ledger row}` for the measured PRs: `status == "ok"` and not dropped."""
    rows: dict[str, dict] = {}
    for path in ledgers:
        for raw in path.read_text().splitlines():
            row = json.loads(raw)
            if row.get("status") == "ok" and "dropped" not in row:
                rows[str(row["key"])] = row
    return rows


def draw_calibration(
    repo_by_key: Mapping[str, str],
    n: int = CALIBRATION_N,
    cap: int = CALIBRATION_REPO_CAP,
    seed: int = SEED,
) -> list[str]:
    """The first `n` PR keys of a `random.Random(seed)` shuffle of the SORTED keys,
    skipping a PR once its repository already has `cap` (same shape as #548's draw)."""
    ordered = sorted(repo_by_key)
    random.Random(seed).shuffle(ordered)
    taken: Counter[str] = Counter()
    picked: list[str] = []
    for key in ordered:
        repo = repo_by_key[key]
        if taken[repo] >= cap:
            continue
        taken[repo] += 1
        picked.append(key)
        if len(picked) == n:
            break
    return picked


def check_disjoint(pilot: Iterable[str], calibration: Iterable[str]) -> None:
    both = set(pilot) & set(calibration)
    if both:
        raise AssertionError(f"pilot and calibration overlap: {sorted(both)}")


# --- shards ------------------------------------------------------------------------


def write_shard(
    directory: Path, name: str, texts: Mapping[str, str], units: Sequence[dict]
) -> dict:
    """`<name>.jsonl` (text only, unique ids, sorted by id) and `<name>.units.jsonl`."""
    directory.mkdir(parents=True, exist_ok=True)
    body = "".join(
        json.dumps({"id": k, "text": texts[k]}, ensure_ascii=False) + "\n" for k in sorted(texts)
    )
    shard = directory / f"{name}.jsonl"
    shard.write_bytes(body.encode("utf-8"))
    (directory / f"{name}.units.jsonl").write_text(
        "".join(json.dumps(u, sort_keys=True) + "\n" for u in units)
    )
    return {
        "file": shard.name,
        "sha256": hashlib.sha256(shard.read_bytes()).hexdigest(),
        "rows": len(texts),
    }


def audit_shard(path: Path) -> None:
    """Re-read a written shard and assert the contract (the embedding job relies on all of it)."""
    raw = path.read_bytes()
    assert b"\r" not in raw, "raw CR in shard"
    ids: list[str] = []
    for line in raw.decode("utf-8").split("\n")[:-1]:
        row = json.loads(line)
        assert set(row) == {"id", "text"}, "unexpected fields"
        assert row["id"] == text_id(row["text"]), "id is not sha256 of the text"
        assert row["text"] == normalise(row["text"]), "text is not normalised"
        ids.append(row["id"])
    assert ids == sorted(ids), "ids are not sorted"
    assert len(ids) == len(set(ids)), "duplicate ids"
    assert raw.endswith(b"\n") or not raw, "missing final newline"


def git_sha() -> str:
    run = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    dirty = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "status",
            "--porcelain",
            "--untracked-files=no",
            "--",
            "bench",
            "src",
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return run.stdout.strip() + ("+dirty" if dirty else "")


# --- one PR ------------------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout


def _checkout(root: Path, sha: str) -> None:
    _git(root, "checkout", "--quiet", "--force", "--detach", sha)
    _git(root, "clean", "-fdxq")


def _drop_reason(exc: Exception) -> str:
    """A failure while measuring one PR, as the drop reason recorded for it."""
    if isinstance(exc, subprocess.CalledProcessError):
        return f"git: {(exc.stderr or str(exc.returncode)).strip()[:80]}"
    if isinstance(exc, ScanTooLargeError):
        return "scan too large"
    return f"{type(exc).__name__}: {str(exc)[:80]}"  # a parse crash is a drop, not a clean PR


def extract_pr(root: Path, base: str, head: str) -> dict:
    """The PR's functions at base and head, or `{"dropped": reason}` (never scored clean)."""
    try:
        diff = _git(root, "diff", "-U0", "--no-renames", base, head, "--", "*.py")
        added = added_line_ranges(diff)
        if not added:
            return {"dropped": "no added python lines"}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _checkout(root, base)
            base_fns, base_texts = extract_tree(root, None)
            _checkout(root, head)
            head_fns, head_texts = extract_tree(root, added)
    except Exception as exc:
        return {"dropped": _drop_reason(exc)}
    if not any(f.in_added for f in head_fns):
        return {"dropped": NO_NEW_FUNCTION}
    shipped_head = {f.id: head_texts[f.id] for f in head_fns if f.in_added}
    return {
        "fns": [f.model_dump() for f in (*base_fns, *head_fns)],
        "texts": {**base_texts, **shipped_head},
    }


# --- driver ------------------------------------------------------------------------


def _clone_and_pin(row: dict) -> Path | str:
    """The clone with the ledger's `head` fetched, or a drop reason."""
    sys.path.append(str(REPO_ROOT / "bench"))
    import diff_duplicates_pr as pr  # ty: ignore[unresolved-import]  (bench/ is sys.path[0])

    ref = pr.PoolPR(
        id=int(row["key"]),
        number=row["number"],
        repo=row["repo"],
        agent=row["agent"],
        merged=row["merged"],
    )
    try:
        repo_dir = pr._ensure_clone(ref)
        fetched = pr._fetch_head(ref, repo_dir)
    except pr.Unresolvable as exc:
        return f"unresolvable: {exc}"
    if fetched != row["head"]:
        return f"head moved: ledger {row['head'][:8]}, fetched {fetched[:8]}"
    return repo_dir


def reason_class(reason: str | None) -> str:
    """`measured`, or the drop reason up to its first colon. A PR with no new function above
    the floor was still analysed by #548, so it is `measured` there and here."""
    if reason is None or reason == NO_NEW_FUNCTION:
        return "measured"
    return reason.split(":")[0]


def run_shard(name: str, rows: Sequence[dict]) -> dict:
    work = OUT / "work" / name
    work.mkdir(parents=True, exist_ok=True)
    texts: dict[str, str] = {}
    units: list[dict] = []
    per_pr: dict[str, int] = {}
    drops: dict[str, str] = {}
    reproduction: dict[str, dict[str, str]] = {}
    for row in sorted(rows, key=lambda r: str(r["key"])):
        key = str(row["key"])
        cached = work / f"{key}.json"
        if not cached.exists():
            target = _clone_and_pin(row)
            result = (
                {"dropped": target}
                if isinstance(target, str)
                else extract_pr(target, row["base"], row["head"])
            )
            cached.write_text(json.dumps(result))
        result = json.loads(cached.read_text())
        reproduction[key] = {
            "ledger": reason_class(row.get("dropped")),
            "mine": reason_class(result.get("dropped")),
        }
        if "dropped" in result:
            drops[key] = result["dropped"]
            continue
        texts.update(result["texts"])
        per_pr[key] = len(result["texts"])
        units += [{"pr": key, "repo": row["repo"], **f} for f in result["fns"]]
    info = write_shard(OUT, name, texts, units)
    audit_shard(OUT / f"{name}.jsonl")
    mismatches = {k: v for k, v in reproduction.items() if v["ledger"] != v["mine"]}
    return {
        **info,
        "pr_keys": sorted(per_pr),
        "texts_per_pr": per_pr,
        "drops": drops,
        "reproduction": reproduction,
        "reproduction_mismatches": mismatches,
    }


def _rows(names: Sequence[str]) -> dict[str, list[dict]]:
    ledgers = sorted(DATA.glob("precision.*.jsonl"))
    measured = measured_rows(ledgers)
    pilot = {
        str(json.loads(r)["key"]): json.loads(r) for r in PILOT_LEDGER.read_text().splitlines()
    }
    check_disjoint(pilot, measured)
    picked = draw_calibration({k: v["repo"] for k, v in measured.items()})
    all_rows = {
        "pilot": list(pilot.values()),
        "calibration": [measured[k] for k in picked],
    }
    check_disjoint((str(r["key"]) for r in all_rows["pilot"]), picked)
    assert len(picked) == CALIBRATION_N, f"drew {len(picked)} calibration PRs"
    return {n: all_rows[n] for n in names}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--shard", choices=SHARD_NAMES, action="append")
    parser.add_argument("--plan", action="store_true", help="print the draw and stop")
    args = parser.parse_args(argv)
    names = args.shard or list(SHARD_NAMES)
    rows = _rows(names)
    if args.plan:
        for name, rs in rows.items():
            print(name, len(rs), [str(r["key"]) for r in rs])
        return 0
    shards = {name: run_shard(name, rs) for name, rs in rows.items()}
    manifest = OUT / "SHARDS.json"
    prior = json.loads(manifest.read_text()) if manifest.exists() else {}
    manifest.write_text(
        json.dumps(
            {
                **prior,
                "extractor_git_sha": git_sha(),
                "shards": {**prior.get("shards", {}), **shards},
            },
            indent=2,
            sort_keys=True,
        )
    )
    clashes = {
        n: s["reproduction_mismatches"] for n, s in shards.items() if s["reproduction_mismatches"]
    }
    if clashes:
        print(f"REPRODUCTION MISMATCH against the #548 ledger: {clashes}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
