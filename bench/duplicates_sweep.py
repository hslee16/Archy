"""Sweep `archy duplicates` across the bench projects at several
`--min-nodes` thresholds and report how the finding volume falls as the
threshold rises.

There is no labelled ground truth for "these functions are copy-paste
duplicates" on the bench, so this script does NOT prove correctness. It
measures one thing: **threshold sensitivity of the finding volume**, so the
default `--min-nodes` can be picked at the knee where trivial-stub clusters
(short generated bodies: dataclass shims, adapter one-liners, Pydantic
`@validator` boilerplate) drop out but real duplication remains. This is the
volume half of the false-positive gate; the accuracy half is a manual
spot-check (15 random clusters each on a diverse trio of projects,
hand-classified true-duplicate vs FP) written up alongside in
`bench/duplicates_results.md` -> "FP spot-check", mirroring the rejected
dead-code study in `docs/research/RESEARCH_METRICS.md` section 12.

Output: a markdown report at `bench/duplicates_results.md` with a per-project
table (group count + duplicated-function count per threshold) and aggregate
medians.

Limitations (these bound what the numbers prove; issue #133):

* **Volume, not accuracy.** A falling group count as `--min-nodes` rises shows
  the threshold suppresses short-body clusters; it does NOT show the surviving
  clusters are genuine duplication. The manual spot-check is what closes that
  gap; this script only sizes the haystack.
* **No cross-threshold identity tracking.** The script counts groups per
  threshold independently; it does not track which specific clusters survive a
  threshold increase, so it cannot say whether the drop is boilerplate leaving
  or real duplication being lost. That per-cluster survival analysis is future
  work.
* **Scans `src_dir` only.** Like `bench/run.py`, each project is scanned at its
  pinned source dir, so vendored code and test suites (a rich source of
  legitimately-repetitive fixtures) are excluded; the numbers understate
  whole-repo duplication.
* **Shape-hash blindness carries through.** Two functions with identical
  control-flow shape but different semantics cluster here exactly as they do in
  the tool; the sweep inherits that advisory-not-proof caveat.

Usage:
    uv run --with pyyaml python bench/duplicates_sweep.py
    uv run --with pyyaml python bench/duplicates_sweep.py --stdout
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import subprocess
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "bench" / "projects.yaml"
RESULTS = REPO_ROOT / "bench" / "duplicates_results.md"
WORKDIR = Path("/tmp/archy_bench")

# The swept axis: minimum normalized AST-node count. The default the tool ships
# with should sit at the knee where trivial-stub clusters have dropped out.
THRESHOLDS: tuple[int, ...] = (5, 10, 20, 40)
TOP = 100000  # effectively unbounded; we want the full count, not a top-K view


def load_manifest() -> list[dict]:
    return yaml.safe_load(MANIFEST.read_text())["projects"]


def clone_or_update(proj: dict) -> Path | None:
    """Mirrors bench/hotspots_sweep.py's clone_or_update; never raises -- on any
    failure we skip the project so the sweep keeps going. The archy self-entry
    uses REPO_ROOT directly."""
    name = proj["name"]
    sha = proj["sha"]
    if name == "archy":
        return REPO_ROOT
    target = WORKDIR / name
    if not target.exists():
        WORKDIR.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            ["git", "clone", "--quiet", f"https://github.com/{proj['repo']}.git", str(target)],
        )
        if res.returncode != 0:
            return None
    has_sha = subprocess.run(
        ["git", "-C", str(target), "cat-file", "-e", sha],
        capture_output=True,
    )
    if has_sha.returncode != 0:
        subprocess.run(["git", "-C", str(target), "fetch", "--quiet", "origin"], check=False)
    if (
        subprocess.run(
            ["git", "-C", str(target), "checkout", "--quiet", sha],
        ).returncode
        != 0
    ):
        return None
    subprocess.run(["git", "-C", str(target), "reset", "--hard", "--quiet", sha], check=False)
    subprocess.run(["git", "-C", str(target), "clean", "-fdx", "--quiet"], check=False)
    return target


def run_duplicates(src: Path, *, min_nodes: int) -> dict:
    """Invoke `archy duplicates <src> --min-nodes N --top TOP --format json`.
    Returns the parsed payload; empty-ish on any failure."""
    cmd = [
        "uv",
        "run",
        "archy",
        "duplicates",
        str(src),
        "--min-nodes",
        str(min_nodes),
        "--top",
        str(TOP),
        "--format",
        "json",
    ]
    res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        return {"total": 0, "duplicated_functions": 0}
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return {"total": 0, "duplicated_functions": 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="write the markdown report to stdout instead of bench/duplicates_results.md",
    )
    args = parser.parse_args()

    manifest = load_manifest()
    print(
        f"# duplicates sweep over {len(manifest)} projects, "
        f"min-nodes thresholds: {', '.join(str(t) for t in THRESHOLDS)}",
        file=sys.stderr,
    )

    rows: list[dict] = []
    for proj in manifest:
        name = proj["name"]
        print(f"# {name:13s}", end="", file=sys.stderr, flush=True)
        root = clone_or_update(proj)
        if root is None:
            print("  SKIP (clone/checkout failed)", file=sys.stderr)
            continue
        src = root / proj.get("src_dir", ".")
        if not src.exists():
            print(f"  SKIP (missing src_dir {proj.get('src_dir')})", file=sys.stderr)
            continue
        try:
            payloads = {t: run_duplicates(src, min_nodes=t) for t in THRESHOLDS}
        except Exception as exc:
            print(f"  SKIP (sweep error: {exc})", file=sys.stderr)
            continue
        row = {"name": name, "sha": proj["sha"]}
        for t in THRESHOLDS:
            row[f"groups_{t}"] = int(payloads[t].get("total", 0))
            row[f"dupfns_{t}"] = int(payloads[t].get("duplicated_functions", 0))
        rows.append(row)
        print(
            "  " + " ".join(f"n{t}={row[f'groups_{t}']}" for t in THRESHOLDS),
            file=sys.stderr,
        )

    out_lines: list[str] = []

    def emit(line: str = "") -> None:
        out_lines.append(line)

    today = dt.date.today().isoformat()
    emit("# Duplicate-function threshold sweep")
    emit()
    emit(f"Output of `uv run --with pyyaml python bench/duplicates_sweep.py`. Captured {today}.")
    emit()
    emit(
        "`groups_N` = number of duplicate clusters at `--min-nodes N`; "
        "`dupfns_N` = total functions across those clusters. Each project is "
        "scanned at its pinned `src_dir`. Pick the shipping default at the knee "
        "where short-stub clusters have dropped but real duplication remains; "
        "confirm with the manual FP spot-check below."
    )
    emit()
    emit("## Per-project results")
    emit()
    cols = (
        ["project", "sha"]
        + [f"groups@{t}" for t in THRESHOLDS]
        + [f"dupfns@{t}" for t in THRESHOLDS]
    )
    emit("| " + " | ".join(cols) + " |")
    emit("| " + " | ".join("---" if c in ("project", "sha") else "---:" for c in cols) + " |")
    for r in rows:
        cells = [r["name"], f"`{r['sha']}`"]
        cells += [str(r[f"groups_{t}"]) for t in THRESHOLDS]
        cells += [str(r[f"dupfns_{t}"]) for t in THRESHOLDS]
        emit("| " + " | ".join(cells) + " |")

    emit()
    emit("## Aggregate (median across projects)")
    emit()

    def median(key: str) -> str:
        vals = [r[key] for r in rows if isinstance(r[key], int)]
        return f"{statistics.median(vals):.1f}" if vals else "n/a"

    emit("| min-nodes | median groups | median dup fns |")
    emit("| ---: | ---: | ---: |")
    for t in THRESHOLDS:
        emit(f"| {t} | {median(f'groups_{t}')} | {median(f'dupfns_{t}')} |")

    emit()
    emit("## FP spot-check")
    emit()
    emit(
        "_Manual, not produced by this script._ At the chosen default, draw 15 "
        "random clusters each from a diverse trio (e.g. fastapi / pytest / "
        "django), hand-classify true-duplicate vs false-positive, and record the "
        "N/15 rate plus the dominant FP taxonomy. This is the accuracy half of "
        "the gate; the rejected dead-code study (RESEARCH_METRICS.md section 12) "
        "is the template."
    )

    report = "\n".join(out_lines) + "\n"
    if args.stdout:
        sys.stdout.write(report)
    else:
        RESULTS.write_text(report)
        print(f"# wrote {RESULTS.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
