#!/usr/bin/env python
"""Evaluate a generated backend the way the Constraint Decay paper does (#369).

    uv run python bench/greenfield_eval.py --tree path/to/generated --host http://localhost:8000
    uv run python bench/greenfield_eval.py --tree path/to/generated --structural-only
    uv run python bench/greenfield_eval.py --fetch-suite

This is the MEASUREMENT half of #369, built and validated before any agent time
is spent, per `AGENTS.md`. The generation half comes after this is trusted,
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

archy:owns        BehavioralVerdict, StructuralVerdict, assert_pass_rate,
                  behavioral_verdict, container_host, count_asserts, fetch_suite, main,
                  nesting_tolerant_config, structural_verdict
archy:mirrored-by StructuralVerdict also in bench.q1b_score
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from archy.graph import DEFAULT_IGNORED_DIRS, build_graph  # noqa: E402
from archy.layers import (  # noqa: E402
    LayerConfig,
    LayerSpec,
    compute_coverage,
    find_violations,
    load_config,
)

CONDUIT_CONFIG = REPO_ROOT / "bench/fixtures/conduit_clean/archy.yaml"
SUITE_DIR = REPO_ROOT / "bench/cache/realworld_hurl"
SUITE_RAW = "https://api.github.com/repos/gothinkster/realworld/contents/specs/api/hurl"

# Official image, DIGEST-pinned. The first draft used `:latest` while claiming
# to pin, which for a measurement instrument is worse than not claiming it: the
# behavioral oracle could change between arm A and arm B and move the result
# without anything in the study recording that it had. Resolved 2026-07-26,
# hurl 7.1.0. Re-pin deliberately, and note it in the results, never silently.
HURL_IMAGE = (
    "ghcr.io/orange-opensource/hurl"
    "@sha256:d7727dcc0166de8aea88916e73ea435ee09bfecb8ba0c281200206b6cf37cf64"
)
HURL_VERSION = "7.1.0"

# THE BEHAVIORAL SCORE IS ASSERT-LEVEL, AND THE DENOMINATOR IS PINNED.
#
# Measured 2026-07-26 against a live server, which is the only way either of
# these was going to surface. Two facts forced this shape:
#
#   - `succeeded files` cannot discriminate. The suite is 13 all-or-nothing
#     files, every one gated on registration, so one wrong status code zeroes
#     the lot. A near-conforming backend and a stub that 501s everything BOTH
#     scored 0.000. A metric that cannot separate those cannot support the
#     behavioral guardrail, and an unfirable guardrail is worse than none: the
#     prereg lets it VOID a structural win.
#   - `passed / executed` rewards failing early. hurl abandons a file at its
#     first failure, so a worse server executes fewer asserts and can post a
#     HIGHER ratio. `--continue-on-error` shrinks that (241 asserts vs 67 on the
#     same server) but does not remove it: the stub still executed only 169.
#
# So the denominator is `max(pinned, executed)`. A run that conforms better than
# the calibration is not capped, and one that dies early cannot inflate. The pin
# is a FLOOR taken from the best reference available, not a claim about the
# suite's true total; re-derive it with `--calibrate <host>` and change it
# deliberately, noting it in the results, exactly as with the image digest.
#
# Provenance, and it has been re-pinned once already. The first floor was 241,
# from borys25ol/fastapi-realworld-backend (2026-07-26, with its `POST
# /api/users` status corrected to the spec's 201). The arm-A smoke generation
# then executed 687, which makes 241 far too low: every run between the two
# would have been scored over its own smaller denominator and read as better
# than it was. Re-pinned to 687 from that run, and this is a FLOOR, not a claim
# about the suite's true total. Re-derive with `--calibrate <host>`.
ASSERT_DENOMINATOR = 687
ASSERT_DENOMINATOR_SOURCE = "arm-A smoke generation, 2026-07-26 (615/687 passed)"

# `hurl --test` summary lines, e.g. "Executed files:  13" / "Succeeded files:  11".
SUMMARY = re.compile(r"^(?P<label>[A-Za-z ]+):\s+(?P<count>\d+)", re.MULTILINE)


class StructuralVerdict(BaseModel):
    """The paper's architecture verdict for one tree.

    Frozen pydantic with tuple fields, matching `bench/q1b_score.py`'s
    `StructuralVerdict`, whose `measurable` flag is the same idea as `evaluable`
    here: a tree that could not be analysed is neither compliant nor
    non-compliant, and the type makes that a third state rather than a comment.
    """

    model_config = ConfigDict(frozen=True)

    evaluable: bool = True
    reason: str | None = None
    layers_present: int = 0
    layers_required: int = 0
    presence_ok: bool = False
    dependency_violations: tuple[str, ...] = ()
    empty_layers: tuple[str, ...] = ()
    compliant: bool = False


class BehavioralVerdict(BaseModel):
    """One run of the RealWorld suite against a live server."""

    model_config = ConfigDict(frozen=True)

    evaluable: bool = True
    reason: str | None = None
    #: Whole-file pass counts. Kept because they are the suite's own headline,
    #: but NOT the score: see `pass_rate`.
    files_executed: int | None = None
    files_succeeded: int | None = None
    #: The score. Assert-level, over a denominator that does not shrink when the
    #: server fails early. Raw counts are recorded beside it so the pinned
    #: denominator stays auditable rather than being an invisible constant.
    pass_rate: float | None = None
    asserts_passed: int | None = None
    asserts_executed: int | None = None
    asserts_denominator: int | None = None
    exit_code: int | None = None
    uid: str | None = None
    hurl_version: str | None = None
    tail: str | None = None


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


def nesting_tolerant_config(config: LayerConfig, modules: Sequence[str]) -> LayerConfig:
    """Expand each layer alias to the package prefixes it actually appears under.

    THE PAPER CHECKS FOR A DIRECTORY; ARCHY MATCHES A DOTTED NAME. A tree whose
    root is a package (`conduit/__init__.py`, `app/`, `src/app/`) yields modules
    like `conduit.services.user`, and the alias `services.**` does not match it.
    Left alone, a correctly-layered generation reports 0 of 4 layers and scores
    NON-COMPLIANT, in both arms, with any arm-difference in nesting habit
    reading as an architecture effect.

    The nested pattern cannot be written in the config: archy rejects a leading
    `**` at load, deliberately (the contracts fallback derives the root package
    from the first segment). So the prefixes are discovered from the tree under
    test and the valid `<prefix>.<alias>.**` form is generated per prefix.

    Alias matching is on whole dotted segments, so `myservices.x` does not count
    as a `services` directory.
    """
    expanded: list[LayerSpec] = []
    for layer in config.layers:
        patterns: list[str] = list(layer.patterns)
        for pattern in layer.patterns:
            alias = pattern.split(".", 1)[0]
            for module in modules:
                segments = module.split(".")
                if alias not in segments[1:]:
                    # segments[0] is already covered by the unnested `alias.**`.
                    continue
                prefix = ".".join(segments[: segments.index(alias)])
                candidate = f"{prefix}.{alias}.**"
                if candidate not in patterns:
                    patterns.append(candidate)
        expanded.append(layer.model_copy(update={"patterns": tuple(patterns)}))
    return config.model_copy(update={"layers": tuple(expanded)})


def structural_verdict(tree: Path) -> StructuralVerdict:
    """The paper's architecture verifier, as archy expresses it.

    Both halves, which archy could only half-state until #123: dependency
    direction has always been `find_violations`, and layer presence arrived as
    `min_layers_present`.
    """
    if not tree.is_dir():
        return StructuralVerdict(evaluable=False, reason=f"no such directory: {tree}")
    if not any(tree.rglob("*.py")):
        # No Python at all is a FAILED GENERATION, not a structural verdict. An
        # agent that emitted nothing and an agent that emitted one large module
        # both report zero layers present, and only the second is a fact about
        # architecture. Counting the first as non-compliant would inflate the
        # rate with runs that never produced code.
        return StructuralVerdict(evaluable=False, reason="no .py files under the tree")

    config = load_config(CONDUIT_CONFIG)
    try:
        graph = build_graph(tree, ignored_dirs=DEFAULT_IGNORED_DIRS | set(config.exclude))
    except Exception as exc:
        return StructuralVerdict(evaluable=False, reason=f"{type(exc).__name__}: {exc}")

    # `build_graph` returns a networkx DiGraph whose nodes are module qualnames.
    # An empty graph is a real state, not a failure: the degenerate single-module
    # solution the paper describes yields one, and it must read as "0 layers
    # present" rather than raising.
    config = nesting_tolerant_config(config, sorted(graph.nodes))
    violations = find_violations(graph, config)
    coverage = compute_coverage(graph, config)
    floor = config.min_layers_present or 0
    presence_ok = coverage.layers_present >= floor
    return StructuralVerdict(
        layers_present=coverage.layers_present,
        layers_required=floor,
        presence_ok=presence_ok,
        dependency_violations=tuple(f"{v.source} -> {v.target}" for v in violations),
        # The paper: "compliant if and only if both checks pass".
        compliant=presence_ok and not violations,
        empty_layers=coverage.empty_layers,
    )


def _server_responds(host: str, timeout: float = 5.0) -> bool:
    """Is anything listening, as seen from *this* process? Any HTTP status
    counts, including 404 and 500.

    The question is "did the server come up", not "is it correct", so only a
    transport-level failure counts as absent. Note this answers the question
    from the host's network, which is NOT where the suite runs; see
    `_suite_can_reach`.
    """
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # it answered, just not with a 200
    except (urllib.error.URLError, OSError):
        return False


def container_host(host: str) -> str:
    """Rewrite a host-local URL into one a container can resolve.

    `localhost` inside a container is the container, so the suite would test
    itself and find nothing. Docker publishes the host as
    `host.docker.internal`, which `--add-host=...:host-gateway` guarantees on
    every platform rather than only where the daemon happens to inject it.
    """
    parsed = urllib.parse.urlsplit(host.rstrip("/"))
    if parsed.hostname not in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return host.rstrip("/")
    netloc = "host.docker.internal" + (f":{parsed.port}" if parsed.port else "")
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def count_asserts(report: object) -> tuple[int, int]:
    """(passed, executed) asserts across a hurl `--report-json` report.

    Assert-level rather than file-level because the paper's own oracle is
    assertion-level (291 of them) and because the file counts cannot separate a
    near-conforming backend from a stub; see ASSERT_DENOMINATOR.
    """
    passed = executed = 0
    for entry_file in report if isinstance(report, list) else []:
        for entry in entry_file.get("entries", []):
            for assertion in entry.get("asserts", []):
                executed += 1
                passed += 1 if assertion.get("success") else 0
    return passed, executed


def assert_pass_rate(passed: int, executed: int) -> float:
    """Score over `max(pinned, executed)`, so failing early cannot pay.

    Using `executed` alone would let a server that dies on request 1 of each
    file post a high ratio off a handful of asserts. Using `pinned` alone would
    cap a run that conforms better than the calibration did.
    """
    return round(passed / max(ASSERT_DENOMINATOR, executed), 4)


def _hurl_cmd(
    mount: Path, host: str, uid: str, glob: str, report_dir: Path | None = None
) -> list[str]:
    """The one place the container invocation is spelled out.

    `--add-host` rather than `--network host`: Docker Desktop on macOS accepts
    `--network host` and then cannot reach the host at all, which is the worst
    available behaviour because the suite still *runs* and reports 0 of 13. A
    correct server measured that way is indistinguishable from one that answers
    every request wrongly, and both arms would have scored a behavioral zero.
    Verified 2026-07-26 on Docker Desktop 28.1.1: `--network host` gets
    connection-refused, `--add-host=host.docker.internal:host-gateway` gets 200.
    """
    cmd = [
        "docker",
        "run",
        "--rm",
        "--add-host=host.docker.internal:host-gateway",
        "-v",
        f"{mount}:/suite:ro",
    ]
    if report_dir is not None:
        cmd += ["-v", f"{report_dir}:/out"]
    cmd += [
        HURL_IMAGE,
        "--test",
        # Without this hurl abandons a file at its first failure, so the number
        # of asserts executed shrinks as the server gets worse. The denominator
        # must not be a function of the thing being measured.
        "--continue-on-error",
        "--variable",
        f"host={container_host(host)}",
        "--variable",
        f"uid={uid}",
    ]
    if report_dir is not None:
        cmd += ["--report-json", "/out/json"]
    return [*cmd, "--glob", glob]


def _suite_can_reach(host: str, timeout: float = 60.0) -> bool:
    """Probe from inside the container, where the suite actually runs.

    `_server_responds` asks the host's network stack, which answers yes for a
    server the container cannot see. That gap is not hypothetical: it is exactly
    the defect this probe was added to close, and it produced `evaluable=true,
    pass_rate=0.0` against a server that was up and answering.

    Uses the same image, flags and variable as the real run, so it tests the
    path the suite will take rather than an approximation of it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "probe.hurl").write_text("GET {{host}}/api/tags\nHTTP *\n", encoding="utf-8")
        try:
            proc = subprocess.run(
                _hurl_cmd(Path(tmp), host, "probe", "/suite/probe.hurl"),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False
    return proc.returncode == 0


def behavioral_verdict(host: str, timeout: float) -> BehavioralVerdict:
    """Run the RealWorld Hurl suite against a live server.

    Runs in Docker so the hurl version is pinned and no local install is needed.
    The server under test is on the host, so the container reaches it through
    `host.docker.internal`; see `_hurl_cmd` for why not `--network host`.

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
        return BehavioralVerdict(evaluable=False, reason="suite missing; run --fetch-suite")
    if shutil.which("docker") is None:
        return BehavioralVerdict(evaluable=False, reason="docker not available")
    if not _server_responds(host):
        # NOT a pass rate of zero. A server that never started and a server that
        # answers every request wrongly both make the suite report 0 of 13, and
        # they are different facts: the first is a generation or startup failure,
        # the second is a behavioral result. Folding them together would let an
        # arm that produced code which does not run masquerade as an arm whose
        # code runs badly, which is the same "unmeasurable is not clean" trap
        # #356 hit from the other direction.
        return BehavioralVerdict(evaluable=False, reason=f"nothing listening at {host}")
    if not _suite_can_reach(host):
        # The server is up and the container cannot see it. That is a fact about
        # this machine's docker networking, not about the generated code, so it
        # must never reach the ledger as a score of any kind.
        return BehavioralVerdict(
            evaluable=False,
            reason=f"server is up at {host} but the suite container cannot reach it",
        )

    with tempfile.TemporaryDirectory() as out:
        try:
            proc = subprocess.run(
                _hurl_cmd(SUITE_DIR, host, uid, "/suite/*.hurl", report_dir=Path(out)),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return BehavioralVerdict(evaluable=False, reason=f"suite exceeded {timeout}s")
        report_path = Path(out) / "json/report.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else None

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
        return BehavioralVerdict(
            evaluable=False, reason="no hurl summary in output", tail=blob[-400:]
        )
    if report is None:
        # The score is assert-level, so no report means no score. The file counts
        # survive in the summary, but reporting them AS the rate would silently
        # substitute the metric this run exists to avoid.
        return BehavioralVerdict(
            evaluable=False, reason="hurl produced no report.json", tail=blob[-400:]
        )
    passed, asserts_executed = count_asserts(report)
    return BehavioralVerdict(
        files_executed=executed,
        files_succeeded=succeeded,
        pass_rate=assert_pass_rate(passed, asserts_executed),
        asserts_passed=passed,
        asserts_executed=asserts_executed,
        asserts_denominator=max(ASSERT_DENOMINATOR, asserts_executed),
        exit_code=proc.returncode,
        uid=uid,
        # Recorded per run so a later re-pin is visible in the data rather than
        # being an invisible difference between two batches.
        hurl_version=HURL_VERSION,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tree", type=Path, help="directory holding the generated backend")
    ap.add_argument(
        "--host",
        help="base URL of the running server WITHOUT /api, e.g. http://localhost:8000",
    )
    ap.add_argument("--structural-only", action="store_true")
    ap.add_argument("--fetch-suite", action="store_true", help="download the RealWorld Hurl suite")
    ap.add_argument(
        "--calibrate",
        action="store_true",
        help="report the assert total for --host, to re-derive ASSERT_DENOMINATOR",
    )
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    if args.fetch_suite:
        return fetch_suite()
    if args.calibrate:
        if not args.host:
            ap.error("--calibrate needs --host")
        verdict = behavioral_verdict(args.host, args.timeout)
        print(json.dumps(verdict.model_dump(), indent=2, sort_keys=True))
        if not verdict.evaluable:
            return 1
        executed = verdict.asserts_executed or 0
        print(
            f"\n# ASSERT_DENOMINATOR is {ASSERT_DENOMINATOR} "
            f"(from {ASSERT_DENOMINATOR_SOURCE}); this host executed {executed}."
        )
        if executed > ASSERT_DENOMINATOR:
            print("# This host is a better floor. Re-pin deliberately and say so in the results.")
        return 0
    if args.tree is None:
        ap.print_help()
        return 1

    structural = structural_verdict(args.tree)
    result: dict = {"tree": str(args.tree), "structural": structural.model_dump()}
    if not args.structural_only:
        if not args.host:
            ap.error("--host is required unless --structural-only")
        result["behavioral"] = behavioral_verdict(args.host, args.timeout).model_dump()

    print(json.dumps(result, indent=2, sort_keys=True))
    # Exit 1 only on structural non-compliance, mirroring `archy check`. The
    # behavioral score is data, not a gate: this tool reports, the study decides.
    return 0 if structural.compliant else 1


if __name__ == "__main__":
    raise SystemExit(main())
