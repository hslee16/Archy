#!/usr/bin/env python
"""Does a declared architecture decay, and can anything see it? (#364)

    uv run python bench/contract_decay.py --measure --repos 14
    uv run python bench/contract_decay.py --report

Validation study for the pivot that #359 pointed at. Same corpus, same clones,
**no agent time**.

## Why this exists

#359 measured that violations are rare (1 of 151 commits, 0.66%) but found two
things it was not looking for: **zero commits fixed a standing violation**, and
2 of 14 repositories sat on contracts that were broken and stayed broken. That
suggests the interesting failure is not the moment of violation, which CI
already shows you, but the rule quietly ceasing to mean anything.

Building a product on two observations from 14 repositories would be
irresponsible, so this measures them.

## Four signals, and only three are interesting

| signal | definition | already visible? |
| --- | --- | --- |
| standing violation | broken at commit N, still broken at N+k | YES, CI shows it |
| rule relaxation | the violation resolves because the RULE changed, not the code | no |
| dead rule | names modules that no longer exist in the tree | no |
| coverage erosion | new modules land outside every contract as the repo grows | no |

**A standing-violation finding alone does not justify the pivot**: it is already
in CI, and the repos observed with one evidently knew and did not care. The
distinctive claim has to rest on relaxation, dead rules, or erosion.

## Pre-registered nulls (#364, written before running)

- standing violations die if the median clears in <= 2 commits
- relaxation dies if under 5% of resolutions come with a rule change
- dead rules die if over 80% of rules govern modules that still exist
- erosion dies if the coverage slope over time is >= 0

**If all four die, the pivot is dead too** and that belongs in
`docs/WHAT_DIDNT_WORK.md`, exactly as #360 commits the agent line to doing. One
validation, not a search for whichever signal survives.

## Method

A chronological series per repo rather than #359's random pairs: decay is a
question about *time*, and independent pairs cannot show a trend. Samples are
evenly spaced over the commits since the project declared an architecture, and
at each one this records the config's own contract set, which modules it names,
how much of the tree those names reach, and which contracts are broken.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import itertools
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.append(str(REPO_ROOT / "bench"))
from _supervise import Ledger  # noqa: E402
from contract_prevalence import (  # noqa: E402
    CORPUS,
    adoption_commit,
    checkout,
    clone,
    evaluate,
    git,
)

from archy.graph import DEFAULT_IGNORED_DIRS, build_graph  # noqa: E402

LEDGER_PATH = REPO_ROOT / "bench/contract_decay.jsonl"

# Keys whose values name modules inside an import-linter contract. Collected
# across contract types (Forbidden, Layers, Independence) because the question
# is only "which modules does this config claim authority over".
MODULE_KEYS = (
    "source_modules",
    "forbidden_modules",
    "modules",
    "layers",
    "containers",
)


def parse_contracts(tree: Path) -> tuple[str, tuple[str, ...], int] | None:
    """(config hash, module names the CONTRACTS mention, contract count).

    `root_packages` is deliberately NOT included. It declares the scan scope,
    not what the rules govern, and folding it in made coverage trivially 100% in
    the first run because every module matches its own root package. Exactly the
    distinction `archy check` draws between `roots:` and governed roots (#362).

    The hash is what makes rule relaxation detectable: if a violation vanishes
    and the hash moved, the rule may have been legislated away rather than
    obeyed. None when no config is present.
    """
    for name in (".importlinter", "setup.cfg", "pyproject.toml"):
        path = tree / name
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        if "importlinter" not in raw:
            continue
        modules, count = _extract_modules(path, raw)
        if not count:
            continue
        digest = hashlib.sha256(
            "\n".join(sorted(modules)).encode() + str(count).encode()
        ).hexdigest()[:16]
        return digest, tuple(sorted(modules)), count
    return None


def _extract_modules(path: Path, raw: str) -> tuple[set[str], int]:
    """Module names and contract count, from either config dialect."""
    modules: set[str] = set()
    count = 0
    if path.name == "pyproject.toml":
        try:
            data = tomllib.loads(raw)
        except tomllib.TOMLDecodeError:
            return modules, 0
        section = data.get("tool", {}).get("importlinter", {})
        contracts = section.get("contracts", [])
        count = len(contracts)
        for contract in contracts:
            for key in MODULE_KEYS:
                modules.update(_as_names(contract.get(key)))
        return modules, count

    parser = configparser.ConfigParser()
    try:
        parser.read_string(raw)
    except configparser.Error:
        return modules, 0
    for section_name in parser.sections():
        if not section_name.startswith("importlinter"):
            continue
        if ":contract:" in section_name:
            count += 1
        for key in MODULE_KEYS:
            if parser.has_option(section_name, key):
                modules.update(_as_names(parser.get(section_name, key)))
    return modules, count


def _as_names(value: object) -> set[str]:
    """Module names from a scalar, a list, or an import-linter multiline block.

    Splits on `:` as well as newlines and commas. A Layers contract writes
    independent siblings on one line as `a : b : c`, and the first draft of this
    parser turned cloudai's whole layer line into the single bogus name
    `installer : parser : report_generator : ...`, which then counted as a module
    that does not exist and inflated the dead-rule signal.
    """
    if value is None:
        return set()
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, (list, tuple)):
        raw = [str(item) for item in value]
    else:
        return set()
    # Split AFTER flattening, not instead of: a TOML `layers = ["a : b : c"]`
    # is a list whose single element still carries the sibling syntax, and the
    # first draft split only the scalar branch.
    parts = [piece for item in raw for piece in re.split(r"[\n,:]", item)]
    # `(` and `)` wrap Layers contracts' independent-sibling groups.
    return {part.strip().strip("()").strip() for part in parts if part.strip()}


def _merged_graph(tree: Path):
    """Modules from the repo root AND from any `src/`-style source directory.

    A src-layout package must be scanned with `src` as the scan ROOT, so its
    modules are named `kio.schema`, which is what the contracts say. Scanning
    from the repo root instead names them `src.kio.schema` (and `extra_roots`
    keeps the prefix too), which made all six of kio's contract names read as
    modules that do not exist: a fake dead-rule signal manufactured entirely by
    the scanner looking in the wrong place.
    """
    import networkx as nx

    roots = [tree, *(tree / d for d in ("src", "lib") if (tree / d).is_dir())]
    merged = nx.DiGraph()
    built = False
    for root in roots:
        try:
            part = build_graph(root, ignored_dirs=DEFAULT_IGNORED_DIRS)
        except Exception:
            continue
        built = True
        merged.add_nodes_from(part.nodes(data=True))
        merged.add_edges_from(part.edges)
    return merged if built else None


def _named_by(module: str, names: tuple[str, ...], roots: frozenset[str] = frozenset()) -> bool:
    """Does any contract name cover `module`?

    Two import-linter spellings the first draft mishandled, both of which
    manufactured fake dead rules:

    - `pkg.*` wildcards, which never matched, so `kio.*` read as a module that
      does not exist.
    - CONTAINER-RELATIVE layer names. A Layers contract with `containers = cloudai`
      writes its layers as `installer`, `parser`, `runner`, meaning
      `cloudai.installer` and so on. Matching those absolutely said seven of
      cloudai's eight names were missing, when every one of them exists.
    """
    for name in names:
        stem = name[:-2] if name.endswith(".*") else name
        candidates = [stem, *(f"{root}.{stem}" for root in roots)]
        for candidate in candidates:
            if module == candidate or module.startswith(candidate + "."):
                return True
    return False


def coverage_of(tree: Path, named: tuple[str, ...]) -> dict:
    """How much of the tree the contract-named modules actually reach.

    Deliberately the same question `archy check` now answers for its own configs
    (#362), asked of somebody else's contracts: a rule set naming three packages
    in a thousand-module repo governs almost nothing, and a zero violation count
    from it means almost nothing either.
    """
    graph = _merged_graph(tree)
    if graph is None:
        return {}
    internal = [n for n, data in graph.nodes(data=True) if not data.get("external")]
    if not internal:
        return {}
    # Roots taken from the GRAPH, not from the contract names. Contracts
    # routinely name EXTERNAL packages ("domain must not import flask"), and
    # archy's graph is internal-only, so deriving roots from the names let
    # `flask` and `fastapi` count as internal modules that had gone missing:
    # a fake dead rule for what is really a perfectly healthy rule about a
    # third-party dependency.
    internal_roots = frozenset(n.split(".")[0] for n in internal)
    roots = frozenset(name.split(".")[0] for name in named) & internal_roots
    in_scope = [n for n in internal if n.split(".")[0] in roots]
    internal_named = tuple(m for m in named if m.split(".")[0] in internal_roots)
    governed = [n for n in in_scope if _named_by(n, named, roots)]
    edges = [(a, b) for a, b in graph.edges if a in set(in_scope) and b in set(in_scope)]
    governed_set = set(governed)
    governed_edges = [(a, b) for a, b in edges if a in governed_set and b in governed_set]
    return {
        "modules_in_scope": len(in_scope),
        "modules_governed": len(governed),
        "edges_in_scope": len(edges),
        "edges_governed": len(governed_edges),
        # A named module absent from the tree is the exact "dead rule" signal:
        # the contract still says it, and it no longer exists. Counted ONLY over
        # names that refer to this project's own packages, since a name rooted
        # outside them is a third-party reference, not rot.
        "named_modules": len(named),
        "named_modules_internal": len(internal_named),
        "named_modules_external": len(named) - len(internal_named),
        "named_modules_absent": sum(
            1 for m in internal_named if not any(_named_by(n, (m,), roots) for n in internal)
        ),
    }


def sample_commits(tree: Path, count: int) -> list[str]:
    """Evenly spaced commits since adoption, oldest first.

    Even spacing rather than #359's random sample: this study asks whether
    things get worse over time, and a trend needs an ordered series.
    """
    adopted = adoption_commit(tree)
    if adopted is None:
        return []
    span = git(tree, "log", "--no-merges", "--reverse", "--format=%H", f"{adopted}..HEAD").split()
    span = [adopted, *span]
    if len(span) <= count:
        return span
    step = len(span) / count
    return [span[int(i * step)] for i in range(count)]


def measure_repo(repo: str, tree: Path, shas: list[str], ledger: Ledger, timeout: float) -> None:
    for index, sha in enumerate(shas):
        key = f"{repo}:{sha}"
        if ledger.is_done(key):
            continue
        if not checkout(tree, sha):
            ledger.record(key, {"repo": repo, "sha": sha, "error": "checkout"}, status="error")
            continue
        parsed = parse_contracts(tree)
        if parsed is None:
            ledger.record(key, {"repo": repo, "sha": sha, "error": "no config"}, status="error")
            continue
        digest, named, contract_count = parsed
        result = evaluate(tree, timeout)
        row = {
            "repo": repo,
            "sha": sha,
            "order": index,
            "date": git(tree, "log", "-1", "--format=%cI", sha).strip(),
            "config_hash": digest,
            "contract_count": contract_count,
            "evaluable": result is not None,
            "broken_names": sorted(result["broken_names"]) if result else [],
            "broken": result["broken"] if result else None,
            **coverage_of(tree, named),
        }
        ledger.record(key, row)
        flag = "BROKEN" if row["broken"] else ("ok" if result else "unevaluable")
        print(
            f"      [{index + 1}/{len(shas)}] {sha[:10]} {flag:<12} "
            f"contracts={contract_count} governed="
            f"{row.get('modules_governed', '?')}/{row.get('modules_in_scope', '?')}",
            flush=True,
        )


def summarize(rows: list[dict]) -> str:
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_repo[row["repo"]].append(row)
    for series in by_repo.values():
        series.sort(key=lambda r: r["order"])

    lines = [f"repos: {len(by_repo)}   samples: {len(rows)}"]
    evaluable = [r for r in rows if r.get("evaluable")]
    lines.append(f"evaluable samples: {len(evaluable)} (dropped: {len(rows) - len(evaluable)})")

    standing, relaxed, resolved = [], [], []
    for repo, series in by_repo.items():
        usable = [r for r in series if r.get("evaluable")]
        for prev, curr in itertools.pairwise(usable):
            gone = set(prev["broken_names"]) - set(curr["broken_names"])
            held = set(prev["broken_names"]) & set(curr["broken_names"])
            if held:
                standing.append((repo, len(held)))
            for name in gone:
                resolved.append((repo, name))
                # The rule set changed in the same step the violation vanished:
                # consistent with the rule being edited rather than obeyed.
                if prev["config_hash"] != curr["config_hash"]:
                    relaxed.append((repo, name))

    lines.append("")
    lines.append("SIGNAL 1  standing violations (already visible in CI)")
    repos_with_standing = {repo for repo, _ in standing}
    lines.append(
        f"  {len(standing)} consecutive-sample pairs held a violation, "
        f"in {len(repos_with_standing)} of {len(by_repo)} repos"
    )

    lines.append("")
    lines.append("SIGNAL 2  rule relaxation (invisible today)")
    if resolved:
        share = len(relaxed) / len(resolved)
        lines.append(
            f"  {len(relaxed)} of {len(resolved)} resolutions ({share:.0%}) came with a "
            f"config change: NULL if below 5%"
        )
    else:
        lines.append("  no violations resolved in the sample: signal 2 is untestable here")

    lines.append("")
    lines.append("SIGNAL 3  dead rules (invisible today)")
    absent = [r for r in evaluable if r.get("named_modules_absent")]
    lines.append(
        f"  {len(absent)} of {len(evaluable)} samples name at least one module that does not "
        f"exist in the tree"
    )
    if evaluable:
        latest = [
            series[-1] for series in by_repo.values() if series and series[-1].get("evaluable")
        ]
        dead_now = sum(1 for r in latest if r.get("named_modules_absent"))
        lines.append(f"  at the latest sample: {dead_now} of {len(latest)} repos")

    lines.append("")
    lines.append("SIGNAL 2b  rules deleted outright (invisible today)")
    # Weaker than 2 but far easier to observe: a project that keeps REMOVING
    # contracts is decaying whether or not a violation ever fired. Found in the
    # smoke run, where one repo went from 6 contracts to 1.
    shrank = 0
    for repo, series in by_repo.items():
        counts = [r["contract_count"] for r in series if r.get("contract_count")]
        if len(counts) >= 2 and counts[-1] < counts[0]:
            shrank += 1
            lines.append(f"  {repo:<44} contracts {counts[0]} -> {counts[-1]}")
    lines.append(f"  {shrank} of {len(by_repo)} repos ended with FEWER contracts than they started")

    lines.append("")
    lines.append("SIGNAL 4  coverage erosion (invisible today)")
    eroding = 0
    measured = 0
    for repo, series in by_repo.items():
        points = [
            (r["order"], r["modules_governed"] / r["modules_in_scope"])
            for r in series
            if r.get("modules_in_scope")
        ]
        if len(points) < 3:
            continue
        measured += 1
        slope = _slope(points)
        if slope < 0:
            eroding += 1
        lines.append(f"  {repo:<44} coverage slope {slope:+.4f} per sample")
    lines.append(
        f"  {eroding} of {measured} repos with 3+ points show falling coverage: NULL if that is 0"
    )
    return "\n".join(lines)


def _slope(points: list[tuple[int, float]]) -> float:
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    denom = sum((x - mean_x) ** 2 for x, _ in points)
    if denom == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / denom


def _load_ok_rows() -> list[dict]:
    """Completed rows from the ledger. `status != "ok"` covers checkout failures
    and configs that would not parse, which are dropped rather than scored."""
    rows = [json.loads(line) for line in LEDGER_PATH.read_text().splitlines() if line.strip()]
    return [r for r in rows if r.get("status") == "ok"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--repos", type=int, default=14)
    ap.add_argument("--samples", type=int, default=12, help="commits sampled per repo")
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    ledger = Ledger(LEDGER_PATH)
    if args.report or not args.measure:
        if not LEDGER_PATH.exists():
            print("no ledger yet; run --measure first")
            return 1
        print(summarize(_load_ok_rows()))
        return 0

    corpus = json.loads(CORPUS.read_text())["repos"][: args.repos]
    for index, entry in enumerate(corpus, 1):
        repo = entry["repo"]
        print(f"[{index}/{len(corpus)}] {repo}", flush=True)
        tree = clone(repo)
        if tree is None:
            print("      clone failed", flush=True)
            continue
        shas = sample_commits(tree, args.samples)
        if len(shas) < 3:
            print(f"      skipped: {len(shas)} commits since adoption, need 3+", flush=True)
            continue
        try:
            measure_repo(repo, tree, shas, ledger, args.timeout)
        except Exception as exc:
            print(f"      crashed: {type(exc).__name__}: {exc}", flush=True)

    print("\n" + summarize(_load_ok_rows()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
