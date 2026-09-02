#!/usr/bin/env python
"""Does the problem archy targets actually occur? (#359)

    uv run python bench/contract_prevalence.py --discover           # build the corpus
    uv run python bench/contract_prevalence.py --measure --repos 10 # sample commit pairs
    uv run python bench/contract_prevalence.py --report

## The question, and why it outranks the agent benches

#356 measured agents on SWE-bench and got p_B = 0 of 25. Q1a measured *humans*
at 0.5% of commits introducing a cycle. Read together, the event archy exists to
catch looks rare regardless of who writes the code, and that is a harder problem
for the thesis than any corpus objection.

This bench asks the blunt version: **in projects that already declare an
architecture and check it in CI, how often does a commit actually break it?**

## What makes this evidence the other benches could not produce

Every previous measurement had to author the intent being measured. #353/#354/
#355 wrote layer rules for six repos that declared none, and
`bench/q1b_layers/README.md` carries that bias risk in full. Here the contracts
are **the project's own**, in the project's own words, evaluated with the
project's own tool. The measurer is out of the loop.

## Design: sampled commit PAIRS, not a full history walk

The quantity wanted is "how often does a commit introduce a violation its parent
did not have". That needs both sides of an edge, so each unit of work evaluates
contracts at a commit AND at its parent, with the config **as declared at that
commit** (it comes from the checkout, so this is automatic).

Sampling commit pairs uniformly from Python-touching commits gives an unbiased
estimate of the per-commit introduction rate without walking entire histories,
which matters because import-linter on a large repo is seconds to minutes.

## Feasibility, verified before this file was written

import-linter builds its graph statically, so a project's contracts can be
evaluated **without installing that project's dependencies**: putting the repo
root and any `src/` on PYTHONPATH is enough. Checked on Aiven-Open/kio, where
5 real contracts evaluated across 1,841 files with nothing installed. Had that
failed, this bench would have been unaffordable and the ticket would have needed
a different design.

## Discipline inherited from #356

- A commit whose contracts cannot be evaluated is **dropped, not counted clean**.
  Config errors, missing packages, and grimp failures are their own bucket. A
  repo that mostly fails to evaluate is a finding about that repo, not a zero.
- Every row is written as it completes (`Ledger`), so a kill costs one pair.
- Report per repo as well as pooled: one large repo with many contracts would
  otherwise dominate a pooled rate.

archy:owns        adoption_commit, checkout, clone, discover, evaluate, gh_json, git,
                  main, measure_pair, package_paths, python_commits, summarize
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(REPO_ROOT / "bench"))
from _supervise import Ledger  # noqa: E402

CORPUS = REPO_ROOT / "bench/contract_repos.json"
LEDGER_PATH = REPO_ROOT / "bench/contract_prevalence.jsonl"
CLONES = REPO_ROOT / "bench/cache/contract_clones"

# GitHub code-search queries that find a declared architecture. `.importlinter`
# and the pyproject table are the same tool configured two ways; both are
# searched because projects use whichever they prefer.
SEARCH_QUERIES = (
    "filename:.importlinter",
    "tool.importlinter+filename:pyproject.toml",
)

# import-linter prints one status line per contract plus a tally. Parsing the
# tally alone would lose WHICH contract broke, and "which" is what separates a
# real architectural regression from a rule someone relaxed.
TALLY = re.compile(r"Contracts:\s+(\d+) kept,\s+(\d+) broken", re.IGNORECASE)
STATUS = re.compile(r"^(?P<name>.+?)\s+(?P<verdict>KEPT|BROKEN)\s*$")
ANALYZED = re.compile(r"Analyzed (\d+) files", re.IGNORECASE)


def git(tree: Path, *args: str) -> str:
    """Run a git command against a checkout and return its stdout.

    `check=False` deliberately: several callers ask questions that legitimately
    have no answer (a root commit has no parent, a repo may never have adopted
    import-linter), and an empty string is the honest response to those.
    Mirrors the helper in `bench/inloop_prevalence.py`.
    """
    return subprocess.run(
        ["git", "-C", str(tree), *args], capture_output=True, text=True, check=False
    ).stdout


def gh_json(path: str) -> dict:
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=False).stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}


def discover(limit: int) -> list[dict]:
    """Repos that declare an import-linter architecture, with their metadata.

    Deliberately NOT filtered by stars. A popularity floor would bias the corpus
    toward large mature projects, which is precisely the population most likely
    to have stable architecture and therefore the fewest violations. Stars are
    recorded so the analysis can stratify instead.

    The manifest is ORDERED by stars descending, which is a different thing from
    filtering: nothing is discarded, but `--repos N` then measures the most
    substantial N rather than the alphabetically first N. The first draft took
    them alphabetically and got 40 repos of which most had single-digit commit
    counts, which cannot yield a per-commit rate at all.
    """
    names: set[str] = set()
    for query in SEARCH_QUERIES:
        for page in range(1, 11):
            data = gh_json(f"search/code?q={query}&per_page=100&page={page}")
            items = data.get("items", [])
            if not items:
                break
            names.update(item["repository"]["full_name"] for item in items)
            if len(names) >= limit * 3:
                break
    repos: list[dict] = []
    for name in sorted(names):
        meta = gh_json(f"repos/{name}")
        if not meta or meta.get("fork") or meta.get("archived"):
            continue
        repos.append(
            {
                "repo": name,
                "stars": meta.get("stargazers_count", 0),
                "size_kb": meta.get("size", 0),
                "pushed_at": meta.get("pushed_at"),
                "default_branch": meta.get("default_branch", "main"),
            }
        )
        if len(repos) >= limit:
            break
    return sorted(repos, key=lambda r: (-r["stars"], r["repo"]))


def clone(repo: str) -> Path | None:
    """Blob-filtered clone: full history, blobs fetched only for what is checked out."""
    CLONES.mkdir(parents=True, exist_ok=True)
    target = CLONES / repo.replace("/", "__")
    if target.exists():
        return target
    result = subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--filter=blob:none",
            f"https://github.com/{repo}.git",
            str(target),
        ],
        capture_output=True,
    )
    return target if result.returncode == 0 else None


def adoption_commit(tree: Path) -> str | None:
    """The commit that first introduced an import-linter configuration.

    Sampling uniformly over ALL history was the first design and it was wrong:
    most repos adopted import-linter recently, so the sample landed on commits
    from before there was any declared architecture, where the tool correctly
    reports "Could not read any configuration". Those are not clean commits and
    they are not violations either; they are outside the question being asked.

    The rate this bench estimates is therefore explicitly conditional: **given
    that a project has declared an architecture, how often does a commit break
    it.** Measuring the pre-adoption era would answer a different question, and
    a less interesting one.
    """
    out = git(
        tree,
        "log",
        "-S",
        "importlinter",
        "--reverse",
        "--format=%H",
        "--",
        "pyproject.toml",
        "setup.cfg",
        ".importlinter",
    ).split()
    return out[0] if out else None


def python_commits(tree: Path, cap: int = 4000) -> list[str]:
    """Commits touching .py files SINCE the architecture was declared.

    Merges are excluded because a merge commit's diff against its first parent
    attributes the whole side branch to one commit, which would inflate any
    per-commit rate.
    """
    adopted = adoption_commit(tree)
    span = [f"{adopted}..HEAD"] if adopted else []
    out = subprocess.run(
        [
            "git",
            "-C",
            str(tree),
            "log",
            "--no-merges",
            f"-{cap}",
            "--format=%H",
            *span,
            "--",
            "*.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return out.split()


def package_paths(tree: Path) -> str:
    """PYTHONPATH for a checkout: the repo root plus any conventional source root.

    import-linter needs to *find* the packages, not import their dependencies,
    so this is enough to evaluate contracts without installing anything.
    """
    # ABSOLUTE paths: the child runs with `cwd=tree`, so a relative entry here
    # would resolve against the checkout and silently find nothing, which this
    # module reports as "unevaluable" rather than as the configuration error it
    # actually is.
    root = tree.resolve()
    parts = [str(root)]
    for candidate in ("src", "lib"):
        if (root / candidate).is_dir():
            parts.append(str(root / candidate))
    return ":".join(parts)


def evaluate(tree: Path, timeout: float) -> dict | None:
    """Run the project's own contracts. None when they cannot be evaluated.

    None is NOT "no violations". A repo whose contracts fail to evaluate is
    dropped from the rate and counted in its own bucket, the same discipline
    that kept #356's unmeasurable runs out of p_B.
    """
    # The CONSOLE SCRIPT, not `python -m importlinter.cli`: the module form
    # exits 0 with completely empty output, which this function would then
    # (correctly) treat as unevaluable, silently dropping every pair. The first
    # smoke run lost all 12 pairs to exactly that.
    lint_imports = Path(sys.executable).parent / "lint-imports"
    try:
        proc = subprocess.run(
            [str(lint_imports)],
            cwd=str(tree),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"PYTHONPATH": package_paths(tree), "PATH": "/usr/bin:/bin"},
        )
    except subprocess.TimeoutExpired:
        return None
    blob = f"{proc.stdout}\n{proc.stderr}"
    tally = TALLY.search(blob)
    if not tally:
        return None
    broken = [
        m.group("name").strip()
        for m in (STATUS.match(line.strip()) for line in blob.splitlines())
        if m and m.group("verdict") == "BROKEN"
    ]
    # `Analyzed N files` is the reach of the contracts, and it is needed to
    # read a zero. A repo whose contracts govern a handful of modules can
    # report 0 violations forever without that meaning anything, which is the
    # same trap #355 hit with dead layer patterns and #362 found in archy's own
    # config (33 of 76 modules in no layer, yet `archy check` says "clean").
    analyzed = ANALYZED.search(blob)
    return {
        "kept": int(tally.group(1)),
        "broken": int(tally.group(2)),
        "broken_names": sorted(broken),
        "files_analyzed": int(analyzed.group(1)) if analyzed else None,
    }


def checkout(tree: Path, sha: str) -> bool:
    for cmd in (
        ["git", "-C", str(tree), "reset", "--hard", "--quiet"],
        ["git", "-C", str(tree), "clean", "-qfdx"],
        ["git", "-C", str(tree), "checkout", "--quiet", "--force", "--detach", sha],
    ):
        if subprocess.run(cmd, capture_output=True).returncode != 0:
            return False
    return True


def measure_pair(tree: Path, sha: str, timeout: float) -> dict:
    """Contracts at `sha` and at its first parent, and what `sha` introduced."""
    parent = git(tree, "rev-parse", f"{sha}^").strip()
    if not parent:
        return {"error": "no parent (root commit)"}

    if not checkout(tree, sha):
        return {"error": f"checkout {sha[:10]} failed"}
    after = evaluate(tree, timeout)
    if not checkout(tree, parent):
        return {"error": f"checkout parent {parent[:10]} failed"}
    before = evaluate(tree, timeout)

    if after is None or before is None:
        return {
            "evaluable": False,
            "sha": sha,
            "parent": parent,
            "reason": "contracts did not evaluate at one or both ends",
        }
    introduced = sorted(set(after["broken_names"]) - set(before["broken_names"]))
    fixed = sorted(set(before["broken_names"]) - set(after["broken_names"]))
    return {
        "evaluable": True,
        "sha": sha,
        "parent": parent,
        "contracts_total": after["kept"] + after["broken"],
        "files_analyzed": after.get("files_analyzed"),
        "broken_before": before["broken"],
        "broken_after": after["broken"],
        "introduced": introduced,
        "fixed": fixed,
        # The rate this bench exists to estimate.
        "introduced_violation": bool(introduced),
    }


def summarize(rows: list[dict]) -> str:
    evaluable = [r for r in rows if r.get("evaluable")]
    unevaluable = len(rows) - len(evaluable)
    lines = [
        f"commit pairs sampled: {len(rows)}   evaluable: {len(evaluable)}   "
        f"dropped (would not evaluate): {unevaluable}",
    ]
    if not evaluable:
        lines.append("\nNo evaluable pairs: the rate is undefined, not zero.")
        return "\n".join(lines)

    hits = [r for r in evaluable if r["introduced_violation"]]
    rate = len(hits) / len(evaluable)
    lines.append(
        f"\ncommits introducing a contract violation: {len(hits)}/{len(evaluable)} = {rate:.2%}"
    )
    lines.append("  for scale, Q1a's human cycle-introduction rate was 0.5% per commit")
    fixed = sum(1 for r in evaluable if r.get("fixed"))
    lines.append(f"commits that FIXED a standing violation: {fixed}")

    # Sensitivity, not decoration: a rate of zero from contracts that govern
    # three modules is not evidence the event does not happen.
    reach = [r["files_analyzed"] for r in evaluable if r.get("files_analyzed")]
    contracts = [r["contracts_total"] for r in evaluable if r.get("contracts_total")]
    if reach:
        lines.append(
            f"reach: median {sorted(reach)[len(reach) // 2]} files analyzed, "
            f"median {sorted(contracts)[len(contracts) // 2] if contracts else 0} contracts"
        )

    per_repo: dict[str, list[dict]] = defaultdict(list)
    for row in evaluable:
        per_repo[row["repo"]].append(row)
    lines.append("\nper repo:")
    for repo in sorted(per_repo, key=lambda r: -len(per_repo[r])):
        group = per_repo[repo]
        hit = sum(1 for r in group if r["introduced_violation"])
        standing = sum(1 for r in group if r["broken_after"])
        lines.append(
            f"  {repo:<44} {hit}/{len(group)} introduced   "
            f"{standing}/{len(group)} commits sit on a broken contract"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--discover", action="store_true", help="build the corpus manifest")
    ap.add_argument("--measure", action="store_true", help="sample and evaluate commit pairs")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--limit", type=int, default=60, help="repos to discover")
    ap.add_argument("--repos", type=int, default=10, help="repos to measure")
    ap.add_argument("--commits", type=int, default=20, help="commit pairs sampled per repo")
    ap.add_argument("--timeout", type=float, default=600.0, help="seconds per contract run")
    ap.add_argument("--seed", type=int, default=20260726, help="commit sampling seed")
    ap.add_argument(
        "--min-commits",
        type=int,
        default=50,
        help="skip repos with fewer Python-touching commits; a rate needs a denominator",
    )
    args = ap.parse_args()

    if args.discover:
        repos = discover(args.limit)
        CORPUS.write_text(json.dumps({"repos": repos}, indent=2, sort_keys=True) + "\n")
        print(f"# {len(repos)} repos declaring an import-linter architecture -> {CORPUS}")
        for r in repos[:15]:
            print(f"  {r['stars']:>6} stars  {r['repo']}")
        return 0

    ledger = Ledger(LEDGER_PATH)
    if args.report:
        rows = [json.loads(line) for line in LEDGER_PATH.read_text().splitlines() if line.strip()]
        print(summarize([r for r in rows if r.get("status") == "ok"]))
        return 0

    if not args.measure:
        ap.print_help()
        return 0

    corpus = json.loads(CORPUS.read_text())["repos"][: args.repos]
    rng = random.Random(args.seed)
    skipped_no_config = 0
    for index, entry in enumerate(corpus, 1):
        repo = entry["repo"]
        print(f"[{index}/{len(corpus)}] {repo}", flush=True)
        tree = clone(repo)
        if tree is None:
            print("      clone failed", flush=True)
            continue
        if adoption_commit(tree) is None:
            # GitHub code search returns false positives: a repo can match
            # "importlinter" in a doc, a lockfile, or a deleted config and never
            # actually declare an architecture. Out of scope, not a zero, and
            # logged so the corpus attrition is visible rather than silent.
            print("      skipped: no import-linter config in this repo's history", flush=True)
            skipped_no_config += 1
            continue
        commits = python_commits(tree)
        if len(commits) < args.min_commits:
            # Not a quality judgement about the project: you cannot estimate a
            # per-commit rate from a handful of commits. Logged, never silent.
            print(f"      skipped: {len(commits)} python commits < {args.min_commits}", flush=True)
            continue
        sample = rng.sample(commits, min(args.commits, len(commits)))
        for sha in sample:
            key = f"{repo}:{sha}"
            if ledger.is_done(key):
                continue
            row = measure_pair(tree, sha, args.timeout)
            row["repo"] = repo
            if "error" in row:
                ledger.record(key, row, status="error")
                continue
            ledger.record(key, row)
            mark = "VIOLATION" if row.get("introduced_violation") else "ok"
            print(
                f"      {sha[:10]} {mark:<10} broken {row.get('broken_before')} -> "
                f"{row.get('broken_after')}",
                flush=True,
            )

    rows = [json.loads(line) for line in LEDGER_PATH.read_text().splitlines() if line.strip()]
    print("\n" + summarize([r for r in rows if r.get("status") == "ok"]))
    if skipped_no_config:
        print(
            f"\ncorpus attrition: {skipped_no_config} of {len(corpus)} repos matched the code "
            "search but declare no import-linter architecture"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
