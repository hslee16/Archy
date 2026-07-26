#!/usr/bin/env python
"""Evaluate a generated backend the way the Constraint Decay paper does (#369).

    uv run python bench/greenfield_eval.py --tree path/to/generated --host http://localhost:8000
    uv run python bench/greenfield_eval.py --tree path/to/generated --structural-only
    uv run python bench/greenfield_eval.py --fetch-suite

This is the MEASUREMENT half of #369, built and validated before any agent time
is spent, per `CLAUDE.md`. The generation half comes after this is trusted,
because every harness bug found during a paid run is a run wasted (#356 lost
eleven that way).

## What it computes

The paper (arxiv:2605.06445) scores a solution on two orthogonal axes, and
zeroes the behavioral score of any structurally non-compliant run. Both are
reproduced here:

**Structural**, from its Appendix C.1, which is exactly archy's two checks:

- *dependency direction*: a file in a lower-rank layer importing a higher-rank
  one. This is `find_violations` against `bench/fixtures/conduit_clean/archy.yaml`.
- *layer presence*: at least 3 of the 4 canonical layers present. This is
  `min_layers_present`, shipped for this purpose in #123.

**Behavioral**, from its Appendix G: the paper used a Postman collection run via
Newman. That collection is not published. The RealWorld project itself ships a
Hurl suite against the same OpenAPI contract, so this uses that instead.

## Why substituting the suite is legitimate here, and what it costs

#369 runs BOTH arms itself, so the comparison is against a control executed
under identical conditions, not against the paper's table. What is lost is the
ability to say "we reproduced their L3 and improved on it"; absolute pass rates
are not comparable to their published figures. What survives is the claim the
ticket actually asks for: on the same contract and the same constraint prompt,
does archy-in-the-loop move structural compliance, and at what cost to behavior.

Report both axes always. The paper zeroes behavior on structural failure, which
means a naive reading rewards a compliant-but-broken server, and that is the
easiest way for this experiment to produce a flattering lie.
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
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from archy.graph import DEFAULT_IGNORED_DIRS, build_graph  # noqa: E402
from archy.layers import compute_coverage, find_violations, load_config  # noqa: E402

CONDUIT_CONFIG = REPO_ROOT / "bench/fixtures/conduit_clean/archy.yaml"
SUITE_DIR = REPO_ROOT / "bench/cache/realworld_hurl"
SUITE_RAW = "https://api.github.com/repos/gothinkster/realworld/contents/specs/api/hurl"

# Official image, so the suite runs without a local hurl install and the version
# is pinned in one place rather than varying per machine.
HURL_IMAGE = "ghcr.io/orange-opensource/hurl:latest"

# `hurl --test` summary lines, e.g. "Executed files:  13" / "Succeeded files:  11".
SUMMARY = re.compile(r"^(?P<label>[A-Za-z ]+):\s+(?P<count>\d+)", re.MULTILINE)


def fetch_suite() -> int:
    """Download the RealWorld Hurl suite into the gitignored bench cache.

    Not vendored into the repo: it is someone else's test suite under their
    licence, and pinning a copy here would silently diverge from the contract it
    is supposed to track.
    """
    SUITE_DIR.mkdir(parents=True, exist_ok=True)
    listing = subprocess.run(
        ["gh", "api", SUITE_RAW], capture_output=True, text=True, check=False
    ).stdout
    try:
        entries = json.loads(listing)
    except json.JSONDecodeError:
        print("could not list the suite; is `gh` authenticated?")
        return 1
    fetched = 0
    for entry in entries:
        if not entry.get("name", "").endswith(".hurl"):
            continue
        body = subprocess.run(
            ["curl", "-sSL", entry["download_url"]], capture_output=True, text=True, check=False
        ).stdout
        (SUITE_DIR / entry["name"]).write_text(body, encoding="utf-8")
        fetched += 1
    print(f"# {fetched} .hurl files -> {SUITE_DIR}")
    return 0 if fetched else 1


def structural_verdict(tree: Path) -> dict:
    """The paper's architecture verifier, as archy expresses it.

    Both halves, which archy could only half-state until #123: dependency
    direction has always been `find_violations`, and layer presence arrived as
    `min_layers_present`.
    """
    if not tree.is_dir():
        return {"evaluable": False, "reason": f"no such directory: {tree}"}
    if not any(tree.rglob("*.py")):
        # No Python at all is a FAILED GENERATION, not a structural verdict. An
        # agent that emitted nothing and an agent that emitted one large module
        # both report zero layers present, and only the second is a fact about
        # architecture. Counting the first as non-compliant would inflate the
        # rate with runs that never produced code.
        return {"evaluable": False, "reason": "no .py files under the tree"}

    config = load_config(CONDUIT_CONFIG)
    try:
        graph = build_graph(tree, ignored_dirs=DEFAULT_IGNORED_DIRS | set(config.exclude))
    except Exception as exc:
        return {"evaluable": False, "reason": f"{type(exc).__name__}: {exc}"}

    violations = find_violations(graph, config)
    coverage = compute_coverage(graph, config)
    floor = config.min_layers_present or 0
    presence_ok = coverage.layers_present >= floor
    return {
        "evaluable": True,
        "layers_present": coverage.layers_present,
        "layers_required": floor,
        "presence_ok": presence_ok,
        "dependency_violations": [f"{v.source} -> {v.target}" for v in violations],
        # The paper: "compliant if and only if both checks pass".
        "compliant": presence_ok and not violations,
        "empty_layers": list(coverage.empty_layers),
    }


def _server_responds(host: str, timeout: float = 5.0) -> bool:
    """Is anything listening? Any HTTP status counts, including 404 and 500.

    The question is "did the server come up", not "is it correct", so only a
    transport-level failure counts as absent.
    """
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # it answered, just not with a 200
    except (urllib.error.URLError, OSError):
        return False


def behavioral_verdict(host: str, timeout: float) -> dict:
    """Run the RealWorld Hurl suite against a live server.

    Runs in Docker so the hurl version is pinned and no local install is needed.
    `--network host` because the server under test is on the host, not in the
    container; that is a real portability limit and it is stated rather than
    hidden (Docker Desktop on macOS honours it for this purpose).

    TWO VARIABLES, both taken from the project's own `run-api-tests-hurl.sh`
    rather than guessed:

    - `host` is the base URL WITHOUT `/api`. The suite appends it (`POST
      {{host}}/api/users`), so passing `http://localhost:8000/api` yields
      `/api/api/users` and every request 404s, which reads exactly like a
      broken server.
    - `uid` seeds the usernames and emails the suite registers. Without it the
      literal string `{{uid}}` is used, so a second run collides with the first
      run's account and fails for a reason that has nothing to do with the
      code under test.
    """
    uid = f"{int(time.time())}{os.getpid()}"
    if not SUITE_DIR.exists() or not any(SUITE_DIR.glob("*.hurl")):
        return {"evaluable": False, "reason": "suite missing; run --fetch-suite"}
    if shutil.which("docker") is None:
        return {"evaluable": False, "reason": "docker not available"}
    if not _server_responds(host):
        # NOT a pass rate of zero. A server that never started and a server that
        # answers every request wrongly both make the suite report 0 of 13, and
        # they are different facts: the first is a generation or startup failure,
        # the second is a behavioral result. Folding them together would let an
        # arm that produced code which does not run masquerade as an arm whose
        # code runs badly, which is the same "unmeasurable is not clean" trap
        # #356 hit from the other direction.
        return {"evaluable": False, "reason": f"nothing listening at {host}"}

    try:
        proc = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "host",
                "-v",
                f"{SUITE_DIR}:/suite:ro",
                HURL_IMAGE,
                "--test",
                "--variable",
                f"host={host.rstrip('/')}",
                "--variable",
                f"uid={uid}",
                "--glob",
                "/suite/*.hurl",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"evaluable": False, "reason": f"suite exceeded {timeout}s"}

    blob = f"{proc.stdout}\n{proc.stderr}"
    counts = {
        m.group("label").strip().lower(): int(m.group("count")) for m in SUMMARY.finditer(blob)
    }
    executed = counts.get("executed files")
    succeeded = counts.get("succeeded files")
    if executed is None or succeeded is None:
        # No summary means the suite did not run, which is NOT a score of zero.
        # Scoring an unrunnable suite as total failure would make a server that
        # never started look identical to one that answered every request wrong.
        return {"evaluable": False, "reason": "no hurl summary in output", "tail": blob[-400:]}
    return {
        "evaluable": True,
        "files_executed": executed,
        "files_succeeded": succeeded,
        "pass_rate": round(succeeded / executed, 4) if executed else 0.0,
        "exit_code": proc.returncode,
        "uid": uid,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tree", type=Path, help="directory holding the generated backend")
    ap.add_argument(
        "--host",
        help="base URL of the running server WITHOUT /api, e.g. http://localhost:8000",
    )
    ap.add_argument("--structural-only", action="store_true")
    ap.add_argument("--fetch-suite", action="store_true", help="download the RealWorld Hurl suite")
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    if args.fetch_suite:
        return fetch_suite()
    if args.tree is None:
        ap.print_help()
        return 1

    result: dict = {"tree": str(args.tree), "structural": structural_verdict(args.tree)}
    if not args.structural_only:
        if not args.host:
            ap.error("--host is required unless --structural-only")
        result["behavioral"] = behavioral_verdict(args.host, args.timeout)

    print(json.dumps(result, indent=2, sort_keys=True))
    # Exit 1 only on structural non-compliance, mirroring `archy check`. The
    # behavioral score is data, not a gate: this tool reports, the study decides.
    return 0 if result["structural"].get("compliant") else 1


if __name__ == "__main__":
    raise SystemExit(main())
