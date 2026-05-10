"""Run the archy benchmark across the projects pinned in projects.yaml.

Outputs a markdown table of per-project scores plus pairwise Pearson
correlations of the four sub-metrics. Used to refresh SCORING.md after
formula changes; and `--vulture` to refresh RESEARCH_METRICS.md §12.

Usage:
    uv run --with networkx --with pyyaml python bench/run.py
    uv run --with networkx --with pyyaml python bench/run.py --vulture
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "bench" / "projects.yaml"
WORKDIR = Path("/tmp/archy_bench")


def load_manifest() -> list[dict]:
    return yaml.safe_load(MANIFEST.read_text())["projects"]


def clone_or_update(proj: dict) -> Path:
    name = proj["name"]
    sha = proj["sha"]
    if name == "archy":
        return REPO_ROOT
    target = WORKDIR / name
    if not target.exists():
        WORKDIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", f"https://github.com/{proj['repo']}.git", str(target)],
            check=True,
        )
    # Pin the SHA. Fetch first in case it's not in shallow clone.
    res = subprocess.run(
        ["git", "-C", str(target), "cat-file", "-e", sha],
        capture_output=True,
    )
    if res.returncode != 0:
        subprocess.run(
            ["git", "-C", str(target), "fetch", "--quiet", "origin", sha],
            check=False,
        )
    subprocess.run(
        ["git", "-C", str(target), "checkout", "--quiet", sha],
        check=True,
    )
    return target


def archy_score(src: Path) -> dict:
    out = subprocess.check_output(
        ["uv", "run", "archy", "score", "--format", "json", str(src)],
        cwd=REPO_ROOT,
    )
    return json.loads(out)


def vulture_count(src: Path, min_confidence: int = 60) -> int:
    if shutil.which("vulture") is None:
        return -1
    res = subprocess.run(
        ["vulture", "--min-confidence", str(min_confidence), str(src)],
        capture_output=True,
        text=True,
    )
    # vulture exit codes: 0=no findings, 1=findings, 2=usage error,
    # 3=syntax error in some target file. Code 3 still produces valid
    # output for the parseable files, so accept it.
    if res.returncode not in (0, 1, 3):
        return -1
    return len([line for line in res.stdout.splitlines() if line.strip()])


def loc(src: Path) -> int:
    if not src.exists():
        return 0
    total = 0
    for py in src.rglob("*.py"):
        # Skip vendored test directories that explode the count.
        parts = set(py.parts)
        if any(p in parts for p in ("tests", "test", "_test", ".venv")):
            continue
        try:
            total += sum(1 for _ in py.open(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return total


def pearson(xs: list[float], ys: list[float]) -> float:
    m = len(xs)
    if m < 2:
        return float("nan")
    mx = sum(xs) / m
    my = sum(ys) / m
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return float("nan")
    return num / (dx * dy)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vulture", action="store_true", help="also run vulture")
    args = parser.parse_args()

    manifest = load_manifest()
    rows: list[dict] = []
    print(f"# benchmark over {len(manifest)} projects", file=sys.stderr)
    for proj in manifest:
        print(f"# {proj['name']:13s} ", end="", file=sys.stderr, flush=True)
        try:
            root = clone_or_update(proj)
        except subprocess.CalledProcessError as exc:
            print(f"  CLONE/CHECKOUT FAILED: {exc}", file=sys.stderr)
            continue
        src = root / proj["src_dir"]
        if not src.exists():
            print(f"  SRC MISSING: {src}", file=sys.stderr)
            continue
        try:
            score = archy_score(src)
        except subprocess.CalledProcessError as exc:
            print(f"  ARCHY FAILED: {exc}", file=sys.stderr)
            continue
        comp = score["components"]
        inputs = score["inputs"]
        row = {
            "name": proj["name"],
            "sha": proj["sha"],
            "loc": loc(src),
            "modules": inputs["module_count"],
            "edges": inputs["edge_count"],
            "overall": score["overall"],
            "modularity": comp["modularity"],
            "acyclicity": comp["acyclicity"],
            "depth": comp["depth"],
            "equality": comp["equality"],
            "cycle_count": inputs["cycle_count"],
            "tangle_ratio": inputs.get("tangle_ratio", 0.0),
        }
        if args.vulture:
            row["vulture_60"] = vulture_count(src, 60)
            row["vulture_90"] = vulture_count(src, 90)
        rows.append(row)
        msg = f"  overall={score['overall']:.3f}  modules={inputs['module_count']}"
        print(msg, file=sys.stderr)

    rows.sort(key=lambda r: -r["overall"])

    print()
    print("## Score table\n")
    cols = [
        "name", "sha", "modules", "edges",
        "overall", "modularity", "acyclicity", "depth", "equality",
    ]
    print("| " + " | ".join(cols) + " |")
    print("| " + " | ".join("---:" if c not in {"name", "sha"} else "---" for c in cols) + " |")
    for r in rows:
        cells = [
            r["name"],
            f"`{r['sha']}`",
            str(r["modules"]),
            str(r["edges"]),
            f"{r['overall']:.3f}",
            f"{r['modularity']:.3f}",
            f"{r['acyclicity']:.3f}",
            f"{r['depth']:.3f}",
            f"{r['equality']:.3f}",
        ]
        print("| " + " | ".join(cells) + " |")

    print()
    print("## Pairwise Pearson correlations\n")
    axes = ["modularity", "acyclicity", "depth", "equality"]
    cols2 = {a: [r[a] for r in rows] for a in axes}
    print("| pair | r |")
    print("| --- | ---: |")
    for a, b in combinations(axes, 2):
        r = pearson(cols2[a], cols2[b])
        print(f"| {a} ↔ {b} | {r:+.3f} |")

    if args.vulture:
        print()
        print("## Vulture findings\n")
        print("| project | sha | LOC | vulture @60% | vulture @90% |")
        print("| --- | --- | ---: | ---: | ---: |")
        # Re-sort by LOC descending for the vulture table.
        for r in sorted(rows, key=lambda r: -r["loc"]):
            print(
                f"| {r['name']} | `{r['sha']}` | {r['loc']:,} | "
                f"{r.get('vulture_60', '?')} | {r.get('vulture_90', '?')} |"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
