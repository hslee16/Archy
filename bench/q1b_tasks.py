#!/usr/bin/env python
"""Select structurally-risky tasks for the Q1b agent A/B (#348).

    uv run python bench/q1b_tasks.py --out bench/q1b_tasks.json
    uv run python bench/q1b_tasks.py --profile        # corpus stats only

Q1b asks whether archy-in-the-loop reduces structurally-bad edits. The protocol
in `docs/research/INLOOP_PREVALENCE_EMPIRICS.md` requires tasks **enriched for
structural risk**, because Q1a measured the per-commit cycle rate at 0.5% and a
uniform sample would need an impractical N.

This picks those tasks out of SWE-bench by parsing each instance's *gold* patch
and keeping the multi-file, multi-directory ones. Data comes from the
HuggingFace datasets-server JSON API, so there is no `datasets`/`pyarrow`
dependency and no local dataset copy.

## The caveat that must not be lost between here and the results

**SWE-bench is bug fixes by construction, and its patches are small.** Measured
2026-07-25:

| corpus | n | median `.py` files | >=3 files & >=2 dirs |
| --- | --- | --- | --- |
| SWE-bench Verified | 500 | 1.0 | 20 (4.0%) |
| SWE-bench (full) | 2294 | 1.0 | ~7.9% |

Q1a found structural regressions concentrate in commits touching a **median of
7** `.py` files. Even after filtering, the qualifying SWE-bench tasks sit around
3-4 files. So this corpus reaches the *low end* of the risky regime at best, and
it is drawn from the localized bug-fix class that the causal synthesis expects
agents to handle fine.

That is why the first run is a **p_B pilot, not the A/B**: measure how often an
unaided agent produces a structurally-bad diff here, and only then decide
whether the A/B is powered. Pre-registered reading of that pilot:

- **p_B >= 25%** -> the A/B is feasible on this corpus at ~80-130 pairs.
- **p_B <= 10%** -> **the corpus is wrong, not archy.** Do not report that as
  evidence archy is unnecessary. It means SWE-bench-class tasks do not reach the
  regime where the signal lives, and a harder corpus (real multi-file feature
  and refactor commits) is required. Recording this *before* seeing the number
  is the point; the same discipline is what stopped #289 shipping on an N=10
  hint.

archy:owns        fetch_rows, main, patch_files, profile, select
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API = "https://datasets-server.huggingface.co"
DEFAULT_DATASET = "princeton-nlp/SWE-bench"
PAGE = 100

# `diff --git a/X b/X` is the only reliable per-file marker in a unified diff;
# `+++` lines lie for renames and /dev/null for adds.
FILE_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.M)


def _get(url: str, attempts: int = 4) -> dict:
    """GET JSON with backoff. The datasets-server rate-limits and 5xxs under
    load; a half-fetched corpus would silently bias the selection, so failing
    loudly after retries is better than returning a partial page."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "archy-q1b"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if i < attempts - 1:
                time.sleep(2.0**i)
    raise RuntimeError(f"datasets-server failed after {attempts} attempts: {last}")


def fetch_rows(dataset: str, split: str = "test", *, refresh: bool = False) -> list[dict]:
    """All rows for a split, cached on disk.

    The cache is not an optimisation, it is politeness plus reproducibility.
    A full SWE-bench pull is 23 pages, and re-running the selector twice in
    quick succession earned an HTTP 429 that no backoff inside one invocation
    could clear. Caching also pins the corpus: a selection re-derived weeks
    later must not silently shift because the upstream dataset was revised.

    Lives under `bench/cache/`, which is gitignored.
    """
    cache_dir = REPO_ROOT / "bench" / "cache"
    cache = cache_dir / f"q1b_{dataset.replace('/', '__')}_{split}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))

    size = _get(f"{API}/size?dataset={dataset}")
    total = next(s["num_rows"] for s in size["size"]["splits"] if s["split"] == split)
    rows: list[dict] = []
    for offset in range(0, total, PAGE):
        page = _get(
            f"{API}/rows?dataset={dataset}&config=default&split={split}"
            f"&offset={offset}&length={PAGE}"
        )
        rows.extend(item["row"] for item in page["rows"])
        time.sleep(0.3)  # be a good citizen; 23 pages back-to-back trips the limiter
    if len(rows) != total:
        raise RuntimeError(f"fetched {len(rows)} of {total} rows; refusing a partial corpus")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def patch_files(patch: str) -> list[str]:
    return sorted({b for _, b in FILE_RE.findall(patch or "") if b.endswith(".py")})


def _dirs(files: list[str]) -> set[str]:
    """Directory is the proxy for module boundary. A change spanning two
    directories is the cheapest available signal that it crosses a boundary,
    which is the property Q1a found predicts cycle introduction."""
    return {"/".join(f.split("/")[:-1]) or "." for f in files}


def select(rows: list[dict], *, min_files: int, min_dirs: int) -> list[dict]:
    """The committed manifest is the *selection*, not the corpus.

    `problem_statement`, `FAIL_TO_PASS` and `PASS_TO_PASS` are deliberately not
    carried here: including them makes the manifest ~3.4MB of prose that is
    already reproducible from the cached corpus by `instance_id`. What has to be
    pinned for reproducibility is which tasks were chosen and why, which is the
    ids plus the counts the filter acted on.
    """
    out = []
    for r in rows:
        # The gold patch only. `test_patch` is excluded deliberately: test files
        # are not in the package under analysis and would inflate the counts
        # with changes archy never scores.
        files = patch_files(r.get("patch", ""))
        dirs = _dirs(files)
        if len(files) < min_files or len(dirs) < min_dirs:
            continue
        out.append(
            {
                "instance_id": r["instance_id"],
                "repo": r["repo"],
                "base_commit": r["base_commit"],
                "environment_setup_commit": r.get("environment_setup_commit"),
                "gold_py_files": files,
                "gold_py_file_count": len(files),
                "gold_dir_count": len(dirs),
            }
        )
    # Most structurally risky first, so a truncated pilot still runs the tasks
    # most likely to exercise the signal.
    out.sort(key=lambda t: (-t["gold_py_file_count"], -t["gold_dir_count"], t["instance_id"]))
    return out


def profile(rows: list[dict]) -> str:
    counts = [len(patch_files(r.get("patch", ""))) for r in rows]
    lines = [
        f"n={len(rows)}  median={statistics.median(counts)}  "
        f"mean={statistics.mean(counts):.2f}  max={max(counts)}",
        "",
        "gold .py files per patch:",
    ]
    for k, v in sorted(Counter(counts).items()):
        lines.append(f"  {k:>3} files: {v:>5}")
    lines.append("")
    lines.append("cuts:")
    for mf, md in ((2, 1), (2, 2), (3, 2), (4, 2), (5, 2), (7, 2)):
        n = len(select(rows, min_files=mf, min_dirs=md))
        lines.append(f"  >={mf} files & >={md} dirs: {n:>5} ({100 * n / len(rows):.1f}%)")
    lines.append("")
    lines.append("Q1a's structural-risk regime is a median of 7 .py files; note how few reach it.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--min-files", type=int, default=3)
    ap.add_argument("--min-dirs", type=int, default=2)
    ap.add_argument("--out", type=Path, help="write the selected task manifest here")
    ap.add_argument("--profile", action="store_true", help="print corpus stats and exit")
    ap.add_argument("--refresh", action="store_true", help="bypass the on-disk corpus cache")
    args = ap.parse_args()

    rows = fetch_rows(args.dataset, refresh=args.refresh)
    if args.profile:
        print(f"# {args.dataset}\n")
        print(profile(rows))
        return 0

    tasks = select(rows, min_files=args.min_files, min_dirs=args.min_dirs)
    by_repo = Counter(t["repo"] for t in tasks)
    print(f"# {args.dataset}: {len(tasks)} of {len(rows)} tasks clear the structural-risk filter")
    print(f"# filter: >={args.min_files} .py files across >={args.min_dirs} directories\n")
    for repo, n in by_repo.most_common():
        print(f"  {repo:34} {n:>4}")
    sizes = [t["gold_py_file_count"] for t in tasks]
    print(f"\n  selected file counts: median {statistics.median(sizes)}, max {max(sizes)}")
    print("  (Q1a's risky regime is median 7; see the module docstring caveat)")

    if args.out:
        payload = {
            "dataset": args.dataset,
            "filter": {"min_files": args.min_files, "min_dirs": args.min_dirs},
            "selected": len(tasks),
            "of": len(rows),
            "tasks": tasks,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
