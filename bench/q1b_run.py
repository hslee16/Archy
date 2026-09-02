#!/usr/bin/env python
"""Run the Q1b arm-B pilot: how often does an unaided agent break structure? (#348)

    uv run python bench/q1b_run.py --limit 5 --dry-run   # plan only, spends nothing
    uv run python bench/q1b_run.py --limit 25            # spends agent hours

**This is the only piece of the Q1b harness that spends agent time.** Everything
it depends on (the task selector #351, the outcome measure #352, the layer rules
#353/#354/#355) was built and validated first, precisely so that a spent run is
never the thing that discovers a bug in the measurement.

It runs on SUBSCRIPTION auth, not an API key: `claude -p` authenticates however
the surrounding session is configured, so a nested run inside a logged-in
session bills against usage limits rather than an API account (verified live in
#282; nothing here sets `ANTHROPIC_API_KEY`). The cost that matters is wall
clock and rate limits, roughly `--limit x --max-wall` in the worst case.
`total_cost_usd` is still recorded from the CLI's JSON, but under subscription
auth it is a NOTIONAL list-price figure, not a bill, and must never be reported
as what the pilot cost.

## Arm B only, and why that ordering

The A/B needs 5,738 pairs at the Q1a human cycle rate (0.5%) and 78 at 30%, so
the sample size is unknowable until p_B is known. Running arm B alone first
buys that number for half the cost of a pilot A/B.

The reading is **pre-registered** in `bench/q1b_tasks.py` and must not be
revised after seeing the result:

- **p_B >= 25%**: powered at ~80-130 pairs. Proceed to arm A.
- **p_B <= 10%**: **the corpus is wrong, not archy.** SWE-bench is bug fixes by
  construction, which is the localized regime agents are expected to handle. It
  means moving to real multi-file feature/refactor commits. It is NOT evidence
  that archy is unnecessary, and must not be reported as such.

## The outcome

`structurally_bad` here is broader than `q1b_score.py`'s, and deliberately so:

    cycle_regression  OR  a layer violation the agent introduced

`q1b_score.py` predates the authored layer rules and gates on cycles alone,
because at the time no SWE-bench repo declared an architecture. Six do now
(`bench/q1b_layers/`), so the declared-layer half of the protocol's outcome can
finally fire. Layer violations are counted as `after - before` per rule, not as
`after > 0`: the configs are validated to be silent at the top-25 base commits,
but this runner also accepts `--limit` past 25, where that has not been checked.

Score regression is recorded and never gates: Q1a Finding 3 (98% of human
score drops are under 0.005, and the worst structural event in that corpus moved
the score UP).

## What is easy to get wrong here, and how each is handled

- **Unmeasurable runs are dropped, never scored clean.** `measurable=False`
  means the tree could not be analysed at all. Folding those in as passes would
  bias p_B down by exactly the runs that broke something badly enough to stop
  the parser.
- **Only stalls are retried** (`with_retries`). A non-zero agent exit is a real
  result; re-rolling it selects for runs that happened to succeed.
- **The package directory is resolved per base commit.** `requests` lives at
  `src/requests` today and at `requests/` at the base commit this pilot uses.
- **Each run starts from a hard reset**, in a dedicated worktree, so the shared
  `bench/repo_cache/` clones other benches use are never left detached.
- **Every row is written as it completes** (`Ledger`), so a kill mid-pilot loses
  one run rather than the whole spend.

archy:owns        RateLimited, agent_env, changed_files, hit_rate_limit,
                  layer_violations, main, parse_reset_at, problem_statements,
                  project_slug, reset_worktree, resolve_package, run_agent, run_task,
                  run_with_limit_backoff, save_transcript, summarize, worktree_at
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.append(str(REPO_ROOT / "bench"))
import q1b_tasks  # noqa: E402
from _supervise import Ledger, StallTimeout, WallTimeout, run_supervised, with_retries  # noqa: E402
from q1b_score import score_working_tree  # noqa: E402

from archy.graph import DEFAULT_IGNORED_DIRS, build_graph  # noqa: E402
from archy.layers import find_violations, load_config  # noqa: E402

CACHE = REPO_ROOT / "bench/repo_cache"
CONFIGS = REPO_ROOT / "bench/q1b_layers"
WORKTREES = REPO_ROOT / "bench/cache/q1b_runs"
TRANSCRIPTS = REPO_ROOT / "bench/q1b_transcripts"
LEDGER_PATH = REPO_ROOT / "bench/q1b_results.jsonl"

# SWE-bench `repo` -> (cached clone dir, import package name).
REPOS = {
    "django/django": ("django", "django"),
    "psf/requests": ("requests", "requests"),
    "scikit-learn/scikit-learn": ("scikit-learn", "sklearn"),
    "sympy/sympy": ("sympy", "sympy"),
    "pydata/xarray": ("xarray", "xarray"),
    "matplotlib/matplotlib": ("matplotlib", "matplotlib"),
}

# Where a top-level package can live, in the order to try. `requests` moved from
# `requests/` to `src/requests` and `matplotlib` sits under `lib/`, so guessing
# one layout would silently measure an empty tree.
PACKAGE_ROOTS = ("", "src", "lib")

ALLOWED_TOOLS = ("Read", "Write", "Edit", "Bash", "Grep", "Glob")

# Environment variables that would point an agent's `pip`/`python` at archy's
# own virtualenv. `uv run` sets VIRTUAL_ENV and the UV_* family; PYTHONPATH and
# PYTHONHOME would leak the parent interpreter the same way.
SCRUBBED_ENV = frozenset(
    {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "UV_PROJECT_ENVIRONMENT", "UV_CACHE_DIR"}
)

# Below this many measurable runs, `summarize` refuses to read the thresholds.
MIN_N_FOR_VERDICT = 10

# Arm B: no archy, no structural hint. The wording must not prime the agent
# toward or away from structural care, or the arm stops being a control.
PROMPT = """\
{problem_statement}

Implement a fix for the issue described above in this repository. Make the code
changes you judge necessary. Do not run the full test suite; it is slow and is
not what is being checked here."""


class RateLimited(RuntimeError):
    """The subscription's usage limit was hit, so the run never happened.

    Distinct from every other failure here on purpose: a stall is a stuck
    process, a non-zero exit is a real (bad) agent result, but a rate limit is a
    fact about the ACCOUNT. Retrying it after a wait cannot bias the sample,
    which is why it is the one failure that gets an unbounded-ish wait rather
    than being recorded and moved past.
    """

    def __init__(self, message: str, *, reset_at: float | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


# The CLI surfaces a subscription limit as prose, not a status code, so these
# are the phrasings seen in practice plus the HTTP shapes an API-keyed run would
# produce.
RATE_LIMIT_PATTERNS = (
    "usage limit reached",
    "rate limit",
    "rate_limit_error",
    "429",
    "too many requests",
    "overloaded_error",
)


def hit_rate_limit(stdout: str, stderr: str, returncode: int) -> bool:
    """True only when the CLI ITSELF reported a limit.

    "Over-match, it only costs a wait" was wrong, and cost a real one: on a
    SUCCESSFUL run whose *agent output* happened to mention a rate limit, the
    naive whole-blob match slept 5m, then 10m, and would have halted the pilot
    after eight waits. Agent prose about HTTP 429s is a normal thing to find in
    a fix for an HTTP library, or a sympy run that quotes a traceback.

    So the match is scoped to where the CLI speaks, never to where the agent
    does: stderr, a non-zero exit, or a JSON envelope with `is_error` set. A
    successful envelope is never a rate limit no matter what it contains.
    """
    if any(p in stderr.lower() for p in RATE_LIMIT_PATTERNS):
        return True
    if returncode != 0:
        return any(p in stdout.lower() for p in RATE_LIMIT_PATTERNS)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict) or not payload.get("is_error"):
        return False
    text = f"{payload.get('result', '')} {payload.get('subtype', '')}".lower()
    return any(p in text for p in RATE_LIMIT_PATTERNS)


def parse_reset_at(blob: str) -> float | None:
    """The epoch seconds in `Claude AI usage limit reached|1753500000`, if present."""
    match = re.search(r"usage limit reached\|(\d{9,11})", blob, flags=re.IGNORECASE)
    return float(match.group(1)) if match else None


def project_slug(repo_dir: Path) -> str:
    """Claude Code's `~/.claude/projects/<slug>/` name for a working directory.

    Every non-alphanumeric character collapses to '-', not just '/'; verified
    against a live headless run in #282, where the '/'-only version silently
    missed every transcript.
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", str(repo_dir.resolve()))


def problem_statements(dataset: str) -> dict[str, str]:
    """instance_id -> problem statement, from the cached SWE-bench pull.

    The manifest (`q1b_tasks.json`) deliberately does NOT carry these. It holds
    only what task SELECTION needed, and selection is done blind; keeping the
    prompts out of it is what makes "authored blind to the task set" checkable
    for the layer configs by reading a single committed file.

    First call may fetch 23 pages from the datasets API and cache them under
    `bench/cache/` (gitignored). That is the only network access in the pilot.
    """
    rows = q1b_tasks.fetch_rows(dataset)
    return {row["instance_id"]: row["problem_statement"] for row in rows}


def resolve_package(repo_dir: Path, package: str) -> str | None:
    """The path (relative to `repo_dir`) of `package` AT THIS COMMIT, or None."""
    for root in PACKAGE_ROOTS:
        candidate = Path(root) / package if root else Path(package)
        if (repo_dir / candidate / "__init__.py").exists():
            return str(candidate)
    return None


def worktree_at(name: str, sha: str) -> Path:
    """A dedicated worktree at `sha`, hard-reset so a rerun cannot inherit edits.

    Deliberately not the shared `bench/repo_cache/<name>` clone: other benches
    read those at HEAD, and a pilot that left one detached at a 2019 commit
    would corrupt them silently.

    ORDER MATTERS, and getting it wrong cost 11 runs in the first pilot attempt:
    the previous run's edits must be discarded BEFORE checking out, because
    `git checkout` refuses to clobber local modifications and the agent had
    often `git add`ed its work. Every task after the first in each repo died on
    "Your local changes would be overwritten by checkout". Reset-then-checkout
    (plus `--force`) is the fix; the trailing reset re-pins after the checkout.
    """
    WORKTREES.mkdir(parents=True, exist_ok=True)
    tree = WORKTREES / name
    if not tree.exists():
        subprocess.run(
            ["git", "-C", str(CACHE / name), "worktree", "add", "--detach", str(tree), sha],
            check=True,
            capture_output=True,
        )
    else:
        reset_worktree(tree)
        subprocess.run(
            ["git", "-C", str(tree), "checkout", "--quiet", "--force", "--detach", sha],
            check=True,
            capture_output=True,
        )
    reset_worktree(tree, sha)
    return tree


def layer_violations(tree: Path, name: str) -> Counter | None:
    """Violations per rule under this repo's authored config, or None if absent."""
    cfg_path = CONFIGS / f"{name}.yaml"
    if not cfg_path.exists():
        return None
    cfg = load_config(cfg_path)
    raw = yaml.safe_load(cfg_path.read_text())
    graph = build_graph(tree, ignored_dirs=DEFAULT_IGNORED_DIRS | set(raw.get("exclude", [])))
    counts: Counter = Counter()
    for violation in find_violations(graph, cfg):
        counts[f"{violation.rule.from_layer}->{violation.rule.to_layer}"] += 1
    return counts


def agent_env() -> dict[str, str]:
    """The child environment, with this repo's Python environment scrubbed out.

    THIS IS NOT COSMETIC. The pilot is launched with `uv run`, which exports
    `VIRTUAL_ENV` pointing at archy's own `.venv`. The agent has Bash, and a
    SWE-bench task on scikit-learn very reasonably runs `pip install`: the first
    pilot attempt uninstalled `scipy` from archy's venv and broke an unrelated
    archy test. Inheriting the parent's interpreter also means an agent could
    edit the environment the MEASUREMENT runs in, which is worse than the
    collateral damage.

    `PIP_REQUIRE_VIRTUALENV` then makes a stray `pip install` fail loudly in the
    child rather than silently landing in the user's system Python.
    """
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_ENV}
    env["PIP_REQUIRE_VIRTUALENV"] = "1"
    return env


def reset_worktree(tree: Path, sha: str | None = None) -> None:
    """Discard whatever the last run left behind, staged or not.

    `sha` re-pins to a specific commit; omitted, it resets to the current HEAD.
    `-e *.egg-info` because an editable install the agent performed is not the
    agent's edit, and re-creating it costs a rebuild on every task.
    """
    reset = ["git", "-C", str(tree), "reset", "--hard", "--quiet"]
    subprocess.run([*reset, sha] if sha else reset, check=True)
    subprocess.run(["git", "-C", str(tree), "clean", "-qfdx", "-e", "*.egg-info"], check=True)


def changed_files(tree: Path) -> list[str]:
    """Every path the agent touched: modified, staged, or newly created.

    `diff HEAD` rather than plain `diff`, because agents routinely `git add`
    their work and plain `git diff` reports nothing for a staged change. Neither
    form sees untracked files, so `ls-files --others` is unioned in: a run whose
    only contribution is a new module has still edited the tree.
    """
    tracked = subprocess.run(
        ["git", "-C", str(tree), "diff", "HEAD", "--name-only"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    untracked = subprocess.run(
        ["git", "-C", str(tree), "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return sorted(set(tracked) | set(untracked))


def run_agent(tree: Path, prompt: str, *, model: str, max_wall: float) -> dict:
    """One supervised headless `claude` run in `tree`; returns its JSON result.

    Stalls are the only retried failure. `progress_paths` watches both the
    transcript directory and the repo, because an agent that is thinking hard
    touches neither for minutes at a time while still burning CPU, which is what
    the CPU floor in `run_supervised` is for.
    """
    transcript_dir = Path.home() / ".claude" / "projects" / project_slug(tree)
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        model,
        "--dangerously-skip-permissions",
        "--allowedTools",
        ",".join(ALLOWED_TOOLS),
        "--setting-sources",
        "local",
    ]

    def once():
        return run_supervised(
            cmd,
            cwd=tree,
            progress_paths=[transcript_dir, tree],
            max_wall=max_wall,
            env=agent_env(),
        )

    def on_stall(attempt: int, exc: Exception) -> None:
        print(f"      stall on attempt {attempt}: {exc}", flush=True)
        reset_worktree(tree)

    result = with_retries(once, attempts=3, on_error=on_stall)
    blob = f"{result.stdout}\n{result.stderr}"
    if hit_rate_limit(result.stdout, result.stderr, result.returncode):
        # NOT a result. The agent never got to attempt the task, so recording
        # this as a run would bias p_B with a fact about the subscription
        # rather than about the agent. Raised so the caller waits and retries
        # the same task, which is the one retry that cannot select for luck.
        raise RateLimited(blob.strip()[:200], reset_at=parse_reset_at(blob))
    if result.returncode != 0:
        # BOTH streams. The CLI puts its JSON (including error text) on stdout,
        # so recording stderr alone produced `agent exited 1:` with nothing
        # after the colon, which cannot be diagnosed after the fact. A failure
        # row is only useful if it says what failed.
        return {
            "ok": False,
            "returncode": result.returncode,
            "stderr": result.stderr[-1000:],
            "stdout": result.stdout[-1000:],
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "returncode": 0,
            "stderr": "unparseable claude JSON",
            "stdout": result.stdout[-1000:],
        }
    payload["ok"] = True
    payload["wall_seconds"] = result.wall_seconds
    return payload


def save_transcript(tree: Path, session_id: str, instance_id: str) -> str | None:
    src = Path.home() / ".claude" / "projects" / project_slug(tree) / f"{session_id}.jsonl"
    if not src.exists():
        return None
    TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    dst = TRANSCRIPTS / f"{instance_id}_B_{session_id}.jsonl"
    shutil.copy(src, dst)
    return dst.name


def run_task(task: dict, statement: str, *, model: str, max_wall: float) -> dict:
    started_at = time.time()
    name, package = REPOS[task["repo"]]
    tree = worktree_at(name, task["base_commit"])

    pkg = resolve_package(tree, package)
    if pkg is None:
        return {"error": f"package {package!r} not found at {task['base_commit'][:10]}"}

    before = layer_violations(tree, name)
    result = run_agent(
        tree,
        PROMPT.format(problem_statement=statement),
        model=model,
        max_wall=max_wall,
    )
    if not result.get("ok"):
        return {
            "error": f"agent exited {result['returncode']}",
            "stderr": result.get("stderr", ""),
            "stdout": result.get("stdout", ""),
            # Kept even on failure: the transcript is the only record of what
            # the agent actually did before it died, and it is the difference
            # between "the agent gave up" and "the CLI fell over".
            "files_changed_at_failure": len(changed_files(tree)),
        }

    verdict = score_working_tree(tree, pkg, task["base_commit"])
    after = layer_violations(tree, name)
    introduced: dict[str, int] = {}
    if before is not None and after is not None:
        for rule, count in after.items():
            delta = count - before.get(rule, 0)
            if delta > 0:
                introduced[rule] = delta

    changed = changed_files(tree)

    return {
        "instance_id": task["instance_id"],
        "repo": task["repo"],
        "arm": "B",
        "base_commit": task["base_commit"],
        "package": pkg,
        "model": model,
        "measurable": verdict.measurable,
        # The protocol's outcome: a cycle OR a declared-layer violation. Score
        # regression rides along and never gates (Q1a Finding 3).
        "structurally_bad": bool(verdict.cycle_regression or introduced),
        "cycle_regression": verdict.cycle_regression,
        "layer_violations_introduced": introduced,
        "layer_violations_before": dict(before) if before is not None else None,
        "score_regression": verdict.score_regression,
        "new_cyclic_modules": list(verdict.new_cyclic_modules),
        "max_new_scc": verdict.max_new_scc,
        "overall_delta": verdict.overall_delta,
        "files_changed": len(changed),
        "made_edit": bool(changed),
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        # Notional list price from the CLI, NOT a bill: these runs are on
        # subscription auth. Never report it as what the pilot cost.
        "notional_cost_usd": result.get("total_cost_usd"),
        "wall_seconds": round(result.get("wall_seconds", 0.0), 1),
        # Wall-clock stamps, because `wall_seconds` covers only the agent call.
        # Without these the gap between runs is unreconstructable after the
        # fact, which is exactly the question the first live pilot raised and
        # could not answer.
        "started_at": round(started_at, 1),
        "finished_at": round(time.time(), 1),
        "transcript": save_transcript(tree, result.get("session_id", ""), task["instance_id"]),
    }


def run_with_limit_backoff(
    task: dict,
    statement: str,
    *,
    model: str,
    max_wall: float,
    max_waits: int,
    max_backoff: float,
) -> dict:
    """`run_task`, waiting out subscription usage limits rather than burning tasks.

    Without this, one exhausted limit would march through every remaining task
    in seconds, recording each as an error, and the pilot would look finished
    while having measured nothing. The wait is the point of the whole exercise
    being resumable.
    """
    for attempt in range(max_waits + 1):
        try:
            return run_task(task, statement, model=model, max_wall=max_wall)
        except RateLimited as exc:
            if attempt == max_waits:
                raise
            if exc.reset_at:
                wait = min(max(exc.reset_at - time.time(), 60.0), max_backoff)
                why = f"limit resets in {wait / 60:.0f}m"
            else:
                # No reset stamp: back off geometrically instead of hammering.
                wait = min(300.0 * (2**attempt), max_backoff)
                why = "no reset time given, backing off"
            print(
                f"      rate limited ({why}); sleeping {wait / 60:.0f}m "
                f"[wait {attempt + 1}/{max_waits}]",
                flush=True,
            )
            time.sleep(wait)
    raise RuntimeError("unreachable")


def summarize(rows: list[dict]) -> str:
    """p_B pooled and per repo, with unmeasurable and no-edit runs held out.

    Per repo is not optional: ruleset strength varies a lot (scikit-learn 7
    rules against requests' 22, and scikit-learn's is deliberately weak because
    its estimator families are not a documented hierarchy), so a shift in repo
    mix would otherwise read as an effect.
    """
    measurable = [r for r in rows if r.get("measurable")]
    dropped = len(rows) - len(measurable)
    no_edit = [r for r in measurable if not r.get("made_edit")]
    lines = [
        f"runs: {len(rows)}   measurable: {len(measurable)}   dropped (unmeasurable): {dropped}",
        f"no-edit runs (agent changed nothing): {len(no_edit)}",
    ]
    if not measurable:
        lines.append("\nNo measurable runs: p_B is undefined, not zero.")
        return "\n".join(lines)

    bad = [r for r in measurable if r["structurally_bad"]]
    p_b = len(bad) / len(measurable)
    lines.append(f"\np_B (pooled) = {len(bad)}/{len(measurable)} = {p_b:.1%}")
    cycles = sum(1 for r in measurable if r["cycle_regression"])
    layers = sum(1 for r in measurable if r["layer_violations_introduced"])
    lines.append(f"  by mechanism: cycle_regression {cycles}, layer violation {layers}")

    # A run that edited nothing cannot break structure, so leaving it in the
    # denominator drags p_B down for a reason that is about the agent giving up,
    # not about the agent damaging anything. Reported as a second rate rather
    # than substituted for the first: which denominator is right depends on
    # whether "the agent declined the task" counts as a trial, and that is a
    # judgment the results doc should make explicitly, not one this code should
    # make silently.
    edited = [r for r in measurable if r.get("made_edit")]
    if edited and len(edited) != len(measurable):
        bad_edited = sum(1 for r in edited if r["structurally_bad"])
        lines.append(
            f"p_B (edited runs only) = {bad_edited}/{len(edited)} = "
            f"{bad_edited / len(edited):.1%}   "
            f"[{len(measurable) - len(edited)} no-edit run(s) excluded]"
        )

    per_repo: dict[str, list[dict]] = defaultdict(list)
    for row in measurable:
        per_repo[row["repo"]].append(row)
    lines.append("\nper repo:")
    for repo in sorted(per_repo):
        group = per_repo[repo]
        hits = sum(1 for r in group if r["structurally_bad"])
        lines.append(f"  {repo:<28} {hits}/{len(group)} = {hits / len(group):.0%}")

    # A smoke run of one must not print a corpus verdict. At n<10 the 95% CI on
    # any p_B spans both thresholds, so reading either branch would be reading
    # noise, and this text is quotable enough to end up in a results doc.
    if len(measurable) < MIN_N_FOR_VERDICT:
        lines.append(
            f"\nToo few runs ({len(measurable)} < {MIN_N_FOR_VERDICT}) to read the "
            "pre-registered thresholds. No verdict."
        )
        return "\n".join(lines)

    lines.append("\nPre-registered reading (bench/q1b_tasks.py, do not revise now):")
    if p_b >= 0.25:
        lines.append("  p_B >= 25%: powered at ~80-130 pairs. Proceed to arm A.")
    elif p_b <= 0.10:
        lines.append(
            "  p_B <= 10%: THE CORPUS IS WRONG, NOT ARCHY. SWE-bench is bug fixes by\n"
            "  construction. Move to multi-file feature/refactor commits. This is NOT\n"
            "  evidence archy is unnecessary and must not be reported as such."
        )
    else:
        lines.append("  10% < p_B < 25%: between the thresholds. Neither branch is triggered.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=25, help="tasks from the top of the manifest")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--max-wall", type=float, default=1800.0, help="seconds per agent run")
    ap.add_argument(
        "--pause", type=float, default=60.0, help="seconds between runs, to pace usage limits"
    )
    ap.add_argument(
        "--limit-waits",
        type=int,
        default=6,
        help="how many times to wait out a usage limit before stopping (resumable)",
    )
    ap.add_argument(
        "--max-backoff", type=float, default=3600.0, help="ceiling on any single limit wait"
    )
    ap.add_argument("--dry-run", action="store_true", help="plan only; spends nothing")
    ap.add_argument("--report", action="store_true", help="summarize the ledger and exit")
    args = ap.parse_args()

    ledger = Ledger(LEDGER_PATH)
    manifest = json.loads((REPO_ROOT / "bench/q1b_tasks.json").read_text())
    tasks = manifest["tasks"][: args.limit]

    if args.report:
        rows = [ledger.get(f"{t['instance_id']}:B") for t in tasks]
        print(summarize([r for r in rows if r]))
        return 0

    if args.dry_run:
        print(f"# plan: {len(tasks)} tasks, arm B, model {args.model}")
        for task in tasks:
            name, package = REPOS[task["repo"]]
            key = f"{task['instance_id']}:B"
            if ledger.is_done(key):
                print(f"  done  {task['instance_id']}")
                continue
            tree = worktree_at(name, task["base_commit"])
            pkg = resolve_package(tree, package)
            before = layer_violations(tree, name)
            fires = sum(before.values()) if before else 0
            flag = "  " if pkg and not fires else "!!"
            print(f"  {flag} {task['instance_id']:<38} pkg={pkg}  rules_firing_at_base={fires}")
        print("\nNothing was spent. Drop --dry-run to run.")
        return 0

    # Fetched once, before anything is spent: a missing prompt must fail the
    # whole pilot up front, not one task at a time after paying for the others.
    statements = problem_statements(manifest["dataset"])
    missing = [t["instance_id"] for t in tasks if t["instance_id"] not in statements]
    if missing:
        print(f"no problem statement for {len(missing)} task(s): {missing[:3]}")
        return 1

    spent = 0
    for index, task in enumerate(tasks, 1):
        key = f"{task['instance_id']}:B"
        if ledger.is_done(key):
            print(f"[{index}/{len(tasks)}] skip {task['instance_id']} (done)", flush=True)
            continue
        # Pace between runs that actually spent something. Skipped tasks cost
        # nothing, so pausing for them would only add hours to a resume.
        if spent and args.pause:
            print(f"      pausing {args.pause:.0f}s before the next run", flush=True)
            time.sleep(args.pause)
        print(f"[{index}/{len(tasks)}] {task['instance_id']} ...", flush=True)
        spent += 1
        try:
            row = run_with_limit_backoff(
                task,
                statements[task["instance_id"]],
                model=args.model,
                max_wall=args.max_wall,
                max_waits=args.limit_waits,
                max_backoff=args.max_backoff,
            )
        except RateLimited as exc:
            # Out of budgeted waits. Recorded as `limited`, which `is_done` does
            # not count, so resuming later picks this task up untouched.
            ledger.record(
                key, {"instance_id": task["instance_id"], "error": str(exc)}, status="limited"
            )
            print(
                f"      still rate-limited after {args.limit_waits} waits; stopping.\n"
                f"      Re-run the same command later to continue from here.",
                flush=True,
            )
            break
        except (StallTimeout, WallTimeout) as exc:
            ledger.record(
                key, {"instance_id": task["instance_id"], "error": str(exc)}, status="stalled"
            )
            print(f"      gave up after retries: {exc}", flush=True)
            continue
        except KeyboardInterrupt:
            # Everything finished so far is already on disk; say how to resume
            # rather than dying silently mid-pilot.
            print(
                f"\ninterrupted after {ledger.completed} completed run(s). "
                f"Re-run the same command to continue; completed tasks are skipped.",
                flush=True,
            )
            return 130
        except Exception as exc:
            # A git failure, a scorer crash, a bad transcript: one task's
            # problem must not end a pilot that has already spent hours. The
            # row lands with status="error", which `is_done` does NOT count, so
            # a later resume retries exactly these.
            ledger.record(
                key,
                {"instance_id": task["instance_id"], "error": f"{type(exc).__name__}: {exc}"},
                status="error",
            )
            print(f"      crashed: {type(exc).__name__}: {exc}", flush=True)
            continue
        if "error" in row:
            ledger.record(key, {"instance_id": task["instance_id"], **row}, status="error")
            print(f"      error: {row['error']}", flush=True)
            continue
        ledger.record(key, row)
        print(
            f"      structurally_bad={row['structurally_bad']} "
            f"cycle={row['cycle_regression']} layers={row['layer_violations_introduced']} "
            f"files={row['files_changed']} {row['wall_seconds']}s",
            flush=True,
        )

    rows = [ledger.get(f"{t['instance_id']}:B") for t in tasks]
    print("\n" + summarize([r for r in rows if r]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
