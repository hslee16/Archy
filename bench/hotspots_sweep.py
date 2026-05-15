"""Sweep `archy hotspots` across the 27 bench projects at multiple
`--since` windows and report top-K overlap.

There's no labelled ground truth on the bench (nobody has tagged
"these files should be hotspots"), so this script measures two
window-choice proxies:

* **Window stability** -- Jaccard of top-K hotspot module sets between
  full history, 12 months, and 6 months. If the window doesn't move
  the top-K set, the default doesn't matter much.

* **Recency contamination** -- fraction of full-history top-K modules
  whose 12-month churn is zero. A high fraction means full history is
  biased toward complex-but-dead files; a low fraction means full
  history is already fine.

Output: a markdown report at `bench/hotspots_results.md` with a
per-project table and aggregate medians.

Usage:
    uv run --with pyyaml python bench/hotspots_sweep.py
    uv run --with pyyaml python bench/hotspots_sweep.py --stdout
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
RESULTS = REPO_ROOT / "bench" / "hotspots_results.md"
WORKDIR = Path("/tmp/archy_bench")

TOP_K = 20
WINDOWS: tuple[tuple[str, str | None], ...] = (
    ("full", None),
    ("12mo", "12.months"),
    ("6mo", "6.months"),
)


def load_manifest() -> list[dict]:
    return yaml.safe_load(MANIFEST.read_text())["projects"]


def clone_or_update(proj: dict) -> Path | None:
    """Mirrors bench/run.py's clone_or_update, but never raises -- on
    any failure we just skip the project so the sweep keeps going.
    The archy self-entry uses REPO_ROOT directly (its pin may name a
    tag that doesn't exist yet during a release PR)."""
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


def run_hotspots(root: Path, *, since: str | None) -> list[dict]:
    """Invoke `archy hotspots <root> --top TOP_K --format json` and
    return the parsed `hotspots` list. Empty on any failure."""
    cmd = ["uv", "run", "archy", "hotspots", str(root), "--top", str(TOP_K), "--format", "json"]
    if since:
        cmd += ["--since", since]
    res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        return []
    try:
        return json.loads(res.stdout).get("hotspots", [])
    except json.JSONDecodeError:
        return []


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard index, with the conventional `1.0` for two empty sets
    (no disagreement to measure) so a missing-window project doesn't
    poison the median."""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="write the markdown report to stdout instead of bench/hotspots_results.md",
    )
    args = parser.parse_args()

    manifest = load_manifest()
    print(
        f"# hotspots sweep over {len(manifest)} projects, windows: "
        f"{', '.join(name for name, _ in WINDOWS)}",
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
        try:
            results = {wname: run_hotspots(root, since=since) for wname, since in WINDOWS}
        except Exception as exc:
            print(f"  SKIP (sweep error: {exc})", file=sys.stderr)
            continue
        modules: dict[str, set[str]] = {
            wname: {h["module"] for h in results[wname]} for wname, _ in WINDOWS
        }
        full_mods = modules["full"]
        twelve = modules["12mo"]
        row = {
            "name": name,
            "sha": proj["sha"],
            "top_full": len(full_mods),
            "top_12mo": len(twelve),
            "top_6mo": len(modules["6mo"]),
            "j_full_12mo": jaccard(full_mods, twelve),
            "j_full_6mo": jaccard(full_mods, modules["6mo"]),
            "j_12mo_6mo": jaccard(twelve, modules["6mo"]),
            "stale_full_count": len(full_mods - twelve),
            "stale_full_frac": (
                len(full_mods - twelve) / len(full_mods) if full_mods else float("nan")
            ),
        }
        rows.append(row)
        print(
            f"  full={len(full_mods)} 12mo={len(twelve)} "
            f"j(full,12mo)={row['j_full_12mo']:.2f} "
            f"stale_frac={row['stale_full_frac']:.2f}",
            file=sys.stderr,
        )

    out_lines: list[str] = []

    def emit(line: str = "") -> None:
        out_lines.append(line)

    today = dt.date.today().isoformat()
    emit("# Hotspots window sweep")
    emit()
    emit(f"Output of `uv run --with pyyaml python bench/hotspots_sweep.py`. Captured {today}.")
    emit()
    emit(f"Top-K = {TOP_K}. Windows: full history, 12 months, 6 months. ")
    emit("Jaccard = |A intersect B| / |A union B| on the per-window top-K module sets. ")
    emit(
        "`stale_full_frac` = fraction of full-history top-K modules that "
        "are NOT in the 12-month top-K (the recency-contamination proxy: "
        "high means full-history is dominated by complex-but-dead files)."
    )
    emit()
    emit("## Per-project results")
    emit()
    cols = [
        "project",
        "sha",
        "|full|",
        "|12mo|",
        "|6mo|",
        "J(full,12mo)",
        "J(full,6mo)",
        "J(12mo,6mo)",
        "stale_full_frac",
    ]
    emit("| " + " | ".join(cols) + " |")
    emit("| " + " | ".join("---" if c in ("project", "sha") else "---:" for c in cols) + " |")
    for r in rows:
        emit(
            "| {name} | `{sha}` | {tf} | {tt} | {ts} | "
            "{jft:.2f} | {jfs:.2f} | {jts:.2f} | {sff} |".format(
                name=r["name"],
                sha=r["sha"],
                tf=r["top_full"],
                tt=r["top_12mo"],
                ts=r["top_6mo"],
                jft=r["j_full_12mo"],
                jfs=r["j_full_6mo"],
                jts=r["j_12mo_6mo"],
                sff=(
                    f"{r['stale_full_frac']:.2f}"
                    if r["stale_full_frac"] == r["stale_full_frac"]  # NaN check
                    else "n/a"
                ),
            )
        )

    emit()
    emit("## Aggregate (median across projects)")
    emit()

    def median(key: str) -> str:
        vals = [r[key] for r in rows if isinstance(r[key], float) and r[key] == r[key]]
        return f"{statistics.median(vals):.3f}" if vals else "n/a"

    emit("| metric | median |")
    emit("| --- | ---: |")
    emit(f"| J(full, 12mo) | {median('j_full_12mo')} |")
    emit(f"| J(full, 6mo)  | {median('j_full_6mo')} |")
    emit(f"| J(12mo, 6mo)  | {median('j_12mo_6mo')} |")
    emit(f"| stale_full_frac | {median('stale_full_frac')} |")

    report = "\n".join(out_lines) + "\n"
    if args.stdout:
        sys.stdout.write(report)
    else:
        RESULTS.write_text(report)
        print(f"# wrote {RESULTS.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
