#!/usr/bin/env python
"""Prevalence study: how often does a single real commit introduce a structural
regression that archy is built to catch?

This answers Q1a (the prevalence / headroom precondition) of the open question
recorded in ``docs/research/AGENT_CAUSAL_REASONING_SYNTHESIS.md`` sec 10:

    "Does archy-in-the-loop measurably reduce structurally-bad edits?"

The full causal claim (Q1b) needs an agent A/B and is out of scope here. What is
computable now, with no usage signal, is the *base rate*: replay real merged
history one commit at a time, and for each commit measure whether it introduced
a new import cycle or dropped the composite score relative to its parent. If the
base rate is ~0, an architectural feedback loop has little to catch; if it is
meaningful, there is headroom for archy to help.

For each sampled commit C (single-parent, touches .py files under the package):
  - check out C^,  build the graph over the package dir, record metrics
  - check out C,   build the graph over the package dir, record metrics
  - a "cycle regression" = cycle_count rose AND new modules became cyclic
  - a "score regression" = overall score fell vs the parent

Package dir per repo matches bench/projects.yaml src_dir so numbers are
apples-to-apples with archy's published benchmarks (test/doc noise excluded).

Usage:
    uv run python bench/inloop_prevalence.py \
        --per-repo 40 --out bench/inloop_prevalence_results.json

archy:owns        changed_py, checkout, clone, cyclic_nodes, default_ref, git, main,
                  metrics, run_repo, sample_commits, summarize
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from archy.graph import build_graph
from archy.score import compute_score

# (repo, package subdir): small-to-medium pure-Python projects with years of
# history and many contributors, spanning CLI / HTTP / web / terminal domains.
REPOS: list[tuple[str, str]] = [
    ("pallets/click", "src/click"),
    ("psf/requests", "src/requests"),
    ("pallets/flask", "src/flask"),
    ("encode/httpx", "httpx"),
    ("encode/starlette", "starlette"),
    ("Textualize/rich", "rich"),
    ("pydantic/pydantic", "pydantic"),
    ("fastapi/fastapi", "fastapi"),
    ("scrapy/scrapy", "scrapy"),
    ("mkdocs/mkdocs", "mkdocs"),
    ("simonw/datasette", "datasette"),
]

CACHE = Path(__file__).resolve().parent / "replay_cache"


def clone(repo: str) -> Path:
    CACHE.mkdir(exist_ok=True)
    dest = CACHE / repo.split("/")[-1]
    if not (dest / ".git").exists():
        subprocess.run(
            ["git", "clone", "--quiet", f"https://github.com/{repo}.git", str(dest)],
            check=True,
        )
    return dest


def git(dest: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(dest), *args], text=True)


def default_ref(dest: Path) -> str:
    """The repo's default-branch tip (e.g. origin/main), independent of whatever
    detached commit a prior replay left HEAD on."""
    try:
        return git(dest, "rev-parse", "--abbrev-ref", "origin/HEAD").strip()
    except subprocess.CalledProcessError:
        for cand in ("origin/main", "origin/master"):
            try:
                git(dest, "rev-parse", "--verify", cand)
                return cand
            except subprocess.CalledProcessError:
                continue
    return "HEAD"


def sample_commits(dest: Path, ref: str, pkg: str, n: int, pool: int = 2000) -> list[str]:
    """n single-parent commits touching .py files under pkg, sampled evenly
    across up to `pool` most-recent such commits reachable from `ref`, so the
    base rate spans years of history rather than only the last few weeks (which
    bias toward small release-time fixes). Reading from an explicit ref makes
    sampling independent of the current (possibly detached) HEAD."""
    out = git(
        dest,
        "log",
        ref,
        "--no-merges",
        "--format=%H",
        "-n",
        str(pool),
        "--",
        f"{pkg}/**/*.py",
        f"{pkg}/*.py",
    ).split()
    if len(out) <= n:
        return out
    step = len(out) / n
    return [out[int(i * step)] for i in range(n)]


def cyclic_nodes(g: nx.DiGraph) -> set[str]:
    out: set[str] = set()
    for scc in nx.strongly_connected_components(g):
        if len(scc) > 1:
            out |= scc
    for node in g.nodes:
        if g.has_edge(node, node):
            out.add(node)
    return out


def metrics(pkg_path: Path) -> dict | None:
    try:
        g = build_graph(pkg_path)
    except Exception:
        return None
    if g.number_of_nodes() == 0:
        return None
    s = compute_score(g)
    cyc = cyclic_nodes(g)
    # node -> size of the SCC it belongs to (only non-trivial SCCs), so callers
    # can size the *specific* cycle a node sits in rather than the global max.
    node_scc_size: dict[str, int] = {}
    for scc in nx.strongly_connected_components(g):
        if len(scc) > 1:
            for node in scc:
                node_scc_size[node] = len(scc)
    return {
        "overall": s.overall,
        "acyclicity": s.acyclicity,
        "cycle_count": s.inputs.cycle_count,
        "tangle": s.inputs.tangle_ratio,
        "modules": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "cyclic": cyc,
        "node_scc_size": node_scc_size,
    }


def checkout(dest: Path, ref: str) -> bool:
    try:
        subprocess.run(
            ["git", "-C", str(dest), "checkout", "-q", "--force", ref],
            check=True,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def changed_py(dest: Path, parent: str, commit: str, pkg: str) -> int:
    out = git(dest, "diff", "--name-only", parent, commit, "--", f"{pkg}/**/*.py", f"{pkg}/*.py")
    return len([line for line in out.splitlines() if line.strip()])


def run_repo(repo: str, pkg: str, n: int) -> list[dict]:
    dest = clone(repo)
    rows: list[dict] = []
    ref = default_ref(dest)
    checkout(dest, ref)  # anchor to default-branch tip before sampling
    commits = sample_commits(dest, ref, pkg, n)
    print(f"[{repo}] {len(commits)} commits sampled from {ref}", file=sys.stderr)
    for i, c in enumerate(commits):
        parent = f"{c}^"
        if not checkout(dest, c):
            continue
        after = metrics(dest / pkg)
        if not checkout(dest, parent):
            continue
        before = metrics(dest / pkg)
        if after is None or before is None:
            continue
        new_cyclic = sorted(after["cyclic"] - before["cyclic"])
        cycle_reg = after["cycle_count"] > before["cycle_count"] and len(new_cyclic) > 0
        # size of the *newly-formed* tangle: the largest SCC in the after-graph
        # that actually contains a module which was acyclic at the parent.
        new_scc_size = max((after["node_scc_size"].get(m, 0) for m in new_cyclic), default=0)
        score_reg = after["overall"] < before["overall"] - 1e-9
        try:
            size = changed_py(dest, parent, c, pkg)
        except subprocess.CalledProcessError:
            size = -1
        rows.append(
            {
                "repo": repo,
                "commit": c[:10],
                "files_changed": size,
                "modules_before": before["modules"],
                "cycle_count_before": before["cycle_count"],
                "cycle_count_after": after["cycle_count"],
                "new_cyclic_modules": len(new_cyclic),
                "max_new_scc": new_scc_size if cycle_reg else 0,
                "overall_before": round(before["overall"], 5),
                "overall_after": round(after["overall"], 5),
                "overall_delta": round(after["overall"] - before["overall"], 5),
                "acyclicity_delta": round(after["acyclicity"] - before["acyclicity"], 5),
                "cycle_regression": cycle_reg,
                "score_regression": score_reg,
            }
        )
        if (i + 1) % 10 == 0:
            print(f"[{repo}] {i + 1}/{len(commits)} done", file=sys.stderr)
    checkout(dest, ref)  # restore default-branch tip for reuse
    return rows


def summarize(rows: list[dict]) -> None:
    if not rows:
        print("no rows")
        return
    by_repo: dict[str, list[dict]] = {}
    for r in rows:
        by_repo.setdefault(r["repo"], []).append(r)

    def pct(num: int, den: int) -> str:
        return f"{100 * num / den:.1f}%" if den else "n/a"

    print("\n=== Per-repo prevalence ===")
    print(f"{'repo':22} {'N':>4} {'cycle_reg':>10} {'score_reg':>10} {'either':>8}")
    tot_n = tot_cyc = tot_score = tot_either = 0
    for repo, rs in by_repo.items():
        n = len(rs)
        cyc = sum(r["cycle_regression"] for r in rs)
        sco = sum(r["score_regression"] for r in rs)
        either = sum(r["cycle_regression"] or r["score_regression"] for r in rs)
        tot_n += n
        tot_cyc += cyc
        tot_score += sco
        tot_either += either
        print(f"{repo:22} {n:>4} {pct(cyc, n):>10} {pct(sco, n):>10} {pct(either, n):>8}")
    print(
        f"{'TOTAL':22} {tot_n:>4} {pct(tot_cyc, tot_n):>10} "
        f"{pct(tot_score, tot_n):>10} {pct(tot_either, tot_n):>8}"
    )

    print("\n=== Cycle-regression characterization ===")
    cyc_rows = [r for r in rows if r["cycle_regression"]]
    print(f"commits introducing a new cycle: {len(cyc_rows)} / {tot_n} ({pct(tot_cyc, tot_n)})")
    if cyc_rows:
        sccs = [r["max_new_scc"] for r in cyc_rows]
        twos = sum(1 for x in sccs if x == 2)
        print(f"  new-SCC size: min={min(sccs)} median={statistics.median(sccs)} max={max(sccs)}")
        print(
            f"  2-module cycles (easiest to flag/fix): "
            f"{twos}/{len(cyc_rows)} ({pct(twos, len(cyc_rows))})"
        )

    print("\n=== Commit-size relationship (cycle regressions) ===")
    small = [r for r in rows if 0 <= r["files_changed"] <= 3]
    large = [r for r in rows if r["files_changed"] >= 10]
    small_reg = pct(sum(r["cycle_regression"] for r in small), len(small))
    large_reg = pct(sum(r["cycle_regression"] for r in large), len(large))
    print(f"  small commits (<=3 .py files): {small_reg} cycle-reg over N={len(small)}")
    print(f"  large commits (>=10 .py files): {large_reg} cycle-reg over N={len(large)}")

    print("\n=== Score-delta distribution ===")
    deltas = [r["overall_delta"] for r in rows]
    print(
        f"  overall delta: min={min(deltas)} median={statistics.median(deltas)} max={max(deltas)}"
    )
    print(f"  commits with any score drop: {pct(tot_score, tot_n)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-repo", type=int, default=40)
    ap.add_argument("--out", type=Path, default=Path("bench/inloop_prevalence_results.json"))
    args = ap.parse_args()

    all_rows: list[dict] = []
    for repo, pkg in REPOS:
        try:
            all_rows.extend(run_repo(repo, pkg, args.per_repo))
        except Exception as exc:
            print(f"[{repo}] FAILED: {exc}", file=sys.stderr)
    args.out.write_text(json.dumps(all_rows, indent=2))
    summarize(all_rows)
    print(f"\nwrote {len(all_rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
