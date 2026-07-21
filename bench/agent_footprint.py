"""Agent-footprint minimal-pair bench (#259).

Runs a coding agent (Claude Code, headless) on before/after variants of a repo
and measures its token footprint and file revisitation on a fixed task. See
`docs/SPEC_AGENT_FOOTPRINT_BENCH.md` for the full protocol, metric definitions,
and the anti-theater guardrails this harness must honor.

Two layers, deliberately split:

* `parse_transcript` is the **deterministic core**: a persisted Claude Code
  session `.jsonl` transcript -> a `ParsedTranscript` of token sums and
  file-touch metrics. It invokes no agent and is unit-tested in
  `tests/test_agent_footprint.py` against a synthetic fixture.
* `run_variant` / `run_pair` are the **live runner**: they invoke `claude -p`
  headless, copy the persisted transcript, and run the repo's test suite as the
  regression gate the paper lacks. They need a working `claude` CLI (any auth:
  an API key or a subscription login, so a logged-in Claude Code session runs
  them with no key) and real agent time, so they run only when this file is
  executed directly, never in CI.

Sweep scripts are run as `python bench/agent_footprint.py`, so `bench/` is on
`sys.path[0]`; keep imports self-contained.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# Tool names that name a single target file (their `file_path` input is the
# touched path). Grep/Glob/Bash are excluded: they have no single unambiguous
# target, so they do not count as a file touch (spec section 5).
_READ_TOOLS = frozenset({"Read"})
_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_FILE_TOOLS = _READ_TOOLS | _WRITE_TOOLS
# Exploratory-read surface for the #289 reads-before-first-edit metric: the tool
# calls an agent makes to *find* what to look at (spec section 14.3). Grep/Glob
# have no single file target so they are not file touches, but they ARE reads for
# this metric. Bash is excluded: it is not unambiguously a source read.
_SEARCH_TOOLS = frozenset({"Grep", "Glob"})
_EXPLORE_TOOLS = _READ_TOOLS | _SEARCH_TOOLS

# Default tool surface handed to the agent; pinned so the two variants see an
# identical, reproducible tool set (spec section 10).
DEFAULT_ALLOWED_TOOLS = ("Read", "Write", "Edit", "Bash", "Grep", "Glob")


def _footprint_tokens(input_tokens: int, output_tokens: int) -> int:
    """Headline token footprint: non-cache input + output (spec section 5).

    Shared by both value types so the definition lives in one place.
    """
    return input_tokens + output_tokens


def _normalize_touch_path(raw: str) -> str:
    """Lexically normalize a tool's file_path so equivalent paths collapse.

    `os.path.normpath` (not `pathlib`) is deliberate: it is a pure-string
    normalizer, whereas pathlib has no lexical equivalent and `Path.resolve()`
    would touch the filesystem -- wrong here, since we parse a transcript whose
    repo may not be checked out on this machine.
    """
    return os.path.normpath(raw)


class ParsedTranscript(NamedTuple):
    """Everything derivable from a session transcript alone (no run config).

    Token fields are summed across every assistant message: each assistant
    message carries the usage for *its own* generation, so the sum is the total
    the run consumed. `cache_read_input_tokens` is reported separately and never
    folded into the footprint headline, because cache hit/miss is a cross-run
    artifact, not agent effort (spec section 4).
    """

    input_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    output_tokens: int
    assistant_messages: int
    distinct_files_touched: int
    file_revisitations: int
    # #289 reads-before-first-edit (arm C metric of record, spec section 14.3),
    # measured over the transcript prefix up to the first Edit/Write.
    pre_edit_reads: int
    pre_edit_distinct_files: int
    pre_edit_input_tokens: int
    made_edit: bool  # False -> the whole run is the prefix; report separately.

    @property
    def footprint_tokens(self) -> int:
        return _footprint_tokens(self.input_tokens, self.output_tokens)


class FootprintRecord(NamedTuple):
    """One agent run's full footprint row: parsed transcript + run context."""

    variant: str
    run_index: int
    model: str
    input_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    output_tokens: int
    num_turns: int
    distinct_files_touched: int
    file_revisitations: int
    pre_edit_reads: int
    pre_edit_distinct_files: int
    pre_edit_input_tokens: int
    made_edit: bool
    brief_tokens: int  # realized archy-brief size charged to this arm (0 for A)
    duration_ms: int
    total_cost_usd: float
    task_completed: bool
    test_regression: bool

    @property
    def footprint_tokens(self) -> int:
        return _footprint_tokens(self.input_tokens, self.output_tokens)


def _iter_tool_uses(message: dict):
    """Yield (tool_name, file_path|None) for each tool_use block, in order."""
    for block in message.get("content", []):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name", "")
        file_path = None
        if name in _FILE_TOOLS:
            raw = (block.get("input") or {}).get("file_path")
            if raw:
                file_path = _normalize_touch_path(str(raw))
        yield name, file_path


def parse_transcript(path: str | Path) -> ParsedTranscript:
    """Parse a persisted Claude Code session `.jsonl` into footprint metrics.

    Deterministic and agent-free: this is the unit-test boundary. Token sums
    come from the `usage` on assistant messages; file metrics come from their
    `tool_use` blocks, walked in transcript order so revisitation is causal.

    Revisitation (the paper's "returns to files it has already edited", spec
    section 5): maintain the set of paths already written (`Edit`/`Write`);
    every subsequent `Read`/`Edit` of a path already in that set is one
    revisitation. The first write of a file is not a revisit; a read *before*
    any write is not a revisit.
    """
    path = Path(path)

    # Claude Code writes streaming partials of the same assistant message: the
    # same `message.id` appears on several lines with identical usage, and the
    # tool_use blocks land on the final, most-complete copy. Dedupe by id
    # (last-wins), preserving first-seen order, so usage is counted once and
    # tool calls are read once. Verified against a live transcript where summing
    # raw lines triple-counted tokens (7 lines for 3 real turns).
    deduped: dict[str, dict] = {}
    order: list[str] = []
    unkeyed = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant":
                continue
            message = entry.get("message", {})
            mid = message.get("id")
            if not mid:
                # No id to dedupe on: treat each occurrence as its own message.
                mid = f"_noid_{unkeyed}"
                unkeyed += 1
            if mid not in deduped:
                order.append(mid)
            deduped[mid] = message  # last copy wins (most complete)

    in_toks = cache_read = cache_create = out_toks = 0
    touched: set[str] = set()
    written: set[str] = set()
    revisitations = 0
    # #289 pre-edit accumulators: everything before the first Edit/Write, walked
    # in the same causal order (spec section 14.3). `made_edit` flips at the first
    # write block; `pre_edit_input_tokens` includes the turn that write lands in
    # ("up to the turn containing the first edit"), so this message's input tokens
    # must be added *before* its blocks are scanned.
    pre_edit_reads = 0
    pre_edit_files: set[str] = set()
    pre_edit_input = 0
    made_edit = False
    for mid in order:
        message = deduped[mid]
        usage = message.get("usage") or {}
        msg_input = int(usage.get("input_tokens", 0) or 0)
        in_toks += msg_input
        cache_read += int(usage.get("cache_read_input_tokens", 0) or 0)
        cache_create += int(usage.get("cache_creation_input_tokens", 0) or 0)
        out_toks += int(usage.get("output_tokens", 0) or 0)
        if not made_edit:
            pre_edit_input += msg_input

        for name, file_path in _iter_tool_uses(message):
            if not made_edit:
                if name in _WRITE_TOOLS:
                    # The confident point: stop counting exploratory reads. The
                    # write's own file touch is not a "read", so it is excluded.
                    made_edit = True
                elif name in _EXPLORE_TOOLS:
                    pre_edit_reads += 1
                    if name in _READ_TOOLS and file_path is not None:
                        pre_edit_files.add(file_path)
            # file_path is non-None only for a file tool (Read/Edit/Write), so
            # any such touch of an already-written path is a revisit.
            if file_path is None:
                continue
            touched.add(file_path)
            if file_path in written:
                revisitations += 1
            if name in _WRITE_TOOLS:
                written.add(file_path)

    return ParsedTranscript(
        input_tokens=in_toks,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_create,
        output_tokens=out_toks,
        assistant_messages=len(order),
        distinct_files_touched=len(touched),
        file_revisitations=revisitations,
        pre_edit_reads=pre_edit_reads,
        pre_edit_distinct_files=len(pre_edit_files),
        pre_edit_input_tokens=pre_edit_input,
        made_edit=made_edit,
    )


# --- live runner (needs a working `claude` CLI; never invoked in CI) ---------


def _project_slug(repo_dir: Path) -> str:
    """Claude Code's on-disk project dir name for a working directory.

    Claude Code derives the `~/.claude/projects/<slug>/` dir from the resolved
    absolute path with *every* non-alphanumeric character collapsed to '-', not
    just '/': e.g. `/tmp/af_smoke` -> `-private-tmp-af-smoke` (the `_` becomes
    `-` too, and `/tmp` resolves to `/private/tmp`). Verified against a live
    headless run; the '/'-only version silently missed the transcript.
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", str(repo_dir.resolve()))


def _locate_transcript(repo_dir: Path, session_id: str) -> Path:
    """Path to the persisted session transcript for a headless run."""
    return Path.home() / ".claude" / "projects" / _project_slug(repo_dir) / f"{session_id}.jsonl"


def _run_claude(repo_dir: Path, task_prompt: str, *, model: str, allowed_tools) -> dict:
    """Invoke `claude -p` headless in `repo_dir`; return the parsed JSON result.

    Isolation comes from `--setting-sources local`: only the project's own
    settings load, not the user/global CLAUDE.md / hooks / MCP, so the repo
    under test is the variable. (`--bare` would isolate more but breaks `-p`
    headless execution in a nested/sandboxed context, verified by a live run.)
    `claude` authenticates however it is configured: an ANTHROPIC_API_KEY or a
    subscription login, so no key is required inside a logged-in session.
    """
    cmd = [
        "claude",
        "-p",
        task_prompt,
        "--output-format",
        "json",
        "--model",
        model,
        "--dangerously-skip-permissions",
        "--allowedTools",
        ",".join(allowed_tools),
        "--setting-sources",
        "local",
    ]
    proc = subprocess.run(
        cmd, cwd=repo_dir, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")
    return json.loads(proc.stdout)


def _run_test_gate(repo_dir: Path, test_cmd: list[str], baseline_failed: bool) -> bool:
    """Return True if the agent's output regressed the pre-existing suite.

    `baseline_failed` is whether the pristine variant's own suite was already
    red; a regression is a suite that passed at baseline but fails after the
    agent's edits. A variant whose baseline is already red cannot be used to
    detect regressions, so it returns False (and the caller should flag it).
    """
    if baseline_failed:
        return False
    passed = subprocess.run(test_cmd, cwd=repo_dir, capture_output=True).returncode == 0
    return not passed


def run_variant(
    repo_dir: Path,
    task_prompt: str,
    *,
    variant: str,
    run_index: int,
    model: str,
    artifact_dir: Path,
    test_cmd: list[str] | None = None,
    baseline_failed: bool = False,
    allowed_tools=DEFAULT_ALLOWED_TOOLS,
    prompt_prefix: str = "",
    brief_tokens: int = 0,
) -> FootprintRecord:
    """Run one agent task on one variant and return its footprint row.

    `prompt_prefix` is the #289 arm-C injection (spec section 14.4): the archy
    brief is prepended to the task so its tokens land inside the run's own
    `input_tokens` (and thus `pre_edit_input_tokens`), making the net-accounting
    guard structural rather than bolted on (spec section 14.6). Arm A passes "".
    """
    full_prompt = f"{prompt_prefix}\n\n{task_prompt}" if prompt_prefix else task_prompt
    result = _run_claude(repo_dir, full_prompt, model=model, allowed_tools=allowed_tools)
    session_id = result["session_id"]

    transcript = _locate_transcript(repo_dir, session_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    saved = artifact_dir / f"{variant}_{run_index}_{session_id}.jsonl"
    shutil.copy(transcript, saved)
    parsed = parse_transcript(saved)

    regression = (
        _run_test_gate(repo_dir, test_cmd, baseline_failed) if test_cmd is not None else False
    )

    return FootprintRecord(
        variant=variant,
        run_index=run_index,
        model=model,
        input_tokens=parsed.input_tokens,
        cache_read_input_tokens=parsed.cache_read_input_tokens,
        cache_creation_input_tokens=parsed.cache_creation_input_tokens,
        output_tokens=parsed.output_tokens,
        num_turns=int(result.get("num_turns", parsed.assistant_messages)),
        distinct_files_touched=parsed.distinct_files_touched,
        file_revisitations=parsed.file_revisitations,
        pre_edit_reads=parsed.pre_edit_reads,
        pre_edit_distinct_files=parsed.pre_edit_distinct_files,
        pre_edit_input_tokens=parsed.pre_edit_input_tokens,
        made_edit=parsed.made_edit,
        brief_tokens=brief_tokens,
        duration_ms=int(result.get("duration_ms", 0)),
        total_cost_usd=float(result.get("total_cost_usd", 0.0)),
        task_completed=(result.get("subtype") == "success" and not result.get("is_error")),
        test_regression=regression,
    )


def run_pair(
    variant_a_dir: Path,
    variant_b_dir: Path,
    task_prompt: str,
    *,
    n: int,
    model: str,
    artifact_dir: Path,
    test_cmd: list[str] | None = None,
) -> list[FootprintRecord]:
    """Run the task n times on each variant, interleaved (A, B, A, B, ...).

    Interleaving averages out any slow drift in the service across the run
    (spec section 8). A single pair is not a result; n >= 10 is the floor.
    """
    records: list[FootprintRecord] = []
    for i in range(n):
        for variant, repo_dir in (("A", variant_a_dir), ("B", variant_b_dir)):
            records.append(
                run_variant(
                    repo_dir,
                    task_prompt,
                    variant=variant,
                    run_index=i,
                    model=model,
                    artifact_dir=artifact_dir,
                    test_cmd=test_cmd,
                )
            )
    return records


def run_arm_c(
    repo_dir: Path,
    task_prompt: str,
    brief: str,
    *,
    n: int,
    model: str,
    artifact_dir: Path,
    brief_tokens: int = 0,
    test_cmd: list[str] | None = None,
) -> list[FootprintRecord]:
    """#289 context-injection study: arm A (no brief) vs arm C (archy brief).

    Both arms run the identical task on the identical `repo_dir` at HEAD (spec
    section 14.2); they differ only by the brief prepended to arm C's prompt. A
    and C are interleaved per run_index so slow service drift averages out (spec
    section 8), and the brief bytes are persisted alongside the transcripts so the
    injected context is reviewable.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "brief.txt").write_text(brief, encoding="utf-8")
    records: list[FootprintRecord] = []
    for i in range(n):
        for variant, prefix, btoks in (("A", "", 0), ("C", brief, brief_tokens)):
            records.append(
                run_variant(
                    repo_dir,
                    task_prompt,
                    variant=variant,
                    run_index=i,
                    model=model,
                    artifact_dir=artifact_dir,
                    test_cmd=test_cmd,
                    prompt_prefix=prefix,
                    brief_tokens=btoks,
                )
            )
    return records


def _paired_deltas(records: list[FootprintRecord], metric: str) -> list[float]:
    """Per-run treatment-minus-A deltas for one metric, matched on run_index.

    "A" is always the control; the treatment is whichever other variant is
    present ("B" for the refactor study section 3, "C" for the #289 injection
    study section 14). A run_index missing either side is skipped.
    """
    by_run: dict[int, dict[str, FootprintRecord]] = {}
    for r in records:
        by_run.setdefault(r.run_index, {})[r.variant] = r
    deltas = []
    for pair in by_run.values():
        if "A" not in pair:
            continue
        treatment = next((v for v in ("B", "C") if v in pair), None)
        if treatment is None:
            continue
        deltas.append(float(getattr(pair[treatment], metric)) - float(getattr(pair["A"], metric)))
    return deltas


def summarize(records: list[FootprintRecord]) -> dict:
    """Median paired delta + sign count per footprint metric (spec section 9).

    `footprint_tokens` is a property rather than a field, but `getattr` in
    `_paired_deltas` resolves it the same way, so it needs no special case.
    """
    metrics = [
        "footprint_tokens",
        "input_tokens",
        "output_tokens",
        "num_turns",
        "distinct_files_touched",
        "file_revisitations",
        # #289 arm-C metrics; `pre_edit_reads` is the metric of record (14.3).
        "pre_edit_reads",
        "pre_edit_distinct_files",
        "pre_edit_input_tokens",
    ]
    out: dict = {"n_pairs": 0, "metrics": {}, "regressions": 0, "no_edit_runs": 0}
    for m in metrics:
        deltas = _paired_deltas(records, m)
        if not deltas:
            continue
        out["n_pairs"] = len(deltas)
        out["metrics"][m] = {
            "median_delta": statistics.median(deltas),
            # "lower" = treatment (B/C) spent less than A on this metric.
            "treatment_lower_count": sum(1 for d in deltas if d < 0),
            "treatment_higher_count": sum(1 for d in deltas if d > 0),
            "tie_count": sum(1 for d in deltas if d == 0),
        }
    out["regressions"] = sum(1 for r in records if r.test_regression)
    # A run that never edited: its whole transcript is the pre-edit prefix, so it
    # is reported separately and never folded into the median (spec section 14.3).
    out["no_edit_runs"] = sum(1 for r in records if not r.made_edit)
    return out


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Agent-footprint minimal-pair bench (#259)")
    ap.add_argument(
        "--mode",
        choices=("ab", "ac"),
        default="ab",
        help="ab = refactor study (section 3); ac = #289 context-injection (section 14)",
    )
    ap.add_argument("--repo-a", required=True, type=Path, help="variant A checkout (as-is)")
    ap.add_argument(
        "--repo-b", type=Path, help="variant B checkout (refactored); required for --mode ab"
    )
    ap.add_argument(
        "--brief-file",
        type=Path,
        help="archy brief prepended to arm C's prompt; required for --mode ac (section 14.4)",
    )
    ap.add_argument("--task-file", required=True, type=Path, help="file with the task prompt")
    ap.add_argument("--n", type=int, default=10, help="runs per variant (>= 10; spec section 8)")
    ap.add_argument("--model", required=True, help="pinned model id, recorded on every row")
    ap.add_argument("--out", type=Path, default=Path("/tmp/archy_footprint"))
    ap.add_argument("--test-cmd", default=None, help="test command for the regression gate")
    args = ap.parse_args(argv)

    # The runner shells out to `claude`, which authenticates however it is
    # configured: an ANTHROPIC_API_KEY, or an interactive subscription login
    # (so this bench runs inside a logged-in Claude Code session with no key).
    # Only the CLI's presence is a hard requirement.
    if shutil.which("claude") is None:
        print("`claude` CLI not found on PATH; the live runner needs it.", file=sys.stderr)
        return 2
    if args.n < 10:
        print(f"warning: n={args.n} < 10; a single pair is not a result (spec section 8).")

    task_prompt = args.task_file.read_text(encoding="utf-8")
    test_cmd = args.test_cmd.split() if args.test_cmd else None
    if args.mode == "ac":
        if args.brief_file is None:
            print("--mode ac requires --brief-file (spec section 14.4).", file=sys.stderr)
            return 2
        brief = args.brief_file.read_text(encoding="utf-8")
        # A rough token estimate charged to arm C for the results table; the
        # real cost is already inside pre_edit_input_tokens by construction.
        brief_tokens = max(1, len(brief) // 4)
        records = run_arm_c(
            args.repo_a,
            task_prompt,
            brief,
            n=args.n,
            model=args.model,
            artifact_dir=args.out,
            brief_tokens=brief_tokens,
            test_cmd=test_cmd,
        )
    else:
        if args.repo_b is None:
            print("--mode ab requires --repo-b.", file=sys.stderr)
            return 2
        records = run_pair(
            args.repo_a,
            args.repo_b,
            task_prompt,
            n=args.n,
            model=args.model,
            artifact_dir=args.out,
            test_cmd=test_cmd,
        )
    summary = summarize(records)
    (args.out / "records.json").write_text(json.dumps([r._asdict() for r in records], indent=2))
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
