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
from math import comb
from pathlib import Path

from pydantic import BaseModel, ConfigDict

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


def load_file_map(path: str | Path | None) -> dict[str, str]:
    """Load a variant's file-equivalence map (new path -> pre-refactor path).

    A decomposition variant splits one file into several, so a raw count of
    distinct files opened is **not variant-neutral**: reaching the same surface
    in B necessarily touches more files than in A, by construction. The map is
    authored with variant B, pre-registered alongside it, and reviewed as part of
    the variant diff; A's map is empty (identity).
    """
    if path is None:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _canonical_touch_path(normalized: str, file_map: dict[str, str]) -> str:
    """Map a touched path back to its pre-refactor equivalent.

    Keys are repo-relative and matched as path suffixes, because transcripts
    record absolute paths inside a per-variant checkout. Longest key first, so a
    more specific mapping wins over a shorter one that also matches.
    """
    for key in sorted(file_map, key=len, reverse=True):
        if normalized.endswith(key):
            return normalized[: -len(key)] + file_map[key]
    return normalized


def _normalize_touch_path(raw: str) -> str:
    """Lexically normalize a tool's file_path so equivalent paths collapse.

    `os.path.normpath` (not `pathlib`) is deliberate: it is a pure-string
    normalizer, whereas pathlib has no lexical equivalent and `Path.resolve()`
    would touch the filesystem -- wrong here, since we parse a transcript whose
    repo may not be checked out on this machine.
    """
    return os.path.normpath(raw)


class ParsedTranscript(BaseModel):
    """Everything derivable from a session transcript alone (no run config).

    Token fields are summed across every assistant message: each assistant
    message carries the usage for *its own* generation, so the sum is the total
    the run consumed. `cache_read_input_tokens` is reported separately and never
    folded into the footprint headline, because cache hit/miss is a cross-run
    artifact, not agent effort (spec section 4).
    """

    model_config = ConfigDict(frozen=True)

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
    # Breadth counted over *pre-refactor* paths, so the two arms are comparable
    # even though B splits one file into several (spec section 5). Identical to
    # the raw counts on variant A, whose map is empty.
    canonical_distinct_files_touched: int
    canonical_pre_edit_distinct_files: int
    canonical_file_revisitations: int

    @property
    def footprint_tokens(self) -> int:
        return _footprint_tokens(self.input_tokens, self.output_tokens)


class FootprintRecord(BaseModel):
    """One agent run's full footprint row: parsed transcript + run context."""

    model_config = ConfigDict(frozen=True)

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
    # None only on rows written before #302; new runs always populate these.
    canonical_distinct_files_touched: int | None = None
    canonical_pre_edit_distinct_files: int | None = None
    canonical_file_revisitations: int | None = None
    brief_tokens: int  # realized archy-brief size charged to this arm (0 for A)
    duration_ms: int
    total_cost_usd: float
    task_completed: bool
    test_regression: bool
    # Recorded per row so "0 regressions" is falsifiable from the artifact alone:
    # `test_regression` is forced False when the variant's own baseline was red,
    # so without this field a disabled gate is indistinguishable from a passing one.
    baseline_failed: bool = False

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


def parse_transcript(path: str | Path, file_map: dict[str, str] | None = None) -> ParsedTranscript:
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
    file_map = file_map or {}

    # Claude Code writes ONE LINE PER CONTENT BLOCK: the same `message.id` repeats
    # across consecutive lines, each carrying identical `usage` and a `content`
    # list holding exactly one block (a `thinking` block, then one `tool_use`, and
    # so on). So usage must be counted once per id, but blocks must be
    # CONCATENATED across the lines.
    #
    # An earlier version kept the last line per id, on the theory that the final
    # copy was a complete streaming partial. It is not: last-wins silently dropped
    # every block but the last, undercounting tool calls whenever a message
    # carried more than one (1.9% of blocks in the #282 run transcripts, ~12% in
    # sessions that batch parallel tool calls). That is a data-dependent
    # undercount of every file metric, so it is not even a constant offset.
    blocks_by_id: dict[str, list[dict]] = {}
    usage_by_id: dict[str, dict] = {}
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
            # Sidechain entries are a subagent's own transcript interleaved into
            # this file; folding them in would attribute a subagent's tool calls
            # to the parent run.
            if entry.get("isSidechain"):
                continue
            message = entry.get("message", {})
            mid = message.get("id")
            if not mid:
                # No id to group on: treat each occurrence as its own message.
                mid = f"_noid_{unkeyed}"
                unkeyed += 1
            if mid not in blocks_by_id:
                order.append(mid)
                blocks_by_id[mid] = []
                usage_by_id[mid] = message.get("usage") or {}
            blocks_by_id[mid].extend(
                b for b in (message.get("content") or []) if isinstance(b, dict)
            )
    deduped = {mid: {"usage": usage_by_id[mid], "content": blocks_by_id[mid]} for mid in order}

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
    canonical_touched: set[str] = set()
    canonical_pre_edit_files: set[str] = set()
    canonical_written: set[str] = set()
    canonical_revisitations = 0
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
                        canonical_pre_edit_files.add(_canonical_touch_path(file_path, file_map))
            # file_path is non-None only for a file tool (Read/Edit/Write), so
            # any such touch of an already-written path is a revisit.
            if file_path is None:
                continue
            touched.add(file_path)
            canonical_path = _canonical_touch_path(file_path, file_map)
            canonical_touched.add(canonical_path)
            if file_path in written:
                revisitations += 1
            # Revisitation is path-keyed too, so it has the same variant-neutrality
            # problem as breadth (#302): in A, writing then re-reading console.py is
            # one revisit, while in B the same logical surface spans two files and
            # the second touch lands on a different path, silently scoring B better.
            # Counting over pre-refactor paths makes the arms comparable.
            if canonical_path in canonical_written:
                canonical_revisitations += 1
            if name in _WRITE_TOOLS:
                written.add(file_path)
                canonical_written.add(canonical_path)

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
        canonical_distinct_files_touched=len(canonical_touched),
        canonical_pre_edit_distinct_files=len(canonical_pre_edit_files),
        canonical_file_revisitations=canonical_revisitations,
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


def _reset_repo(repo_dir: Path) -> None:
    """Restore `repo_dir` to its pinned commit, discarding the last run's edits.

    Spec section 8 asks for a fresh checkout per run. The variants are real git
    clones pinned to their own HEAD (variant B's refactor is a commit on top of
    the pinned upstream commit), so hard-reset plus clean is that fresh checkout
    without re-cloning: run i+1 must not start from run i's edits, or every run
    after the first measures a different repo than the one under test.

    Note `-fdx` also removes an in-repo `.claude/`, so from here on a variant has
    no project-local settings for `--setting-sources local` to load. That is more
    isolation than spec section 10 describes, not less, but it is a real change in
    how the runs are configured and the variants must not rely on committed
    project settings.
    """
    subprocess.run(["git", "reset", "--hard", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "clean", "-fdxq"], cwd=repo_dir, check=True)


def _head_identity(repo_dir: Path) -> dict[str, str]:
    """HEAD's author, author date and subject, or empty strings if unavailable.

    Never raises: this feeds a diagnostic, and a diagnostic must not be the thing
    that aborts a run (an empty repo or a non-git dir would otherwise propagate a
    CalledProcessError out of `run_pair`).
    """
    # %aI/%cI, not %ad/%cd: the latter render per the repo's `log.date` config, so
    # `log.date = short` collapses a 14-hour gap to an identical string and date
    # parity silently stops detecting anything. ISO-strict is config-immune.
    # %B (full body), not %s: `git log -1` prints the body to the agent too, and
    # the body is where a description of the treatment actually hides.
    fmt = "%an <%ae>%n%aI%n%cn <%ce>%n%cI%n%B"
    proc = subprocess.run(
        ["git", "log", "-1", f"--format={fmt}"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = proc.stdout.splitlines() if proc.returncode == 0 else []
    count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "author": lines[0] if lines else "",
        "author_date": lines[1] if len(lines) > 1 else "",
        "committer": lines[2] if len(lines) > 2 else "",
        "commit_date": lines[3] if len(lines) > 3 else "",
        "message": "\n".join(lines[4:]).strip(),
        "commit_count": count.stdout.strip() if count.returncode == 0 else "",
    }


def check_variant_provenance(variant_a_dir: Path, variant_b_dir: Path) -> dict:
    """Report how far variant B's HEAD advertises that the repo is instrumented.

    B carries the refactor as a commit on top of the pinned SHA, and the agent has
    `Bash`, so `git log -1` / `git blame` hand it whatever that commit says. In
    #282 the message was rewritten to look upstream but the author still read
    `archy bench`, because `git commit --amend` keeps the original author without
    `--reset-author`.

    Author *and* author-date parity are both checked: `git log -1` prints the date
    by default, so an upstream author with the run day's date leaks just as
    plainly (spec section 6). The subject cannot be validated automatically -- a
    careful "refactor: split the application base class" describes the treatment
    while matching every upstream convention -- so it is echoed verbatim for the
    operator to judge rather than keyword-matched, which was both false-positive
    prone ("invariant", "benchmark") and blind to the real leak.
    """
    a, b = _head_identity(variant_a_dir), _head_identity(variant_b_dir)
    leaks = []
    # Committer is checked as well as author because `--reset-author`, the remedy
    # the spec prescribes, does NOT reset the committer: `git log --format=fuller`
    # (or `git show --pretty=fuller`) still prints `archy bench` and the run day.
    # The argument for adding the author-date check applies one flag further.
    for field, label in (
        ("author", "author"),
        ("author_date", "author date"),
        ("committer", "committer"),
        ("commit_date", "commit date"),
    ):
        if b[field] != a[field]:
            leaks.append(f"{label} {b[field]!r} differs from A's {a[field]!r}")
    if b["commit_count"] != a["commit_count"]:
        leaks.append(
            f"HEAD is {b['commit_count']} commits deep against A's {a['commit_count']};"
            " `git log --oneline` and `git show --stat HEAD` expose the refactor"
        )
    return {
        "leaks": leaks,
        "variant_b_message": b["message"],
        "variant_a_head": a,
        "variant_b_head": b,
    }


def _warn_on_provenance_leak(variant_a_dir: Path, variant_b_dir: Path) -> dict:
    """`check_variant_provenance` plus a stderr warning; returns the verdict."""
    verdict = check_variant_provenance(variant_a_dir, variant_b_dir)
    for leak in verdict["leaks"]:
        print(
            f"warning: variant B's HEAD {leak}; `git log` leaks that B is"
            " instrumented. Re-commit B with --reset-author and the upstream"
            " author identity and date (spec section 6).",
            file=sys.stderr,
        )
    if verdict["variant_b_message"]:
        print(
            "note: the agent can read variant B's HEAD message in full"
            f" (`git log -1` prints the body too):\n{verdict['variant_b_message']}\n"
            "It must not describe the treatment.",
            file=sys.stderr,
        )
    return verdict


def _baseline_failed(repo_dir: Path, test_cmd: list[str] | None) -> bool:
    """Whether the variant's own pristine suite is already red (spec section 7).

    A variant with a red baseline cannot be used to detect regressions, so this
    is measured once per variant before any agent runs and threaded into the
    per-run gate.
    """
    if test_cmd is None:
        return False
    _reset_repo(repo_dir)
    return subprocess.run(test_cmd, cwd=repo_dir, capture_output=True).returncode != 0


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
    reset_repo: bool = True,
    file_map: dict[str, str] | None = None,
) -> FootprintRecord:
    """Run one agent task on one variant and return its footprint row.

    `prompt_prefix` is the #289 arm-C injection (spec section 14.4): the archy
    brief is prepended to the task so its tokens land inside the run's own
    `input_tokens` (and thus `pre_edit_input_tokens`), making the net-accounting
    guard structural rather than bolted on (spec section 14.6). Arm A passes "".
    """
    if reset_repo:
        _reset_repo(repo_dir)

    full_prompt = f"{prompt_prefix}\n\n{task_prompt}" if prompt_prefix else task_prompt
    result = _run_claude(repo_dir, full_prompt, model=model, allowed_tools=allowed_tools)
    session_id = result["session_id"]

    transcript = _locate_transcript(repo_dir, session_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    saved = artifact_dir / f"{variant}_{run_index}_{session_id}.jsonl"
    shutil.copy(transcript, saved)
    parsed = parse_transcript(saved, file_map)

    regression = (
        _run_test_gate(repo_dir, test_cmd, baseline_failed) if test_cmd is not None else False
    )

    record = FootprintRecord(
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
        canonical_distinct_files_touched=parsed.canonical_distinct_files_touched,
        canonical_pre_edit_distinct_files=parsed.canonical_pre_edit_distinct_files,
        canonical_file_revisitations=parsed.canonical_file_revisitations,
        brief_tokens=brief_tokens,
        duration_ms=int(result.get("duration_ms", 0)),
        total_cost_usd=float(result.get("total_cost_usd", 0.0)),
        task_completed=(result.get("subtype") == "success" and not result.get("is_error")),
        test_regression=regression,
        baseline_failed=baseline_failed,
    )

    # Append as we go: a full pair run is hours of live agent time, and a single
    # failed `claude` invocation raises out of the loop. Rows already paid for
    # survive in this file even when the process never reaches its final write.
    with (artifact_dir / "records.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.model_dump()) + "\n")

    return record


def run_pair(
    variant_a_dir: Path,
    variant_b_dir: Path,
    task_prompt: str,
    *,
    n: int,
    model: str,
    artifact_dir: Path,
    test_cmd: list[str] | None = None,
    variant_b_file_map: dict[str, str] | None = None,
) -> list[FootprintRecord]:
    """Run the task n times on each variant, counterbalanced (A,B / B,A / A,B ...).

    Interleaving averages out slow drift in the service across the run;
    alternating which variant goes *first* additionally stops variant from being
    confounded with within-pair position (spec section 8). A single pair is not a
    result; n >= 10 is the floor, and an odd n leaves the design slightly
    unbalanced because one order gets the extra pair.
    """
    # Persisted, not just printed: a stderr line scrolls past in a multi-hour run,
    # and afterwards nothing in the artifacts would say whether B's history leaked.
    artifact_dir.mkdir(parents=True, exist_ok=True)
    provenance = _warn_on_provenance_leak(variant_a_dir, variant_b_dir)
    (artifact_dir / "provenance.json").write_text(json.dumps(provenance, indent=2))
    baseline = {
        "A": _baseline_failed(variant_a_dir, test_cmd),
        "B": _baseline_failed(variant_b_dir, test_cmd),
    }
    records: list[FootprintRecord] = []
    for i in range(n):
        # Counterbalanced, not just interleaved: running A first every pair
        # confounds variant with within-pair position. The #282 run did exactly
        # that and A's pre_edit_reads drifted upward across the run (Spearman
        # +0.78) while B's stayed flat, which flattered B on the metric of record.
        order = (("A", variant_a_dir), ("B", variant_b_dir))
        if i % 2:
            order = tuple(reversed(order))
        for variant, repo_dir in order:
            records.append(
                run_variant(
                    repo_dir,
                    task_prompt,
                    variant=variant,
                    run_index=i,
                    model=model,
                    artifact_dir=artifact_dir,
                    test_cmd=test_cmd,
                    baseline_failed=baseline[variant],
                    # Only B's paths need mapping back; A's map is identity, so
                    # both arms end up counted over the same pre-refactor surface.
                    file_map=variant_b_file_map if variant == "B" else None,
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
    and C are interleaved and counterbalanced per run_index so neither slow
    service drift nor within-pair position is confounded with the arm (spec
    section 8), and the brief bytes are persisted alongside the transcripts so the
    injected context is reviewable.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "brief.txt").write_text(brief, encoding="utf-8")
    baseline_red = _baseline_failed(repo_dir, test_cmd)
    records: list[FootprintRecord] = []
    for i in range(n):
        arms = (("A", "", 0), ("C", brief, brief_tokens))
        if i % 2:
            arms = tuple(reversed(arms))
        for variant, prefix, btoks in arms:
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
                    baseline_failed=baseline_red,
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


def sign_test_p(deltas: list[float]) -> float:
    """Two-sided exact binomial sign test on the non-tied paired deltas.

    Ties carry no directional information, so they leave `n` rather than being
    split: with 8 non-tied of 10, a 0-8 split is 2 * C(8,0)/2**8 = 0.0078.
    """
    pos = sum(1 for d in deltas if d > 0)
    neg = sum(1 for d in deltas if d < 0)
    n = pos + neg
    if n == 0:
        return 1.0
    k = min(pos, neg)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2**n)


def _variant_values(records: list[FootprintRecord], metric: str, variant: str) -> list[float]:
    """Every run's value for one metric on one variant, paired runs only."""
    paired = _paired_run_indices(records)
    return [
        float(getattr(r, metric)) for r in records if r.variant == variant and r.run_index in paired
    ]


def _treatment_values(records: list[FootprintRecord], metric: str) -> list[float]:
    """Same, for whichever non-control arm is present ("B" or "C")."""
    treatment = next((v for v in ("B", "C") if any(r.variant == v for r in records)), "B")
    return _variant_values(records, metric, treatment)


def _paired_run_indices(records: list[FootprintRecord]) -> set[int]:
    """Run indices that have both a control and a treatment row.

    Per-variant medians must be computed over the same pairs the deltas use, or a
    half-finished pair would shift one arm's median but not the other's.
    """
    by_run: dict[int, set[str]] = {}
    for r in records:
        by_run.setdefault(r.run_index, set()).add(r.variant)
    return {i for i, vs in by_run.items() if "A" in vs and ("B" in vs or "C" in vs)}


def _tied_ranks(values: list[float]) -> list[float]:
    """Ranks with ties averaged, the standard definition Spearman assumes.

    Ranking tied values ordinally instead (1,2,3 for three equal values) inflates
    the coefficient and makes it depend on tie-break order, so the same data can
    yield different answers. #282 published +0.78 from an ad-hoc ordinal ranking
    where the correct figure is +0.67.
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def drift_spearman(records: list[FootprintRecord], metric: str, variant: str) -> float | None:
    """Tie-corrected Spearman of one variant's metric against `run_index`.

    Position drift is why §8 requires counterbalancing: if one arm's metric trends
    across the run and the other's does not, whichever arm holds a fixed position
    absorbs the trend. Lives here so the diagnostic is reproducible from the
    committed records rather than from a script (§9).
    """
    rows = sorted((r for r in records if r.variant == variant), key=lambda r: r.run_index)
    if len(rows) < 3:
        return None
    values = [float(getattr(r, metric)) for r in rows]
    if len(set(values)) == 1:
        return None  # a flat series has no rank order to correlate
    return statistics.correlation(
        _tied_ranks(values), _tied_ranks([float(r.run_index) for r in rows])
    )


def summarize(records: list[FootprintRecord]) -> dict:
    """Median paired delta, IQR, sign counts and sign-test p per metric (spec 9).

    Everything a published results table needs is emitted here on purpose. The
    #282 writeup was assembled by a separate ad-hoc script, which silently used a
    different `footprint_tokens` definition than the one below and shipped a wrong
    headline; a table computed from any other code path can drift the same way.

    `footprint_tokens` is a property rather than a field, but `getattr` in
    `_paired_deltas` resolves it the same way, so it needs no special case.
    """
    metrics = [
        "footprint_tokens",
        "input_tokens",
        "output_tokens",
        "num_turns",
        # The A/B primary (spec section 4), counted over pre-refactor paths for
        # the same reason breadth is: raw path keys favour the split variant.
        "canonical_file_revisitations",
        "file_revisitations",
        # Breadth, counted over pre-refactor paths so a decomposition variant is
        # not penalized for splitting one file into several (spec section 5).
        "canonical_distinct_files_touched",
        "canonical_pre_edit_distinct_files",
        # The raw counts are kept for the descriptive table but are NOT
        # variant-neutral; section 12.7 forbids headlining them.
        "distinct_files_touched",
        "pre_edit_distinct_files",
        # #289 arm-C metrics; `pre_edit_reads` is the metric of record (14.3).
        "pre_edit_reads",
        "pre_edit_input_tokens",
    ]
    # Which arm is the treatment is emitted, not left implicit: rendering an
    # arm-C summary under a hard-coded "B" heading would mislabel the exact
    # document class this reporting path exists to protect.
    arms = {r.variant for r in records}
    if "B" in arms and "C" in arms:
        # `_paired_deltas` picks the treatment per run while the medians pick one
        # globally, so a mixed file would blend two studies under one heading.
        raise ValueError("records mix arm B and arm C; summarize one study at a time")
    treatment = next((v for v in ("B", "C") if v in arms), "B")
    out: dict = {
        "n_pairs": 0,
        "treatment": treatment,
        "metrics": {},
        "regressions": 0,
        "no_edit_runs": 0,
    }
    for m in metrics:
        if m.startswith("canonical_") and any(getattr(r, m) is None for r in records):
            continue  # rows predating #302 carry no canonical counts
        deltas = _paired_deltas(records, m)
        if not deltas:
            continue
        out["n_pairs"] = len(deltas)
        # "inclusive": the default "exclusive" method extrapolates beyond the data
        # for tiny samples (quantiles([0, 10]) -> [-2.5, 5.0, 12.5]), which would
        # publish an interval wider than every value actually observed.
        quartiles = (
            statistics.quantiles(deltas, n=4, method="inclusive") if len(deltas) >= 2 else None
        )
        out["metrics"][m] = {
            "median_delta": statistics.median(deltas),
            # "lower" = treatment (B/C) spent less than A on this metric.
            "treatment_lower_count": sum(1 for d in deltas if d < 0),
            "treatment_higher_count": sum(1 for d in deltas if d > 0),
            "tie_count": sum(1 for d in deltas if d == 0),
            "sign_p": sign_test_p(deltas),
            # The quartile *bounds* (q1, q3), not their difference: spec section 9
            # asks for an interval around the median, so the pair is what gets
            # published. Named explicitly because "iqr" invites the other reading.
            "iqr_bounds": [quartiles[0], quartiles[2]] if quartiles else None,
            "control_median": statistics.median(_variant_values(records, m, "A")),
            "treatment_median": statistics.median(_treatment_values(records, m)),
            "deltas": deltas,
        }
    # Correcting over the metrics tested while publishing a subset is selective
    # reporting, so the divisor is emitted alongside the p-values that need it.
    out["metrics_tested"] = len(out["metrics"])
    out["regressions"] = sum(1 for r in records if r.test_regression)
    # A red baseline silently disables that run's gate, so "regressions: 0" would
    # otherwise read as "nothing broke" when it means "we could not tell".
    out["gate_disabled_runs"] = sum(1 for r in records if r.baseline_failed)
    # A run that never edited: its whole transcript is the pre-edit prefix, so it
    # is reported separately and never folded into the median (spec section 14.3).
    out["no_edit_runs"] = sum(1 for r in records if not r.made_edit)
    out["drift_pre_edit_reads"] = {
        arm: drift_spearman(records, "pre_edit_reads", arm) for arm in ("A", treatment)
    }
    return out


def load_records(path: str | Path) -> list[FootprintRecord]:
    """Read a `records.jsonl` back into rows, for reporting off a finished run."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [FootprintRecord(**json.loads(line)) for line in lines if line.strip()]


def results_table(summary: dict, *, treatment: str | None = None) -> str:
    """The published results table, rendered from `summarize()` output.

    Emitted by the harness so a writeup pastes it rather than recomputing it, and
    carrying the interval spec section 9 requires alongside the median. All tested
    metrics are rendered, never a subset, because the Bonferroni divisor reported
    underneath is the count of what was tested (spec section 9).
    """
    t = treatment or summary.get("treatment", "B")
    # The pre-registered primary differs by study: the A/B refactor study's is
    # `file_revisitations` (spec section 4), arm C's is `pre_edit_reads` (section
    # 14.3). Hardcoding one would let the table nominate whichever metric read
    # better after the fact.
    primary, primary_ref = (
        ("`pre_edit_reads`", "spec section 14.3")
        if t == "C"
        else ("`file_revisitations`", "spec section 4")
    )
    if not summary["metrics"]:
        # Otherwise a truncated or empty run renders an empty-but-publishable
        # table footed by "0 metrics tested", which reads like a finished study.
        return (
            "No paired runs in this record set: nothing to report."
            " (A run that produced no complete A/B pair is not a null result.)"
        )
    lines = [
        f"| metric | A median | {t} median | median delta ({t}-A) | IQR of deltas |"
        f" {t}<A / {t}>A / tie | sign p |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, m in summary["metrics"].items():
        counts = f"{m['treatment_lower_count']} / {m['treatment_higher_count']} / {m['tie_count']}"
        bounds = m.get("iqr_bounds")
        interval = f"[{bounds[0]:+,.1f}, {bounds[1]:+,.1f}]" if bounds else "n/a"
        lines.append(
            f"| {name} | {m['control_median']:,.1f} | {m['treatment_median']:,.1f} |"
            f" {m['median_delta']:+,.1f} | {interval} | {counts} | {m['sign_p']:.3f} |"
        )
    n = summary.get("metrics_tested", len(summary["metrics"]))
    lines.append("")
    lines.append(
        f"N={summary['n_pairs']} pairs; {n} metrics tested, so a nominal p must clear"
        f" p x {n} to survive Bonferroni. Regressions: {summary['regressions']};"
        f" runs with the gate disabled by a red baseline: {summary.get('gate_disabled_runs', 0)};"
        f" no-edit runs: {summary['no_edit_runs']}."
    )
    lines.append("")
    lines.append(
        "The delta column is the median of the per-pair deltas, not the difference of"
        " the two median columns; with a skewed spread those disagree, and the paired"
        " median is the one the sign test refers to."
        f" Primary metric: {primary} ({primary_ref})."
        " `input_tokens` is non-cache input only (spec section 5) and is ~2 tokens"
        " per turn under prompt caching, so it is a turn proxy, not a token measure."
        " Breadth: use the `canonical_*` rows, which count pre-refactor paths;"
        " the raw `distinct_files_touched` / `pre_edit_distinct_files` rows are"
        " descriptive only and are biased against a decomposition variant by"
        " construction (spec section 12.7)."
    )
    drift = {k: v for k, v in summary.get("drift_pre_edit_reads", {}).items() if v is not None}
    if drift:
        rendered = ", ".join(f"{arm}={value:+.3f}" for arm, value in drift.items())
        lines.append("")
        lines.append(
            "Position drift, tie-corrected Spearman of `pre_edit_reads` vs run index:"
            f" {rendered}. An arm that drifts while the other does not is why"
            " section 8 requires counterbalancing."
        )
    return "\n".join(lines)


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Agent-footprint minimal-pair bench (#259)")
    ap.add_argument(
        "--mode",
        choices=("ab", "ac"),
        default="ab",
        help="ab = refactor study (section 3); ac = #289 context-injection (section 14)",
    )
    ap.add_argument(
        "--table",
        type=Path,
        metavar="RECORDS_JSONL",
        help=(
            "render the published results table from an existing records.jsonl and"
            " exit; no agent runs. Exists so a writeup never needs an ad-hoc"
            " analysis script (spec section 9)"
        ),
    )
    ap.add_argument("--repo-a", type=Path, help="variant A checkout (as-is)")
    ap.add_argument(
        "--repo-b", type=Path, help="variant B checkout (refactored); required for --mode ab"
    )
    ap.add_argument(
        "--brief-file",
        type=Path,
        help="archy brief prepended to arm C's prompt; required for --mode ac (section 14.4)",
    )
    ap.add_argument("--task-file", type=Path, help="file with the task prompt")
    ap.add_argument("--n", type=int, default=10, help="runs per variant (>= 10; spec section 8)")
    ap.add_argument("--model", help="pinned model id, recorded on every row")
    ap.add_argument("--out", type=Path, default=Path("/tmp/archy_footprint"))
    ap.add_argument("--test-cmd", default=None, help="test command for the regression gate")
    ap.add_argument(
        "--file-map",
        type=Path,
        help=(
            "variant B's file-equivalence map (new path -> pre-refactor path), so"
            " breadth is counted over a common surface. Required for --mode ab"
            " when B adds files, or breadth silently reproduces the #302 confound"
        ),
    )
    args = ap.parse_args(argv)

    if args.table is not None:
        print(results_table(summarize(load_records(args.table))))
        return 0

    # Required only for a live run; `--table` is a pure reporting path, so these
    # are validated here rather than by argparse's `required=`.
    missing = [n for n in ("repo_a", "task_file", "model") if getattr(args, n) is None]
    if missing:
        print(f"missing required options for a live run: {missing}", file=sys.stderr)
        return 2

    # The runner shells out to `claude`, which authenticates however it is
    # configured: an ANTHROPIC_API_KEY, or an interactive subscription login
    # (so this bench runs inside a logged-in Claude Code session with no key).
    # Only the CLI's presence is a hard requirement.
    if shutil.which("claude") is None:
        print("`claude` CLI not found on PATH; the live runner needs it.", file=sys.stderr)
        return 2
    if args.n < 10:
        print(f"warning: n={args.n} < 10; a single pair is not a result (spec section 8).")
    if args.n % 2:
        print(
            f"warning: n={args.n} is odd, so counterbalancing is unbalanced"
            " (one within-pair order gets the extra pair)."
        )

    # A re-run into the same --out restarts run_index at 0, so appending would put
    # duplicate (variant, run_index) keys in one file and `_paired_deltas` would
    # silently keep only the last of each. Roll the old file aside instead: the
    # durability the append buys is worthless if a resume corrupts the analysis.
    existing = args.out / "records.jsonl"
    if existing.exists():
        nth = sum(1 for _ in args.out.glob("records.jsonl.prev*")) + 1
        existing.rename(existing.with_suffix(f".jsonl.prev{nth}"))
        # The provenance verdict belongs to those rows, so it moves with them
        # rather than being overwritten by the new run's.
        verdict = args.out / "provenance.json"
        if verdict.exists():
            verdict.rename(verdict.with_suffix(f".json.prev{nth}"))
        print(f"note: moved a pre-existing records.jsonl (and provenance) aside to .prev{nth}")

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
            variant_b_file_map=load_file_map(args.file_map),
        )
    summary = summarize(records)
    (args.out / "records.json").write_text(json.dumps([r.model_dump() for r in records], indent=2))
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
