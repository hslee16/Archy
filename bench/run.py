"""Run the archy benchmark across the projects pinned in projects.yaml.

Writes a markdown table of per-project scores plus pairwise Pearson
correlations of the four sub-metrics to bench/results.md. Used to
refresh SCORING.md after formula changes; and `--vulture` to refresh
RESEARCH_METRICS.md §12.

Usage:
    uv run --with networkx --with pyyaml python bench/run.py
    uv run --with networkx --with pyyaml python bench/run.py --vulture
    uv run --with networkx --with pyyaml python bench/run.py --stdout

archy:owns        archy_score, clone_or_update, fetch_head_sha, load_manifest, loc,
                  main, pearson, update_shas, vulture_count
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import shutil
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "bench" / "projects.yaml"
RESULTS = REPO_ROOT / "bench" / "results.md"
# Clones are cached in-repo (gitignored) and reused across runs: a healthy
# checkout is just fetched + re-pinned, not re-cloned. Override with
# ARCHY_BENCH_CACHE for a throwaway location.
WORKDIR = Path(os.environ.get("ARCHY_BENCH_CACHE", REPO_ROOT / "bench" / "repo_cache"))


def load_manifest() -> list[dict]:
    return yaml.safe_load(MANIFEST.read_text())["projects"]


def _is_git_repo(path: Path) -> bool:
    """True only for a usable clone. An interrupted clone leaves a directory
    that exists but has no valid .git, which would fail every checkout; treat
    that as a cache miss so it gets wiped and re-cloned."""
    if not path.exists():
        return False
    return (
        subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-dir"],
            capture_output=True,
        ).returncode
        == 0
    )


def _clone(repo: str, target: Path) -> None:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--quiet", f"https://github.com/{repo}.git", str(target)],
        check=True,
    )


def _checkout_clean(target: Path, sha: str) -> None:
    # Ensure the pinned commit is present; GitHub rejects fetches of short
    # SHAs as a refspec, so fetch all refs and let checkout resolve it.
    present = subprocess.run(
        ["git", "-C", str(target), "cat-file", "-e", sha],
        capture_output=True,
    )
    if present.returncode != 0:
        subprocess.run(["git", "-C", str(target), "fetch", "--quiet", "origin"], check=False)
    subprocess.run(["git", "-C", str(target), "checkout", "--quiet", sha], check=True)
    # Force a clean tree at the pinned SHA. Untracked .py files left over
    # from a previous checkout at a different ref would otherwise be parsed
    # as phantom modules, inflating module and edge counts.
    subprocess.run(["git", "-C", str(target), "reset", "--hard", "--quiet", sha], check=True)
    subprocess.run(["git", "-C", str(target), "clean", "-fdx", "--quiet"], check=True)


def clone_or_update(proj: dict) -> Path:
    name = proj["name"]
    sha = proj["sha"]
    if name == "archy" and sha == "HEAD":
        # Dev convenience: when the manifest deliberately tracks HEAD, use
        # the working tree so iterating on archy itself doesn't require
        # repeated clones. Anything else (a tag or commit SHA) goes through
        # the normal clone+checkout path so the result is reproducible.
        return REPO_ROOT
    target = WORKDIR / name
    # Drop a corrupt/partial cache entry so it gets re-cloned instead of
    # failing checkout on every future run (the bug that motivated the move
    # off /tmp: an interrupted run left non-git dirs that poisoned the cache).
    if target.exists() and not _is_git_repo(target):
        shutil.rmtree(target, ignore_errors=True)
    if not target.exists():
        _clone(proj["repo"], target)
    try:
        _checkout_clean(target, sha)
    except subprocess.CalledProcessError:
        # Cached repo too damaged to check out (e.g. truncated packfile):
        # wipe and re-clone once, then retry.
        shutil.rmtree(target, ignore_errors=True)
        _clone(proj["repo"], target)
        _checkout_clean(target, sha)
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


def fetch_head_sha(repo: str) -> str | None:
    """Return the short SHA of the remote HEAD, or None on failure.

    Uses git ls-remote so no clone is required.
    """
    res = subprocess.run(
        ["git", "ls-remote", f"https://github.com/{repo}", "HEAD"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return None
    line = res.stdout.strip().split("\n", 1)[0]
    if not line:
        return None
    return line.split()[0][:7]


def update_shas() -> int:
    """Refresh the `sha:` field of every non-tag-pinned project in the manifest.

    Tag-pinned entries (sha starts with "v") are deliberate and skipped so a
    refresh can't silently turn archy's self-pin into a moving HEAD again.
    """
    manifest = load_manifest()
    text = MANIFEST.read_text()
    updated = 0
    for proj in manifest:
        old_sha = proj["sha"]
        if old_sha.startswith("v"):
            print(f"# {proj['name']:13s}  skipped (tag-pinned: {old_sha})", file=sys.stderr)
            continue
        new_sha = fetch_head_sha(proj["repo"])
        if new_sha is None:
            print(f"# {proj['name']:13s}  FETCH FAILED", file=sys.stderr)
            continue
        if new_sha == old_sha:
            print(f"# {proj['name']:13s}  unchanged ({old_sha})", file=sys.stderr)
            continue
        text = text.replace(f'sha: "{old_sha}"', f'sha: "{new_sha}"', 1)
        updated += 1
        print(f"# {proj['name']:13s}  {old_sha} -> {new_sha}", file=sys.stderr)
    MANIFEST.write_text(text)
    print(f"# updated {updated} SHA(s)", file=sys.stderr)
    return 0


def pearson(xs: list[float], ys: list[float]) -> float:
    m = len(xs)
    if m < 2:
        return float("nan")
    mx = sum(xs) / m
    my = sum(ys) / m
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return float("nan")
    return num / (dx * dy)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vulture", action="store_true", help="also run vulture")
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="write the markdown report to stdout instead of bench/results.md",
    )
    parser.add_argument(
        "--update-shas",
        action="store_true",
        help="refresh non-tag-pinned project SHAs to remote HEAD and exit",
    )
    args = parser.parse_args()

    if args.update_shas:
        return update_shas()

    out_lines: list[str] = []

    def emit(line: str = "") -> None:
        out_lines.append(line)

    manifest = load_manifest()

    # archy's OWN archy.yaml (repo root) excludes `repo_cache` (#213), so that a
    # self-score does not descend into the vendored clones. But `archy score`
    # discovers config by walking UP from the target, and the clones carry no
    # archy.yaml of their own, so scoring `repo_cache/<proj>/src/...` would climb
    # into archy's repo root, inherit that exclude, and score every corpus project
    # as 0 modules. Drop a neutral (empty-exclude) archy.yaml at the cache root so
    # discovery stops there. Skipped when the cache lives outside the repo
    # (ARCHY_BENCH_CACHE), where archy's config is never on the path anyway.
    if WORKDIR == REPO_ROOT / "bench" / "repo_cache":
        WORKDIR.mkdir(parents=True, exist_ok=True)
        (WORKDIR / "archy.yaml").write_text("exclude: []\n")

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
            # complexity was promoted from diagnostic to score axis in v0.20.
            # Default to 1.0 (the vacuous case the axis uses) for older
            # archy that doesn't surface it; the bench should always run
            # against current archy so this is just defensive.
            "complexity": comp.get("complexity", 1.0),
            "cycle_count": inputs["cycle_count"],
            "tangle_ratio": inputs.get("tangle_ratio", 0.0),
            "propagation_cost": inputs.get("propagation_cost", 0.0),
            "call_edge_count": inputs.get("call_edge_count", 0),
            "total_calls": inputs.get("total_calls", 0),
            "calls_per_edge": inputs.get("calls_per_edge", 0.0),
            "function_count": inputs.get("function_count", 0),
            "cc_total": inputs.get("cc_total", 0),
            "cc_max": inputs.get("cc_max", 0),
            "cc_mean": inputs.get("cc_mean", 0.0),
            # Call-weighted Newman Q diagnostic shipped in v0.21. The gap
            # between unweighted and weighted raw Q is the load-bearing
            # signal; see docs/research/CALL_WEIGHTED_Q_EMPIRICS.md.
            "raw_modularity": inputs.get("raw_modularity", 0.0),
            "raw_modularity_weighted": inputs.get("raw_modularity_weighted", 0.0),
        }
        if args.vulture:
            row["vulture_60"] = vulture_count(src, 60)
            row["vulture_90"] = vulture_count(src, 90)
        rows.append(row)
        msg = f"  overall={score['overall']:.3f}  modules={inputs['module_count']}"
        print(msg, file=sys.stderr)

    rows.sort(key=lambda r: -r["overall"])

    today = dt.date.today().isoformat()
    cmd = "uv run --with networkx --with pyyaml python bench/run.py"
    if args.vulture:
        cmd += " --vulture"
    emit("# Benchmark results")
    emit()
    emit(f"Output of `{cmd}`.")
    emit(f"SHAs pinned in `bench/projects.yaml`. Captured {today}.")
    emit()
    emit("## Score table")
    emit()
    cols = [
        "name",
        "sha",
        "modules",
        "edges",
        "overall",
        "modularity",
        "acyclicity",
        "depth",
        "equality",
        "complexity",
    ]
    emit("| " + " | ".join(cols) + " |")
    emit("| " + " | ".join("---:" if c not in {"name", "sha"} else "---" for c in cols) + " |")
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
            f"{r['complexity']:.3f}",
        ]
        emit("| " + " | ".join(cells) + " |")

    emit()
    emit("## Pairwise Pearson correlations")
    emit()
    axes = ["modularity", "acyclicity", "depth", "equality", "complexity"]
    cols2 = {a: [r[a] for r in rows] for a in axes}
    emit("| pair | r |")
    emit("| --- | ---: |")
    axis_corrs: list[tuple[str, float]] = []
    for a, b in combinations(axes, 2):
        r = pearson(cols2[a], cols2[b])
        axis_corrs.append((f"{a} ↔ {b}", r))
        emit(f"| {a} ↔ {b} | {r:+.3f} |")

    emit()
    emit("## Call-graph diagnostics")
    emit()
    emit(
        "`coverage` = call_edges / import_edges: the fraction of import edges that "
        "carry at least one resolved call. Static call resolution is partial (dynamic "
        "dispatch, decorators, and re-exports are not followed), so the call-graph "
        "diagnostics below are computed on this fraction, not the whole import graph."
    )
    emit()
    emit("| project | sha | modules | edges | call_edges | coverage | total_calls | calls/edge |")
    emit("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in rows:
        coverage = r["call_edge_count"] / r["edges"] if r["edges"] else 0.0
        emit(
            f"| {r['name']} | `{r['sha']}` | {r['modules']} | {r['edges']} | "
            f"{r['call_edge_count']} | {coverage:.1%} | {r['total_calls']} | "
            f"{r['calls_per_edge']:.2f} |"
        )

    emit()
    emit("## Call-density orthogonality to existing axes")
    emit()
    emit("Pearson correlation of `calls_per_edge` against each axis + propagation cost.")
    emit("Values below `|r| = 0.7` are below the OECD redundancy threshold.")
    emit()
    cpe = [r["calls_per_edge"] for r in rows]
    pc = [r["propagation_cost"] for r in rows]
    emit("| signal | r vs calls/edge |")
    emit("| --- | ---: |")
    for a in axes:
        emit(f"| {a} | {pearson(cols2[a], cpe):+.3f} |")
    emit(f"| propagation_cost | {pearson(pc, cpe):+.3f} |")

    emit()
    emit("## Cyclomatic complexity diagnostics")
    emit()
    emit("| project | sha | functions | cc_mean | cc_max |")
    emit("| --- | --- | ---: | ---: | ---: |")
    for r in rows:
        emit(
            f"| {r['name']} | `{r['sha']}` | {r['function_count']:,} | "
            f"{r['cc_mean']:.2f} | {r['cc_max']} |"
        )

    emit()
    emit("## CC orthogonality to existing axes")
    emit()
    emit("Pearson correlation of `cc_mean` against each axis + the two prior diagnostics.")
    emit("Values below `|r| = 0.7` are below the OECD redundancy threshold.")
    emit()
    cm = [r["cc_mean"] for r in rows]
    emit("| signal | r vs cc_mean |")
    emit("| --- | ---: |")
    for a in axes:
        if a == "complexity":
            # cc_mean is what the complexity axis is computed from, so the
            # correlation is mechanical (cc_mean=1 -> complexity=1, monotone
            # decreasing). Skip to avoid the misleading near-1.0 row.
            continue
        emit(f"| {a} | {pearson(cols2[a], cm):+.3f} |")
    emit(f"| propagation_cost | {pearson(pc, cm):+.3f} |")
    emit(f"| calls_per_edge | {pearson(cpe, cm):+.3f} |")

    emit()
    emit("## Call-weighted modularity diagnostic (v0.21)")
    emit()
    emit(
        "Per-project unweighted vs call-weighted raw Newman Q. The gap "
        "(weighted - unweighted) is the load-bearing signal; see "
        "`docs/research/CALL_WEIGHTED_Q_EMPIRICS.md`."
    )
    emit()
    emit("| project | sha | unweighted Q | weighted Q | gap |")
    emit("| --- | --- | ---: | ---: | ---: |")
    for r in rows:
        gap = r["raw_modularity_weighted"] - r["raw_modularity"]
        emit(
            f"| {r['name']} | `{r['sha']}` | "
            f"{r['raw_modularity']:+.3f} | "
            f"{r['raw_modularity_weighted']:+.3f} | "
            f"{gap:+.3f} |"
        )

    emit()
    emit("Pearson correlation of normalized weighted Q against the existing axes.")
    emit("Lower absolute values indicate stronger orthogonality.")
    emit()
    # Normalize raw Q the same way the unweighted axis does, so cross-axis
    # comparison is on the same [0, 1] scale.
    qw_norm = [max(0.0, min(1.0, (r["raw_modularity_weighted"] + 0.5) / 1.5)) for r in rows]
    emit("| signal | r vs weighted Q (normalized) |")
    emit("| --- | ---: |")
    for a in axes:
        emit(f"| {a} | {pearson(cols2[a], qw_norm):+.3f} |")

    if args.vulture:
        emit()
        emit("## Vulture findings")
        emit()
        emit("| project | sha | LOC | vulture @60% | vulture @90% |")
        emit("| --- | --- | ---: | ---: | ---: |")
        # Vulture findings scale with project size, so LOC ordering is more
        # informative for this table than the score-derived sort above.
        for r in sorted(rows, key=lambda r: -r["loc"]):
            emit(
                f"| {r['name']} | `{r['sha']}` | {r['loc']:,} | "
                f"{r.get('vulture_60', '?')} | {r.get('vulture_90', '?')} |"
            )

    # Falsification gate (issue #177). The bench's load-bearing claim is that the
    # five axes are non-redundant: every inter-axis |r| stays below the OECD
    # redundancy threshold (0.7). Make that claim FAIL loudly instead of silently
    # passing if a future formula change pushes a pair into redundancy, and WARN on
    # the moderate band so the two known depth pairs stay visible rather than buried.
    REDUNDANT = 0.7
    MODERATE = 0.5
    redundant = [(p, r) for p, r in axis_corrs if abs(r) > REDUNDANT]
    moderate = [(p, r) for p, r in axis_corrs if MODERATE <= abs(r) <= REDUNDANT]
    emit()
    emit("## Axis-independence gate")
    emit()
    if redundant:
        emit(
            f"**FAIL**: {len(redundant)} axis pair(s) exceed the OECD redundancy "
            f"threshold `|r| > 0.7`:"
        )
        for p, r in redundant:
            emit(f"- `{p}`: `{r:+.3f}`")
    else:
        emit(
            f"**PASS**: all {len(axis_corrs)} axis pairs are below the OECD "
            f"redundancy threshold `|r| = 0.7`."
        )
    if moderate:
        emit("")
        emit("Moderate coupling (`0.5 <= |r| <= 0.7`), acceptable but watched:")
        for p, r in moderate:
            emit(f"- `{p}`: `{r:+.3f}`")
    emit()

    report = "\n".join(out_lines) + "\n"
    if args.stdout:
        sys.stdout.write(report)
    else:
        RESULTS.write_text(report)
        print(f"# wrote {RESULTS.relative_to(REPO_ROOT)}", file=sys.stderr)

    if redundant:
        print(
            f"# AXIS-INDEPENDENCE GATE FAILED: {len(redundant)} pair(s) over |r|=0.7: "
            + ", ".join(f"{p} ({r:+.3f})" for p, r in redundant),
            file=sys.stderr,
        )
        return 1
    if moderate:
        print(
            f"# axis-independence gate passed; {len(moderate)} pair(s) in the moderate band "
            + ", ".join(f"{p} ({r:+.3f})" for p, r in moderate),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
