"""Labeler B for #548: a fresh-context LLM labels each blind packet, resumably.

    uv run python bench/diff_duplicates_labeler.py PACKETS_DIR [--workers 3] [--max N]

PACKETS_DIR is the output of `diff_duplicates_packets.py`. Each firing is labeled by
its own headless `claude -p` run in a throwaway directory that holds ONLY a
read-only export of the directories of the two functions (`git archive`, no `.git`),
with read-only tools and no settings, memory, MCP or slash commands. The model sees
the packet and that surrounding code, and nothing the pre-registration hides.

Checkpoint and resume, with the same discipline as the other benches:

- One ledger row per firing (`PACKETS_DIR/labels_<name>.jsonl`, B by default), appended in
  one write by `bench/_supervise.Ledger`. Only `status="ok"` is done; an `error` row (a stall, an
  unparseable answer) is retried on the next run.
- A usage limit is not a result: the run waits and retries the SAME firing.
- To PAUSE: `touch PACKETS_DIR/PAUSE`. Workers finish the firing in hand, no new one
  starts, and the process exits 0 saying how to resume. Ctrl-C does the same. Delete
  the file and rerun the same command to continue. `--max N` bounds one invocation.

The ledger rows carry `{"id", "label"}`, so the file is directly the labels file
`diff_duplicates_score.py` reads.

archy:owns        LabelOptions, build_prompt, export_context, label_all, label_one,
                  main, parse_answer
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from _supervise import (  # ty: ignore[unresolved-import]  (bench/ is sys.path[0])
    Ledger,
    run_supervised,
    with_retries,
)
from diff_duplicates_pr import CLONES  # ty: ignore[unresolved-import]
from q1b_run import (  # ty: ignore[unresolved-import]
    RateLimited,
    agent_env,
    hit_rate_limit,
    parse_reset_at,
)

DEFAULT_MODEL = "claude-opus-5-5"
LABELS = ("TRUE", "FALSE", "UNSCORABLE")
REASONS = (
    "sibling",
    "boilerplate",
    "example-or-test-like",
    "isolation",
    "same-class",
    "widespread-name",
    "short-or-trivial",
    "other",
)
TOOLS = "Read,Grep,Glob"
DENIED = "Bash,Write,Edit,NotebookEdit,WebFetch,WebSearch,Task,Agent"
SYSTEM = (
    "You are a careful code reviewer labeling one candidate duplicate for a research "
    "study. You may read files in the current directory (read-only). Answer only with the "
    "JSON object requested."
)
_ANSWER = re.compile(r"\{[^{}]*\}", flags=re.DOTALL)


def build_prompt(packet: dict) -> str:
    """The user prompt: rubric, repository, the two groups of functions, the answer shape."""

    def block(title: str, members: list[dict]) -> str:
        parts = [
            f"### {m['path']}  ({m['qualified_name']})\n```python\n{m['source']}\n```"
            for m in members
        ]
        return f"## {title}\n" + "\n\n".join(parts)

    return "\n\n".join(
        [
            f"Repository: {packet['repo']}",
            "The question is whether the NEW function should have called or reused the "
            "EXISTING one. Surrounding files are in the current directory if you need them.",
            packet["instructions"],
            block("NEW function(s), added by the change", packet["new"]),
            block("EXISTING function(s), already in the codebase", packet["existing"]),
            'Reply with ONE JSON object and nothing else: {"label": "TRUE" | "FALSE" | '
            '"UNSCORABLE", "reason": null for TRUE, else one of '
            f"{list(REASONS)}, "
            '"note": one sentence}. Use UNSCORABLE only if you cannot judge it.',
        ]
    )


def parse_answer(text: str) -> dict | None:
    """The labeler's verdict from its final text, or None if it is not a valid one.

    An unknown FALSE reason is folded into `other`; a missing or unknown LABEL is not a
    verdict and returns None (the firing is retried, never scored from a guess).
    """
    for match in _ANSWER.finditer(text):
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        label = data.get("label")
        if label not in LABELS:
            continue
        reason = data.get("reason")
        if label == "FALSE" and reason not in REASONS:
            reason = "other"
        return {
            "label": label,
            "reason": reason if label == "FALSE" else None,
            "note": str(data.get("note", ""))[:300],
        }
    return None


def export_context(repo_dir: Path, head: str, packet: dict, dest: Path) -> None:
    """Read-only export of the directories holding the packet's functions, at `head`."""
    dirs = sorted({str(Path(m["path"]).parent) for m in packet["new"] + packet["existing"]})
    archive = subprocess.run(
        ["git", "-C", str(repo_dir), "archive", head, "--", *dirs],
        capture_output=True,
        check=True,
        timeout=600,
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True, timeout=600)


def _ask(prompt: str, cwd: Path, model: str) -> dict:
    cmd = [
        "claude", "-p", prompt, "--output-format", "json", "--model", model,
        "--tools", TOOLS, "--disallowedTools", DENIED, "--system-prompt", SYSTEM,
        "--setting-sources", "local", "--strict-mcp-config", "--disable-slash-commands",
        "--no-session-persistence", "--permission-mode", "default",
    ]  # fmt: skip
    result = with_retries(
        lambda: run_supervised(
            cmd, cwd=cwd, progress_paths=[cwd], max_wall=900.0, stall_after=300.0, env=agent_env()
        ),
        attempts=3,
    )
    if hit_rate_limit(result.stdout, result.stderr, result.returncode):
        blob = f"{result.stdout}\n{result.stderr}"
        raise RateLimited(blob.strip()[:200], reset_at=parse_reset_at(blob))
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stdout[-300:]}")
    return json.loads(result.stdout)


def label_one(packet: dict, head: str, model: str) -> dict:
    """Label one packet in a throwaway directory. Raises on an unusable answer."""
    repo_dir = CLONES / packet["repo"].replace("/", "__")
    with tempfile.TemporaryDirectory(prefix="label-") as tmp:
        export_context(repo_dir, head, packet, Path(tmp))
        envelope = _ask(build_prompt(packet), Path(tmp), model)
    answer = parse_answer(str(envelope.get("result", "")))
    if answer is None:
        raise ValueError(f"unparseable labeler answer: {str(envelope.get('result'))[:200]}")
    return {**answer, "seconds": round(envelope.get("duration_ms", 0) / 1000, 1)}


def _wait_out(exc: RateLimited, attempt: int) -> None:
    wait = (
        min(max((exc.reset_at or 0) - time.time(), 60.0), 3600.0)
        if exc.reset_at
        else min(300.0 * 2**attempt, 3600.0)
    )
    print(f"rate limited; sleeping {wait / 60:.0f}m [wait {attempt + 1}]", flush=True)
    time.sleep(wait)


@dataclass(frozen=True)
class LabelOptions:
    """Per-invocation knobs, bundled so `label_all` stays narrow."""

    workers: int = 3
    limit: int | None = None
    sleep: Callable[[RateLimited, int], None] = _wait_out
    name: str = "B"  # which labeler: its ledger is `labels_<name>.jsonl`


DEFAULT_OPTIONS = LabelOptions()
MAX_LIMIT_WAITS = 8


def _attempt(
    packet: dict, head: str, label: Callable[[dict, str], dict], sleep: Callable
) -> dict | None:
    """The row for one firing, waiting out usage limits on the SAME firing (a limit is a
    fact about the account, not a verdict). None if the limit never lifted; any other
    exception propagates to the caller, which records it as a retryable error."""
    for attempt in range(MAX_LIMIT_WAITS):
        try:
            return {"id": packet["id"], **label(packet, head)}
        except RateLimited as exc:
            sleep(exc, attempt)
    return None


class _Run:
    """One labeling invocation over a packets directory."""

    def __init__(self, out: Path, label: Callable[[dict, str], dict], options: LabelOptions):
        self.out, self.label, self.options = out, label, options
        self.key = json.loads((out / "key.json").read_text(encoding="utf-8"))
        self.ledger = Ledger(out / f"labels_{options.name}.jsonl")
        self.lock = threading.Lock()
        self.pause = out / "PAUSE"

    def pending(self) -> list[Path]:
        paths = sorted((self.out / "packets").glob("*.json"))
        todo = [p for p in paths if not self.ledger.is_done(_id(p))]
        return todo if self.options.limit is None else todo[: self.options.limit]

    def record(self, packet_id: str, payload: dict, status: str = "ok") -> None:
        with self.lock:
            self.ledger.record(packet_id, {"id": packet_id, **payload}, status=status)

    def one(self, path: Path) -> None:
        if self.pause.exists():
            return
        packet = json.loads(path.read_text(encoding="utf-8"))
        head = self.key[packet["id"]]["head"]
        try:
            row = _attempt(packet, head, self.label, self.options.sleep)
        except Exception as exc:  # one bad firing must not end the run
            self.record(packet["id"], {"error": str(exc)[:300]}, status="error")
            return
        if row is None:  # never got through the usage limit: retry on resume
            self.record(packet["id"], {"error": "rate limited"}, status="error")
        else:
            self.record(packet["id"], row)


def label_all(
    out: Path,
    *,
    label: Callable[[dict, str], dict],
    options: LabelOptions = DEFAULT_OPTIONS,
) -> int:
    """Label every undone packet in `out`. Returns 0 (finished or paused) or 130 (Ctrl-C)."""
    run = _Run(out, label, options)
    try:
        with ThreadPoolExecutor(max_workers=options.workers) as pool:
            list(pool.map(run.one, run.pending()))
    except KeyboardInterrupt:
        print("interrupted; labels written are kept. Rerun the same command to resume.")
        return 130
    if run.pause.exists():
        print(f"paused (remove {run.pause} and rerun the same command to resume)")
    return 0


def _id(packet_path: Path) -> str:
    return json.loads(packet_path.read_text(encoding="utf-8"))["id"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("packets", type=Path)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max", type=int, default=None, help="label at most N this invocation")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--name", default="B", help="labeler name; writes PACKETS_DIR/labels_<name>.jsonl"
    )
    args = parser.parse_args(argv)
    return label_all(
        args.packets,
        label=lambda packet, head: label_one(packet, head, args.model),
        options=LabelOptions(workers=args.workers, limit=args.max, name=args.name),
    )


if __name__ == "__main__":
    sys.exit(main())
