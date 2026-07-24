"""Unit tests for the deterministic core of the agent-footprint bench (#259).

`parse_transcript` and `summarize` are pure over a session `.jsonl` and need no
live agent. `_reset_repo` and `_baseline_failed` need only a throwaway git repo,
and are covered here because a silent failure in either invalidates a whole run
(a repo that does not reset means run i+1 measures run i's edits; a baseline
measured on the wrong tree state disables the regression gate). Only the parts
that shell out to `claude` (`run_variant` / `run_pair`) are left to the live
bench, never CI. See docs/SPEC_AGENT_FOOTPRINT_BENCH.md sections 4-5 for the
metric definitions these assertions pin.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# bench/ is not a package; append it (not insert(0)) so its generic module
# names cannot shadow stdlib/installed imports, mirroring tests/test_delta_direction.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import agent_footprint as af  # ty: ignore[unresolved-import]  (added to sys.path above)


def _assistant(
    usage: dict, tool_uses: list[tuple[str, str | None]], msg_id: str | None = None
) -> dict:
    """A synthetic Claude Code transcript `assistant` line.

    `msg_id` sets `message.id`; lines sharing an id are streaming partials of one
    logical message and must be deduped (usage counted once).
    """
    content: list[dict] = []
    for name, file_path in tool_uses:
        block: dict = {"type": "tool_use", "id": f"toolu_{name}", "name": name, "input": {}}
        if file_path is not None:
            block["input"]["file_path"] = file_path
        content.append(block)
    message: dict = {"role": "assistant", "content": content, "usage": usage}
    if msg_id is not None:
        message["id"] = msg_id
    return {"type": "assistant", "message": message}


def _user_tool_result(text: str = "ok") -> dict:
    # User/tool_result lines carry no usage and no tool_use; the parser must skip
    # them for token sums so they cannot inflate the footprint.
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "tool_result", "content": text}]},
    }


def _write_transcript(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(entry) for entry in lines) + "\n", encoding="utf-8")


def _sample_transcript(path: Path) -> None:
    """A 4-turn run with a known-by-hand set of expected metrics.

    Sequence of file touches:
      m1 Read a.py     (a not yet written -> no revisit)
      m2 Edit a.py     (a not yet written -> no revisit; a now written)
      m3 Read a.py     (a written -> REVISIT #1) + Write b.py (new -> no revisit; b written)
      m4 Edit b.py     (b written -> REVISIT #2)
    Plus a Grep and a Bash that must NOT count as file touches, and a
    user/system line that must NOT contribute tokens.
    """
    _write_transcript(
        path,
        [
            {"type": "system", "subtype": "init"},
            _assistant(
                {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 1000,
                    "cache_creation_input_tokens": 200,
                },
                [("Read", "a.py"), ("Grep", None)],
            ),
            _user_tool_result(),
            _assistant(
                {
                    "input_tokens": 30,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 1100,
                    "cache_creation_input_tokens": 0,
                },
                [("Edit", "a.py")],
            ),
            _user_tool_result(),
            _assistant(
                {
                    "input_tokens": 40,
                    "output_tokens": 25,
                    "cache_read_input_tokens": 1150,
                    "cache_creation_input_tokens": 0,
                },
                [("Read", "a.py"), ("Write", "b.py"), ("Bash", None)],
            ),
            _user_tool_result(),
            _assistant(
                {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 1200,
                    "cache_creation_input_tokens": 0,
                },
                [("Edit", "b.py")],
            ),
        ],
    )


def test_parse_transcript_token_sums(tmp_path: Path):
    t = tmp_path / "session.jsonl"
    _sample_transcript(t)
    parsed = af.parse_transcript(t)
    # Summed across the four assistant messages only (user/system skipped).
    assert parsed.input_tokens == 100 + 30 + 40 + 10  # 180
    assert parsed.output_tokens == 50 + 20 + 25 + 5  # 100
    assert parsed.cache_read_input_tokens == 1000 + 1100 + 1150 + 1200
    assert parsed.cache_creation_input_tokens == 200
    assert parsed.assistant_messages == 4
    # Headline footprint excludes cache tokens (spec section 5).
    assert parsed.footprint_tokens == 280


def test_parse_transcript_file_metrics(tmp_path: Path):
    t = tmp_path / "session.jsonl"
    _sample_transcript(t)
    parsed = af.parse_transcript(t)
    # a.py and b.py; Grep/Bash have no file_path so they do not count.
    assert parsed.distinct_files_touched == 2
    # Two revisits: m3 Read a.py (a already edited), m4 Edit b.py (b already written).
    assert parsed.file_revisitations == 2


def test_parse_transcript_pre_edit_metrics(tmp_path: Path):
    # #289 reads-before-first-edit (spec section 14.3). First edit is m2 Edit a.py,
    # so the prefix is m1 only: Read a.py + Grep = 2 exploratory reads, 1 distinct
    # file (Grep has no file_path). pre_edit_input_tokens spans up to and including
    # the turn the first edit lands in: m1 (100) + m2 (30) = 130.
    t = tmp_path / "session.jsonl"
    _sample_transcript(t)
    parsed = af.parse_transcript(t)
    assert parsed.made_edit is True
    assert parsed.pre_edit_reads == 2
    assert parsed.pre_edit_distinct_files == 1
    assert parsed.pre_edit_input_tokens == 130


def test_pre_edit_metrics_when_no_edit_is_made(tmp_path: Path):
    # A run that explores but never edits: the whole transcript is the prefix, and
    # made_edit is False so the run is reported separately, never in the median.
    t = tmp_path / "session.jsonl"
    _write_transcript(
        t,
        [
            _assistant(
                {"input_tokens": 12, "output_tokens": 4}, [("Read", "a.py"), ("Grep", None)]
            ),
            _assistant({"input_tokens": 8, "output_tokens": 3}, [("Read", "b.py"), ("Glob", None)]),
        ],
    )
    parsed = af.parse_transcript(t)
    assert parsed.made_edit is False
    assert parsed.pre_edit_reads == 4  # 2 Read + 1 Grep + 1 Glob
    assert parsed.pre_edit_distinct_files == 2  # a.py, b.py (Grep/Glob have no path)
    assert parsed.pre_edit_input_tokens == 20  # whole run, since no edit ever lands


def test_first_write_is_not_a_revisit(tmp_path: Path):
    # A file read once and edited once, never returned to, is zero revisitations.
    t = tmp_path / "session.jsonl"
    _write_transcript(
        t,
        [
            _assistant({"input_tokens": 5, "output_tokens": 5}, [("Read", "x.py")]),
            _assistant({"input_tokens": 5, "output_tokens": 5}, [("Edit", "x.py")]),
        ],
    )
    parsed = af.parse_transcript(t)
    assert parsed.distinct_files_touched == 1
    assert parsed.file_revisitations == 0


def test_path_normalization_collapses_equivalent_paths(tmp_path: Path):
    t = tmp_path / "session.jsonl"
    _write_transcript(
        t,
        [
            _assistant({"input_tokens": 1, "output_tokens": 1}, [("Write", "pkg/a.py")]),
            _assistant({"input_tokens": 1, "output_tokens": 1}, [("Edit", "pkg/../pkg/a.py")]),
        ],
    )
    parsed = af.parse_transcript(t)
    assert parsed.distinct_files_touched == 1  # same file after normpath
    assert parsed.file_revisitations == 1  # the second edit is a revisit


def test_malformed_lines_are_skipped(tmp_path: Path):
    t = tmp_path / "session.jsonl"
    good = json.dumps(_assistant({"input_tokens": 7, "output_tokens": 3}, [("Read", "a.py")]))
    t.write_text(f"not json\n\n{good}\n{{ partial", encoding="utf-8")
    parsed = af.parse_transcript(t)
    assert parsed.input_tokens == 7
    assert parsed.assistant_messages == 1


def test_streaming_duplicate_messages_counted_once(tmp_path: Path):
    # Regression for the real-transcript bug: Claude Code writes several
    # streaming partials of one assistant message (same message.id, identical
    # usage), with the tool_use only on the final copy. Summing raw lines
    # triple-counts tokens; dedup by id must recover the true per-message total.
    t = tmp_path / "session.jsonl"
    usage = {"input_tokens": 10, "output_tokens": 192}
    _write_transcript(
        t,
        [
            _assistant(usage, [], msg_id="msg_1"),  # streamed text partial
            _assistant(usage, [], msg_id="msg_1"),  # duplicate
            _assistant(usage, [("Read", "a.py")], msg_id="msg_1"),  # final, with the tool
            _assistant({"input_tokens": 8, "output_tokens": 57}, [], msg_id="msg_2"),
            _assistant({"input_tokens": 8, "output_tokens": 57}, [], msg_id="msg_2"),  # duplicate
        ],
    )
    parsed = af.parse_transcript(t)
    assert parsed.assistant_messages == 2  # two logical messages, not five lines
    assert parsed.input_tokens == 10 + 8  # counted once per id
    assert parsed.output_tokens == 192 + 57
    assert parsed.distinct_files_touched == 1  # the Read on the final copy is seen once
    assert parsed.file_revisitations == 0


def _record(variant: str, run_index: int, **over) -> af.FootprintRecord:
    base = dict(
        variant=variant,
        run_index=run_index,
        model="test-model",
        input_tokens=100,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        output_tokens=50,
        num_turns=5,
        distinct_files_touched=4,
        file_revisitations=3,
        pre_edit_reads=8,
        pre_edit_distinct_files=5,
        pre_edit_input_tokens=1200,
        made_edit=True,
        brief_tokens=0,
        duration_ms=1000,
        total_cost_usd=0.01,
        task_completed=True,
        test_regression=False,
    )
    base.update(over)
    return af.FootprintRecord(**base)


def test_summarize_paired_deltas(tmp_path: Path):
    # Two paired runs; B is consistently lighter than A on footprint + revisits.
    records = [
        _record("A", 0, input_tokens=200, output_tokens=100, file_revisitations=6),
        _record("B", 0, input_tokens=150, output_tokens=80, file_revisitations=3),
        _record("A", 1, input_tokens=220, output_tokens=90, file_revisitations=5),
        _record("B", 1, input_tokens=160, output_tokens=70, file_revisitations=2),
    ]
    summary = af.summarize(records)
    assert summary["n_pairs"] == 2
    fp = summary["metrics"]["footprint_tokens"]
    # A: 300, 310 ; B: 230, 230 ; deltas: -70, -80 ; median -75.
    assert fp["median_delta"] == -75
    assert fp["treatment_lower_count"] == 2 and fp["treatment_higher_count"] == 0
    assert summary["metrics"]["file_revisitations"]["median_delta"] == -3
    assert summary["regressions"] == 0


def test_summarize_pairs_arm_c_on_pre_edit_reads(tmp_path: Path):
    # #289 arm C (A vs C): treatment is "C", and pre_edit_reads is the metric of
    # record. C reads less before its first edit in both pairs; one no-edit run is
    # counted separately, never folded into a median.
    records = [
        _record("A", 0, pre_edit_reads=12),
        _record("C", 0, pre_edit_reads=7, brief_tokens=300),
        _record("A", 1, pre_edit_reads=10),
        _record("C", 1, pre_edit_reads=6, brief_tokens=300, made_edit=False),
    ]
    summary = af.summarize(records)
    per = summary["metrics"]["pre_edit_reads"]
    # deltas C-A: -5, -4 ; median -4.5 ; C lower in both pairs.
    assert per["median_delta"] == -4.5
    assert per["treatment_lower_count"] == 2 and per["treatment_higher_count"] == 0
    assert summary["no_edit_runs"] == 1


def _git_repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one committed file, for the reset/baseline tests."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "t")
    (repo / "kept.txt").write_text("pristine\n")
    run("add", "-A")
    run("commit", "-qm", "init")
    return repo


def test_reset_repo_restores_pristine_state(tmp_path: Path) -> None:
    """Spec section 8's fresh checkout: run i's edits must not survive into run i+1."""
    repo = _git_repo(tmp_path)
    (repo / "kept.txt").write_text("edited by the agent\n")
    (repo / "new_file.py").write_text("# agent-created\n")
    (repo / "subdir").mkdir()
    (repo / "subdir" / "nested.txt").write_text("also new\n")

    af._reset_repo(repo)

    assert (repo / "kept.txt").read_text() == "pristine\n"
    assert not (repo / "new_file.py").exists()
    assert not (repo / "subdir").exists()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    )
    assert status.stdout.strip() == ""


def test_baseline_failed_reflects_the_suite_and_resets_first(tmp_path: Path) -> None:
    """A red pristine suite must be detected, and measured on the reset tree."""
    repo = _git_repo(tmp_path)
    # Dirty the tree first: a baseline measured on leftover edits is the bug this
    # guards against, so the helper must reset before it runs the command.
    (repo / "kept.txt").write_text("leftover\n")

    # `true`/`false` are not executables on Windows, and this suite runs there.
    ok = [sys.executable, "-c", "raise SystemExit(0)"]
    red = [sys.executable, "-c", "raise SystemExit(1)"]

    assert af._baseline_failed(repo, ok) is False
    assert (repo / "kept.txt").read_text() == "pristine\n"
    assert af._baseline_failed(repo, red) is True
    # No test command means no gate, and nothing to report as red.
    assert af._baseline_failed(repo, None) is False


def test_sign_test_p_matches_the_exact_binomial():
    # Ties carry no direction, so they leave n rather than splitting it: 0 lower /
    # 8 higher / 2 tied is n=8, p = 2*C(8,0)/2**8. This is the #282 headline cell.
    assert af.sign_test_p([1.0] * 8 + [0.0, 0.0]) == pytest.approx(0.0078125)
    # 9-1 clears 0.05, 8-2 does not: the power arithmetic the N=10 plan rests on.
    assert af.sign_test_p([-1.0] * 9 + [1.0]) == pytest.approx(0.021484375)
    assert af.sign_test_p([-1.0] * 8 + [1.0, 1.0]) == pytest.approx(0.109375)
    # All-tied and empty carry no evidence either way.
    assert af.sign_test_p([0.0, 0.0]) == 1.0
    assert af.sign_test_p([]) == 1.0


def _pair_records(deltas: list[int]) -> list[af.FootprintRecord]:
    """One A/B pair per delta, differing only in `pre_edit_reads` (A fixed at 10)."""
    records = []
    for i, d in enumerate(deltas):
        records.append(_record("A", i, pre_edit_reads=10))
        records.append(_record("B", i, pre_edit_reads=10 + d))
    return records


def test_summarize_reports_everything_a_published_table_needs():
    # The #282 writeup was assembled by a separate script and shipped a wrong
    # headline; the fix is that summarize() itself carries p, IQR and both
    # per-variant medians, so a table can be rendered without recomputation.
    summary = af.summarize(_pair_records([-3, -3, -4, 2, -5, -1, 2, -9, -4, 1]))
    per = summary["metrics"]["pre_edit_reads"]
    assert per["sign_p"] == pytest.approx(af.sign_test_p(per["deltas"]))
    assert per["control_median"] == 10
    bounds = per["iqr_bounds"]
    assert bounds is not None and bounds[0] <= per["median_delta"] <= bounds[1]
    # The Bonferroni divisor must be the count of what was tested, not of what
    # someone chose to publish.
    assert summary["metrics_tested"] == len(summary["metrics"])


def test_results_table_renders_every_tested_metric():
    summary = af.summarize(_pair_records([-1, -2, 3]))
    table = af.results_table(summary)
    for metric in summary["metrics"]:
        assert f"| {metric} |" in table
    assert f"{summary['metrics_tested']} metrics tested" in table
    assert "gate disabled" in table


def test_per_variant_medians_ignore_half_finished_pairs():
    # A crashed run can leave an unmatched row; folding it into one arm's median
    # but not the other's would silently bias the published table.
    records = _pair_records([-2, -2])
    orphan = records[0].model_copy(update={"run_index": 99, "pre_edit_reads": 100})
    summary = af.summarize([*records, orphan])
    assert summary["n_pairs"] == 2
    assert summary["metrics"]["pre_edit_reads"]["control_median"] == 10


def test_provenance_leak_warning_fires_on_author_mismatch(tmp_path, capsys):
    # `git commit --amend` keeps the original author without --reset-author, which
    # is exactly how the #282 variant-B commit leaked that the repo was
    # instrumented despite a rewritten message.
    a = _git_repo(tmp_path / "a")
    b = _git_repo(tmp_path / "b")
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "refactor: split the class"],
        cwd=b,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "archy bench", "GIT_AUTHOR_EMAIL": "b@invalid"},
    )
    af._warn_on_provenance_leak(a, b)
    assert "leaks that B is instrumented" in capsys.readouterr().err


def test_no_provenance_warning_when_b_matches_upstream_author_and_date(tmp_path, capsys):
    """The remedy the spec prescribes must actually silence the author/date check."""
    a = _git_repo(tmp_path / "a")
    b = _git_repo(tmp_path / "b")
    a_head = af._head_identity(a)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "refactor: split the class"],
        cwd=b,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_AUTHOR_DATE": a_head["date"]},
    )
    verdict = af._warn_on_provenance_leak(a, b)
    assert verdict["leaks"] == []
    # The subject is still echoed, because only a human can judge whether it
    # describes the treatment; that note is not a leak finding.
    assert "leaks that B is instrumented" not in capsys.readouterr().err


def test_published_282_table_is_exactly_what_the_harness_renders():
    """The #282 writeup must be byte-identical to `results_table()` output.

    This is the test that would have caught the shipped-wrong-headline bug: that
    table was assembled by an ad-hoc script whose `footprint_tokens` definition
    had drifted from the harness property. Comparing the committed records to the
    committed prose catches both a drifting harness and a hand-edited table.
    """
    root = Path(__file__).resolve().parent.parent
    records = af.load_records(root / "bench" / "agent_footprint" / "records_282_flask.jsonl")
    rendered = af.results_table(af.summarize(records))
    published = (root / "bench" / "agent_footprint_results.md").read_text(encoding="utf-8")
    assert rendered in published


def test_run_pair_counterbalances_within_pair_order(monkeypatch, tmp_path: Path):
    """A must not lead every pair, or variant is confounded with position."""
    calls: list[tuple[str, int]] = []

    def fake_run_variant(repo_dir, task_prompt, *, variant, run_index, **kwargs):
        calls.append((variant, run_index))
        return _record(variant, run_index)

    monkeypatch.setattr(af, "run_variant", fake_run_variant)
    monkeypatch.setattr(af, "_baseline_failed", lambda *a, **k: False)
    monkeypatch.setattr(af, "_warn_on_provenance_leak", lambda *a, **k: {"leaks": []})

    af.run_pair(tmp_path / "a", tmp_path / "b", "task", n=4, model="m", artifact_dir=tmp_path)

    assert [v for v, _ in calls] == ["A", "B", "B", "A", "A", "B", "B", "A"]
    # Pairing must survive the reordering: each run_index still has one of each.
    assert sorted(calls) == [("A", i) for i in range(4)] + [("B", i) for i in range(4)]


def test_per_variant_medians_use_only_paired_runs(tmp_path: Path):
    """An unmatched row must not shift one arm's published median."""
    # A-side values 10, 10 paired; the orphan B row would drag B's median if the
    # filter were absent, so the assertion fails without `_paired_run_indices`.
    records = _pair_records([-2, -2])
    orphan = _record("B", 99, pre_edit_reads=1000)
    summary = af.summarize([*records, orphan])
    assert summary["n_pairs"] == 2
    assert summary["metrics"]["pre_edit_reads"]["treatment_median"] == 8
    assert summary["metrics"]["pre_edit_reads"]["control_median"] == 10


def test_summarize_reports_the_interval_and_the_treatment_arm():
    # Spec section 9 wants an interval alongside the median, and an arm-C table
    # must not render under a "B" heading.
    summary = af.summarize([_record("A", 0, pre_edit_reads=12), _record("C", 0, pre_edit_reads=7)])
    assert summary["treatment"] == "C"
    assert "| C median |" in af.results_table(summary)
    # Inclusive quartiles stay inside the observed data; the exclusive default
    # extrapolates past it for tiny samples.
    two = af.summarize(_pair_records([0, 10]))["metrics"]["pre_edit_reads"]
    # Exclusive quartiles would give [-2.5, +12.5]: an interval containing values
    # larger than anything observed. Inclusive stays within the data.
    assert two["iqr_bounds"] == [2.5, 7.5]


def test_provenance_check_catches_a_date_only_leak(tmp_path: Path):
    """Matching the author but not the date still leaks: `git log -1` prints it."""
    a = _git_repo(tmp_path / "a")
    b = _git_repo(tmp_path / "b")
    # Author identity matches A exactly (the remedy as commonly applied), but the
    # commit is dated the run day, which `git log -1` prints without any flags.
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "refactor: split the base class"],
        cwd=b,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_AUTHOR_DATE": "2026-07-24T12:00:00+00:00",
        },
    )
    verdict = af.check_variant_provenance(a, b)
    assert any("author date" in leak for leak in verdict["leaks"])
    # The subject is surfaced for human judgement rather than keyword-matched.
    assert verdict["variant_b_subject"] == "refactor: split the base class"


def test_provenance_check_does_not_crash_outside_git(tmp_path: Path):
    """A diagnostic must never be the thing that aborts a paid multi-hour run."""
    plain = tmp_path / "plain"
    plain.mkdir()
    verdict = af.check_variant_provenance(plain, plain)
    assert verdict["leaks"] == []
    assert verdict["variant_b_subject"] == ""
