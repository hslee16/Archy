"""Function-text extractor and shard writer for the #587 embedding oracle.

Reads no label and runs no model. For the PILOT (#548's 20 pilot PRs) and the
CALIBRATION subset (40 measured PRs, at most 5 per repository, drawn first with
`random.Random(587)` over the sorted PR keys) it parses the base and head trees with
archy's own parser, keeps the functions that clear the pre-registered floor and the
source-only filter, and writes text-only shards for a single CUDA GPU host. The
evaluation subset is NOT extractable by this CLI: `--shard` cannot name it. It has its own
command, `embedding_oracle_evaluation`, which refuses any draw but the registered one.

Per PR it ships (a) every function whose def line falls in an added hunk (head tree) and
(b) every function that existed at base, outside the added hunks by construction. The
unit index also records the head functions that were NOT added, so the pair rules can
test existence at both base and head, but their text is not shipped.

Shard contract with the embedding job (text in, vectors out):

    bench/cache/emb587/pilot.jsonl, calibration.jsonl
        one `{"id": sha256(normalised text), "text": normalised text}` per line,
        unique ids, sorted by id. ASCII-escaped JSON, so a reader that splits on any
        Unicode line separator still sees one row per line; split on "\\n" only.
    bench/cache/emb587/SHARDS.json
        top-level "shards" map keyed pilot / calibration: file sha256, row count, PR keys,
        texts per PR, drops, the extractor version and git sha that produced THAT shard;
        and a top-level "ok" that is false when the #548 reproduction or the parity check
        found a mismatch.
    bench/cache/emb587/<shard>.units.jsonl
        STAYS ON THE MAC. One row per FLOOR-CLEARING function per tree. The join key is
        (pr, side, path, qualname, occurrence): `occurrence` numbers same-named functions
        of one file by line, within ONE tree, so the key is unique but base occurrence k
        is not head occurrence k. Head rows that were not added are not shipped as head
        text: their id is in the shard only when the same text exists at base (an unchanged
        function) or is shipped elsewhere, so join by the key, never by id, and treat an
        id with no vector as "not shipped". `counterpart` says a function with the same
        path and qualified name exists in the other tree at ANY size, so "exists at base
        and head" can be tested even for a function that shrank below the floor. A name
        that occurs more than once in a file is ambiguous: a consumer must not pair such a
        function by name, and must exclude self-pairs by (path, qualname), never by id.

`normalise` is the single definition of the embedded text. A text that occurs more than
once is shipped once.

archy:owns        Fn, audit_shard, check_disjoint, draw_calibration, extract_pr,
                  extract_tree, function_texts, git_sha, is_final, main, measured_rows,
                  normalise, parity_mismatches, pr_result, reason_class,
                  run_and_record, run_shard, text_id, write_shard
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import warnings
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path

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
# Bump when what a PR's cached result CONTAINS changes; a cache entry from another version is
# refused and recomputed, so a code fix can never be served stale results.
EXTRACT_VERSION = "2"
GIT_TIMEOUT_S = 900
# Drops that a re-run would reproduce. Any other drop (a git failure, a moved head, an
# unreachable repository, a timeout) may be transient, so it is never cached: only `ok`
# results are done, and the rest are retried on the next run.
DETERMINISTIC_DROPS = frozenset(
    {"no added python lines", NO_NEW_FUNCTION, "QualnameCollisionError", "scan too large"}
)


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
    lines (split on "\\n" only), so a method keeps its indentation: verbatim, not dedented.
    """
    tree = Parser(PY_LANGUAGE).parse(source)
    # Split on b"\n" ONLY. tree-sitter rows count "\n" and nothing else, while
    # `str.splitlines` also splits on form feed, U+2028 and lone CR and would shift every
    # slice after one (a form feed in a comment is enough).
    lines = source.split(b"\n")
    out: dict[int, str] = {}

    def visit(node, _scope: tuple[str, ...]) -> None:
        first, last = node.start_point[0], node.end_point[0]
        out[first + 1] = normalise(b"\n".join(lines[first : last + 1]).decode("utf-8", "replace"))

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
    occurrence: int = 0
    counterpart: bool = False


class _Sized(BaseModel):
    model_config = ConfigDict(frozen=True)

    qualname: str
    line: int
    nodes: int
    shape: str


def _source_functions(root: Path) -> Iterator[tuple[str, str, list[_Sized]]]:
    """`(relative path, absolute path, every function)` per source-only file with any function.

    The module walk and the source-only filter are the ones `archy.duplicates` uses, so this
    and generator A agree on which files exist. The 30-node floor is applied by the caller:
    existence is recorded for functions of every size.
    """
    modules, results = parse_project(root, max_modules=resolve_max_modules(root))
    path_by_qual = {m.qualname: str(m.path) for m in modules}
    for qual, result in sorted(results.items()):
        abs_path = path_by_qual.get(qual)
        if abs_path is None:
            continue
        rel = _rel(abs_path, root)
        if _path_excluded(rel) or not result.functions:
            continue
        yield (
            rel,
            abs_path,
            [
                _Sized(
                    qualname=f.qualified_name, line=f.line, nodes=f.size, shape=f.shape_hash or ""
                )
                for f in result.functions
            ],
        )


def _clears_floor(f: _Sized) -> bool:
    """The floor and the non-empty shape `archy.duplicates._sized_functions` requires."""
    return bool(f.shape) and f.nodes >= MIN_NODES


def extract_tree(
    root: Path, added: Mapping[str, list[tuple[int, int]]] | None
) -> tuple[list[Fn], dict[str, str], set[tuple[str, str]]]:
    """Every function of `root` that clears the 30-node floor, in source-only paths.

    `added` (head only) marks the functions whose def line is added. Returns the functions,
    `{id: text}` for them, and the `(path, qualname)` of EVERY function of any size, which
    is what `counterpart` is tested against.
    """
    side = "base" if added is None else "head"
    out: list[Fn] = []
    found: dict[str, str] = {}
    names: set[tuple[str, str]] = set()
    seen: Counter[tuple[str, str]] = Counter()
    for rel, abs_path, functions in _source_functions(root):
        names.update((rel, f.qualname) for f in functions)
        texts = function_texts(Path(abs_path).read_bytes())
        for f in sorted(functions, key=lambda f: f.line):
            occurrence = seen[(rel, f.qualname)]
            seen[(rel, f.qualname)] += 1
            if not _clears_floor(f) or f.line not in texts:
                continue
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
                    occurrence=occurrence,
                )
            )
    return out, found, names


def _with_counterpart(fns: Sequence[Fn], other: set[tuple[str, str]]) -> list[Fn]:
    """Each function marked with whether the OTHER tree has a function of that path and name."""
    return [f.model_copy(update={"counterpart": (f.path, f.qualname) in other}) for f in fns]


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


def _atomic_write(path: Path, data: bytes) -> None:
    """Write beside the target and rename over it, so a crash never leaves a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_shard(
    directory: Path, name: str, texts: Mapping[str, str], units: Sequence[dict]
) -> dict:
    """`<name>.jsonl` (text only, unique ids, sorted by id) and `<name>.units.jsonl`."""
    body = "".join(json.dumps({"id": k, "text": texts[k]}) + "\n" for k in sorted(texts))
    shard = directory / f"{name}.jsonl"
    _atomic_write(shard, body.encode("utf-8"))
    _atomic_write(
        directory / f"{name}.units.jsonl",
        "".join(json.dumps(u, sort_keys=True) + "\n" for u in units).encode("utf-8"),
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
    """The repository HEAD, marked `+dirty` when tracked files under bench or src changed."""
    head = _git(REPO_ROOT, "rev-parse", "HEAD").strip()
    dirty = _git(
        REPO_ROOT, "status", "--porcelain", "--untracked-files=no", "--", "bench", "src"
    ).strip()
    return head + ("+dirty" if dirty else "")


# --- one PR ------------------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=GIT_TIMEOUT_S,
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
            base_fns, base_texts, base_names = extract_tree(root, None)
            _checkout(root, head)
            head_fns, head_texts, head_names = extract_tree(root, added)
    except Exception as exc:
        return {"dropped": _drop_reason(exc)}
    if not any(f.in_added for f in head_fns):
        return {"dropped": NO_NEW_FUNCTION}
    shipped_head = {f.id: head_texts[f.id] for f in head_fns if f.in_added}
    return {
        "fns": [
            f.model_dump()
            for f in (
                *_with_counterpart(base_fns, head_names),
                *_with_counterpart(head_fns, base_names),
            )
        ],
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


def is_final(result: dict) -> bool:
    """Whether a re-run would reproduce `result`: an `ok` result or a deterministic drop.

    Only a final result is cached. A git failure, a moved head, an unreachable repository
    or a timeout may be transient, so it is recomputed on the next run.
    """
    reason = result.get("dropped")
    return (
        reason is None
        or reason in DETERMINISTIC_DROPS
        or reason_class(reason) in DETERMINISTIC_DROPS
    )


def _measure(row: dict) -> dict:
    """One PR's result. Any failure is a drop with its reason, never an exception: one PR
    must not end a run that has been going for an hour."""
    try:
        target = _clone_and_pin(row)
        if isinstance(target, str):
            return {"dropped": target}
        return extract_pr(target, row["base"], row["head"])
    except Exception as exc:
        return {"dropped": _drop_reason(exc)}


def _load_cached(path: Path, row: dict) -> dict | None:
    """The cached result, or None when it cannot be trusted for THIS row.

    Refused: a missing or torn file, a malformed entry, another extractor version, an entry
    made on a `+dirty` tree (not reproducible), and an entry for another base or head commit
    of the same PR key.
    """
    try:
        entry = json.loads(path.read_text())
        trusted = (
            entry["version"] == EXTRACT_VERSION
            and not str(entry["extractor_git_sha"]).endswith("+dirty")
            and (entry["base"], entry["head"]) == (row["base"], row["head"])
        )
        return entry["result"] if trusted else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def pr_result(work: Path, row: dict, sha: str) -> dict:
    """The PR's result from the cache, or measured now (and cached when it is final)."""
    path = work / f"{row['key']}.json"
    cached = _load_cached(path, row)
    if cached is not None:
        return cached
    result = _measure(row)
    if is_final(result):
        entry = {
            "version": EXTRACT_VERSION,
            "extractor_git_sha": sha,
            "base": row["base"],
            "head": row["head"],
            "result": result,
        }
        _atomic_write(path, json.dumps(entry).encode("utf-8"))
    return result


def _ledger_members(row: dict) -> list[tuple[str, str, int, bool]]:
    """`(path, qualified name, def line, new)` of every member of every #548 firing of `row`."""
    return [
        (m["path"], m["qualified_name"], m["line"], bool(m["new"]))
        for firing in row.get("firings", [])
        for m in firing["members"]
    ]


def parity_mismatches(row: dict, result: dict) -> list[str]:
    """Ledger firing members the extractor's head rows do not contain, as readable strings.

    #548's firings were computed at the head tree: a NEW member is a head function whose def
    line is added, an old one is a head function that is not. Both must be among the rows
    this extractor wrote, or generator A and generator B disagree on which functions exist.
    A PR with firings and NO extractor rows (it was dropped, or no new function cleared the
    floor) is the clearest disagreement, so every member is reported there.

    One direction only: this never tests the extractor's functions against the ledger, and it
    can only compare the PRs that have a firing (see `parity_checked` in the manifest).
    """
    wanted = _ledger_members(row)
    head = {
        (f["path"], f["qualname"], f["line"], bool(f["in_added"]))
        for f in result.get("fns", [])
        if f["side"] == "head"
    }
    return [f"{p}:{q}@{ln} new={n}" for p, q, ln, n in wanted if (p, q, ln, n) not in head]


def _gather(results: Mapping[str, tuple[dict, dict]]) -> dict:
    """The shard's texts, unit rows, per-PR counts and drops from the per-PR results."""
    texts: dict[str, str] = {}
    units: list[dict] = []
    per_pr: dict[str, int] = {}
    drops: dict[str, str] = {}
    for key, (row, result) in results.items():
        if "dropped" in result:
            drops[key] = result["dropped"]
            continue
        texts.update(result["texts"])
        per_pr[key] = len(result["texts"])
        units += [{"pr": key, "repo": row["repo"], **f} for f in result["fns"]]
    return {"texts": texts, "units": units, "per_pr": per_pr, "drops": drops}


def run_shard(name: str, rows: Sequence[dict]) -> dict:
    sha = git_sha()
    work = OUT / "work" / name
    work.mkdir(parents=True, exist_ok=True)
    results = {
        str(r["key"]): (r, pr_result(work, r, sha))
        for r in sorted(rows, key=lambda r: str(r["key"]))
    }
    got = _gather(results)
    info = write_shard(OUT, name, got["texts"], got["units"])
    audit_shard(OUT / f"{name}.jsonl")
    reproduction = {
        k: {"ledger": reason_class(r.get("dropped")), "mine": reason_class(res.get("dropped"))}
        for k, (r, res) in results.items()
    }
    parity = {k: m for k, (r, res) in results.items() if (m := parity_mismatches(r, res))}
    checked = [len(_ledger_members(r)) for r, _ in results.values() if r.get("firings")]
    return {
        **info,
        "extractor_version": EXTRACT_VERSION,
        "extractor_git_sha": sha,
        "pr_keys": sorted(got["per_pr"]),
        "texts_per_pr": got["per_pr"],
        "drops": got["drops"],
        "reproduction": reproduction,
        "reproduction_mismatches": {
            k: v for k, v in reproduction.items() if v["ledger"] != v["mine"]
        },
        "parity_mismatches": parity,
        "parity_checked": {"prs_with_firings": len(checked), "members": sum(checked)},
    }


def _rows(names: Sequence[str]) -> dict[str, list[dict]]:
    ledgers = sorted(DATA.glob("precision.*.jsonl"))
    measured = measured_rows(ledgers)
    parsed = [json.loads(r) for r in PILOT_LEDGER.read_text().splitlines()]
    pilot = {str(r["key"]): r for r in parsed}
    check_disjoint(pilot, measured)
    picked = draw_calibration({k: v["repo"] for k, v in measured.items()})
    all_rows = {
        "pilot": list(pilot.values()),
        "calibration": [measured[k] for k in picked],
    }
    check_disjoint((str(r["key"]) for r in all_rows["pilot"]), picked)
    assert len(picked) == CALIBRATION_N, f"drew {len(picked)} calibration PRs"
    return {n: all_rows[n] for n in names}


def _load_manifest() -> dict:
    manifest = OUT / "SHARDS.json"
    return json.loads(manifest.read_text()) if manifest.exists() else {}


def _write_manifest(shards: Mapping[str, dict]) -> bool:
    """Merge `shards` into SHARDS.json, atomically; True when the manifest is `ok`.

    `ok` needs every shard entry, including one kept from an earlier run, to be at this
    EXTRACT_VERSION with no #548 class mismatch and no parity mismatch: a manifest that mixes
    versions or hides a mismatch is not good. Top-level keys this module does not own (for
    example a `sample_repos` block added by a consumer) are kept; the old top-level
    `extractor_git_sha` is dropped, since the sha is recorded per shard.
    """
    prior = _load_manifest()
    prior.pop("extractor_git_sha", None)
    merged = {**prior.get("shards", {}), **shards}
    ok = all(
        e.get("extractor_version") == EXTRACT_VERSION
        and not e.get("reproduction_mismatches")
        and not e.get("parity_mismatches")
        for e in merged.values()
    )
    body = json.dumps({**prior, "ok": ok, "shards": merged}, indent=2, sort_keys=True)
    _atomic_write(OUT / "SHARDS.json", body.encode("utf-8"))
    return ok


def _invalidate_manifest() -> None:
    """Mark an existing manifest not `ok` BEFORE a shard's files are replaced, so a run that is
    interrupted between that write and the manifest write is never read as good. Called before
    every shard: recording one shard recomputes `ok`, which would otherwise undo it."""
    if (OUT / "SHARDS.json").exists():
        body = json.dumps({**_load_manifest(), "ok": False}, indent=2, sort_keys=True)
        _atomic_write(OUT / "SHARDS.json", body.encode("utf-8"))


def _run_all(rows: Mapping[str, list[dict]]) -> dict[str, dict] | None:
    """Every shard's result, or None after an interrupt (finished PRs stay cached)."""
    shards: dict[str, dict] = {}
    try:
        for name, rs in rows.items():
            _invalidate_manifest()  # before THIS shard's files are replaced, every time
            shards[name] = run_shard(name, rs)
            _write_manifest({name: shards[name]})  # each shard is recorded as soon as it audits
    except KeyboardInterrupt:
        print(
            f"interrupted. Finished PRs are cached under {OUT / 'work'}; "
            "run the same command again to resume.",
            file=sys.stderr,
        )
        return None
    return shards


def _report_mismatches(shards: Mapping[str, dict]) -> None:
    for name, shard in shards.items():
        for kind in ("reproduction_mismatches", "parity_mismatches"):
            if shard[kind]:
                print(f"{kind.upper()} in {name}: {shard[kind]}", file=sys.stderr)


def run_and_record(rows: Mapping[str, list[dict]]) -> int:
    """Extract the named shards, record them in the manifest, and return the exit code:
    0 done, 130 interrupted, 3 a #548 reproduction or parity mismatch."""
    shards = _run_all(rows)
    if shards is None:
        return 130
    if _write_manifest(shards):
        return 0
    _report_mismatches(shards)
    return 3


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--shard", choices=SHARD_NAMES, action="append")
    parser.add_argument("--plan", action="store_true", help="print the draw and stop")
    args = parser.parse_args(argv)
    rows = _rows(args.shard or list(SHARD_NAMES))
    if args.plan:
        for name, rs in rows.items():
            print(name, len(rs), [str(r["key"]) for r in rs])
        return 0
    return run_and_record(rows)


if __name__ == "__main__":
    raise SystemExit(main())
