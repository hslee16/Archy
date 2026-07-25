#!/usr/bin/env python
"""Validate the authored Q1b layer configs, at HEAD and at every task base commit.

    uv run python bench/q1b_layers_check.py                 # HEAD of each cached repo
    uv run python bench/q1b_layers_check.py --base-commits  # every top-25 task's base commit
    uv run python bench/q1b_layers_check.py --canary        # prove each rule set CAN fire

Three failure modes this exists to catch, all of which have already happened:

1. **A rule that fires on pristine code** makes the declared-layer signal
   constant for every run on that tree, so p_B looks like 100% for reasons that
   have nothing to do with the agent. `bench/q1b_layers/README.md` records the
   drops this caught.

2. **A rule that cannot fire at all.** The first three configs shipped with bare
   `modules: ["django.contrib"]` patterns, which archy matches as an EXACT
   dotted name, so each layer held one empty `__init__.py` instead of a whole
   package and every rule was dead. That failure is invisible from the outside:
   a config whose rules cannot fire looks exactly like a clean codebase.
   `--canary` appends one deliberately violating import and asserts the rule
   fires, then reverts.

3. **A rule that holds today but not at the task's base commit.** The base
   commits are years older than upstream HEAD. `--base-commits` caught
   `django.utils.log -> django.views.debug`, `sympy.external.importtools ->
   sympy.core.compatibility`, and `matplotlib.backend_bases -> pyplot`, each of
   which upstream removed later, and each of which was dropped so that every
   task in a repo runs the same ruleset.

Nothing here reads the task manifest beyond `repo` and `base_commit`; layer
authoring stays blind to `gold_py_files` and problem statements.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from archy.graph import DEFAULT_IGNORED_DIRS, build_graph  # noqa: E402
from archy.layers import _compile_pattern  # noqa: E402

CACHE = REPO_ROOT / "bench/repo_cache"
CONFIGS = REPO_ROOT / "bench/q1b_layers"
WORKTREES = REPO_ROOT / "bench/cache/q1b_worktrees"

# SWE-bench `repo` field -> the directory name under bench/repo_cache.
REPO_DIR = {
    "django/django": "django",
    "psf/requests": "requests",
    "scikit-learn/scikit-learn": "scikit-learn",
    "sympy/sympy": "sympy",
    "pydata/xarray": "xarray",
    "matplotlib/matplotlib": "matplotlib",
}


def load_config(name: str) -> dict:
    return yaml.safe_load((CONFIGS / f"{name}.yaml").read_text())


def graph_for(tree: Path, cfg: dict) -> nx.DiGraph:
    """The module graph a config sees: `exclude:` adds to archy's ignore set."""
    return build_graph(tree, ignored_dirs=DEFAULT_IGNORED_DIRS | set(cfg.get("exclude", [])))


def evaluate(
    tree: Path, cfg: dict, graph: nx.DiGraph | None = None
) -> tuple[dict[str, int], Counter, dict[str, str]]:
    """Layer sizes, firing rules with edge counts, and module -> layer index.

    `graph` is accepted so the canary can rebuild it once per edit instead of
    once per lookup: on sympy a build is several seconds.
    """
    if graph is None:
        graph = graph_for(tree, cfg)
    index: dict[str, str] = {}
    sizes: dict[str, int] = defaultdict(int)
    for layer, body in cfg["layers"].items():
        patterns = body["modules"]
        for qualname in graph.nodes:
            if any(_compile_pattern(p).fullmatch(qualname) for p in patterns):
                index[qualname] = layer
                sizes[layer] += 1
    rules = {(r["from"], r["to"]) for r in cfg["forbid"]}
    fires: Counter = Counter()
    for src, dst in graph.edges:
        pair = (index.get(src), index.get(dst))
        if pair in rules:
            fires[pair] += 1
    return dict(sizes), fires, index


def module_path(graph: nx.DiGraph, qualname: str) -> Path | None:
    raw = graph.nodes[qualname].get("path")
    return Path(raw) if raw else None


def report(label: str, sizes: dict[str, int], fires: Counter, cfg: dict) -> bool:
    empty = [layer for layer in cfg["layers"] if sizes.get(layer, 0) == 0]
    detail = ", ".join(f"{a}->{b}:{n}" for (a, b), n in fires.items())
    status = "FIRES" if fires else "ok   "
    note = f"  [empty layers: {','.join(empty)}]" if empty else ""
    print(f"  {status} {label:<44} {detail}{note}")
    return not fires


def check_head() -> bool:
    ok = True
    for name in sorted(REPO_DIR.values()):
        tree = CACHE / name
        if not tree.exists():
            print(f"  skip  {name:<44} not cloned under bench/repo_cache")
            continue
        cfg = load_config(name)
        sizes, fires, _ = evaluate(tree, cfg)
        head = subprocess.run(
            ["git", "-C", str(tree), "rev-parse", "--short=10", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        ok &= report(f"{name} @ {head} ({len(cfg['forbid'])} rules)", sizes, fires, cfg)
    return ok


def worktree_at(name: str, sha: str) -> Path:
    """A detached worktree of the cached clone, checked out at `sha`."""
    WORKTREES.mkdir(parents=True, exist_ok=True)
    tree = WORKTREES / name
    if not tree.exists():
        subprocess.run(
            ["git", "-C", str(CACHE / name), "worktree", "add", "--detach", str(tree), sha],
            check=True,
            capture_output=True,
        )
    else:
        subprocess.run(
            ["git", "-C", str(tree), "checkout", "--quiet", "--detach", sha],
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "-C", str(tree), "clean", "-qfdx"], check=True)
    return tree


def check_base_commits(limit: int) -> bool:
    tasks = json.loads((REPO_ROOT / "bench/q1b_tasks.json").read_text())["tasks"][:limit]
    ok = True
    for task in tasks:
        name = REPO_DIR[task["repo"]]
        cfg = load_config(name)
        tree = worktree_at(name, task["base_commit"])
        sizes, fires, _ = evaluate(tree, cfg)
        ok &= report(f"{task['instance_id']} @ {task['base_commit'][:10]}", sizes, fires, cfg)
    return ok


def check_canary() -> bool:
    """Append one violating import per repo and assert the ruleset notices.

    A config whose patterns are scoped wrongly passes every other check in this
    file while being incapable of reporting anything, so this is the only check
    that distinguishes "clean" from "dead".
    """
    ok = True
    for name in sorted(REPO_DIR.values()):
        tree = CACHE / name
        if not tree.exists():
            continue
        cfg = load_config(name)
        graph = graph_for(tree, cfg)
        _, _, index = evaluate(tree, cfg, graph)
        members: dict[str, list[str]] = defaultdict(list)
        for qualname, layer in index.items():
            members[layer].append(qualname)
        for rule in cfg["forbid"]:
            sources = sorted(members.get(rule["from"], []))
            targets = sorted(members.get(rule["to"], []))
            if not sources or not targets:
                continue
            victim, imported = sources[0], targets[0]
            path = module_path(graph, victim)
            if path is None:
                continue
            original = path.read_text()
            # try/finally, because a crash between the two writes would leave a
            # cached clone dirty and silently poison every later check.
            try:
                path.write_text(f"{original}\nimport {imported}  # q1b canary\n")
                _, fires, _ = evaluate(tree, cfg)
            finally:
                path.write_text(original)
            fired = fires.get((rule["from"], rule["to"]), 0)
            status = "ok   " if fired else "DEAD "
            print(f"  {status} {name:<20} {victim} -> {imported}: {fired} violation(s)")
            ok &= bool(fired)
            break
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--base-commits",
        action="store_true",
        help="validate at each selected task's base commit, not just HEAD",
    )
    ap.add_argument(
        "--canary",
        action="store_true",
        help="append one violating import per repo and assert it is reported",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=25,
        help="how many tasks from the manifest to check (default: the top 25)",
    )
    args = ap.parse_args()

    ok = True
    if args.base_commits:
        print("# base commits")
        ok &= check_base_commits(args.limit)
    elif args.canary:
        print("# canary (a config whose rules cannot fire looks exactly like a clean repo)")
        ok &= check_canary()
    else:
        print("# pristine HEAD")
        ok &= check_head()
    if not ok:
        print("\nFAILED: see bench/q1b_layers/README.md for what to do about a firing rule.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
