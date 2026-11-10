"""The `score`, `cycles` and `trend` commands."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import (
    main,
)
from tests.cli_helpers import (
    _DELETED_NOTE,
    _git,
    _make_acyclic_project,
    _make_cyclic_project,
    _make_guarded_project,
    _make_many_module_project,
    _make_project_with_generated_dir,
    _make_ratchet_project,
    _make_three_module_project,
    _repo_with_deleted_module,
    _seed_history,
)


@pytest.mark.parametrize("args", [["cycles"], ["graph", "--internal-only"]])
def test_graph_commands_default_to_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
):
    project = _make_cyclic_project(tmp_path)
    monkeypatch.chdir(project)
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert "pkg.a" in result.output
    assert "pkg.b" in result.output


def test_cycles_text_output_lists_cycle(tmp_path: Path):
    project = _make_cyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project)])
    assert result.exit_code == 0
    assert "1 cycle(s) found" in result.output
    assert "pkg.a" in result.output
    assert "pkg.b" in result.output


def test_cycles_text_output_when_clean(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project)])
    assert result.exit_code == 0
    assert "No cycles found" in result.output


def test_cycles_json_output_is_valid_json(tmp_path: Path):
    project = _make_cyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert len(payload) == 1
    cycle = payload[0]
    assert sorted(cycle["modules"]) == ["pkg.a", "pkg.b"]
    edge_pairs = {(e["source"], e["target"]) for e in cycle["edges"]}
    assert edge_pairs == {("pkg.a", "pkg.b"), ("pkg.b", "pkg.a")}


def test_cycles_strict_exits_nonzero_when_cycles_present(tmp_path: Path):
    project = _make_cyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project), "--strict"])
    assert result.exit_code == 1


def test_cycles_strict_exits_zero_when_clean(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project), "--strict"])
    assert result.exit_code == 0


def test_cycles_min_size_filters_smaller_cycles(tmp_path: Path):
    project = _make_cyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project), "--min-size", "3"])
    assert result.exit_code == 0
    assert "No cycles found" in result.output


def test_cycles_min_size_rejects_invalid_values(tmp_path: Path):
    project = _make_cyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project), "--min-size", "0"])
    assert result.exit_code != 0
    assert "--min-size must be >= 1; got 0" in result.output


def test_score_text_output(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["score", str(project)])
    assert result.exit_code == 0
    assert "archy score:" in result.output
    assert "modularity:" in result.output
    assert "acyclicity:" in result.output
    assert "depth:" in result.output
    assert "equality:" in result.output
    assert "complexity:" in result.output


def test_score_json_output(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["score", str(project), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "overall" in payload
    assert set(payload["components"]) == {
        "modularity",
        "acyclicity",
        "depth",
        "equality",
        "complexity",
    }
    assert 0.0 <= payload["overall"] <= 1.0
    for v in payload["components"].values():
        assert 0.0 <= v <= 1.0


def test_score_drops_when_cycle_introduced(tmp_path: Path):
    (tmp_path / "clean").mkdir()
    (tmp_path / "cyclic").mkdir()
    clean_project = _make_acyclic_project(tmp_path / "clean")
    cyclic_project = _make_cyclic_project(tmp_path / "cyclic")
    runner = CliRunner()
    clean = json.loads(
        runner.invoke(main, ["score", str(clean_project), "--format", "json"]).output
    )
    cyclic = json.loads(
        runner.invoke(main, ["score", str(cyclic_project), "--format", "json"]).output
    )
    assert cyclic["components"]["acyclicity"] < clean["components"]["acyclicity"]
    assert cyclic["inputs"]["cycle_count"] == 1
    assert clean["inputs"]["cycle_count"] == 0


def test_score_record_writes_history_row(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    history_path = project / ".archy" / "history.jsonl"
    assert not history_path.exists()
    result = CliRunner().invoke(main, ["score", str(project), "--record"])
    assert result.exit_code == 0
    assert history_path.exists()
    [line] = history_path.read_text().splitlines()
    payload = json.loads(line)
    assert "score" in payload
    assert "overall" in payload["score"]


def test_score_without_record_does_not_write_history(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    history_path = project / ".archy" / "history.jsonl"
    result = CliRunner().invoke(main, ["score", str(project)])
    assert result.exit_code == 0
    assert not history_path.exists()


def test_trend_empty_history(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["trend", str(project)])
    assert result.exit_code == 0
    assert "No archy score history" in result.output


def test_trend_after_recording(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["score", str(project), "--record"])
    runner.invoke(main, ["score", str(project), "--record"])
    result = runner.invoke(main, ["trend", str(project)])
    assert result.exit_code == 0
    assert "last 2 of 2" in result.output
    assert "score" in result.output


def test_trend_last_rejects_invalid_values(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["trend", str(project), "--last", "0"])
    assert result.exit_code != 0
    assert "--last must be >= 1; got 0" in result.output


def test_score_strict_with_no_history_passes(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["score", str(project), "--strict"])
    assert result.exit_code == 0
    assert "no prior score recorded" in result.output


def test_score_strict_passes_when_unchanged(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["score", str(project), "--record"])
    result = runner.invoke(main, ["score", str(project), "--strict"])
    assert result.exit_code == 0
    assert "strict: PASS" in result.output


def test_score_strict_fails_when_score_drops_beyond_tolerance(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    _seed_history(project, overall=0.95)
    result = CliRunner().invoke(
        main,
        ["score", str(project), "--strict", "--strict-tolerance", "0.05"],
    )
    assert result.exit_code == 1
    assert "strict: FAIL" in result.output


def test_score_strict_within_tolerance_passes(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    _seed_history(project, overall=0.95)
    result = CliRunner().invoke(
        main,
        ["score", str(project), "--strict", "--strict-tolerance", "1.0"],
    )
    assert result.exit_code == 0
    assert "strict: PASS" in result.output


def test_score_strict_tolerance_rejects_out_of_range_values(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(
        main,
        ["score", str(project), "--strict", "--strict-tolerance", "-0.5"],
    )
    assert result.exit_code != 0
    assert "--strict-tolerance must be in [0, 1]; got -0.5" in result.output


def test_score_strict_passes_when_score_improves(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    _seed_history(project, overall=0.10)
    result = CliRunner().invoke(
        main,
        ["score", str(project), "--strict", "--strict-tolerance", "0.0"],
    )
    assert result.exit_code == 0
    assert "strict: PASS" in result.output
    assert "improved" in result.output


def test_score_strict_compares_before_recording(tmp_path: Path):
    # `--strict --record` together must compare against the existing last row,
    # not against the row about to be appended (which would always tie at 0).
    project = _make_acyclic_project(tmp_path)
    _seed_history(project, overall=0.95)
    history_path = project / ".archy" / "history.jsonl"
    rows_before = len(history_path.read_text().splitlines())
    result = CliRunner().invoke(
        main,
        [
            "score",
            str(project),
            "--strict",
            "--strict-tolerance",
            "0.0",
            "--record",
        ],
    )
    # `--record` appends unconditionally; the gate verdict is independent.
    assert result.exit_code == 1
    rows_after = len(history_path.read_text().splitlines())
    assert rows_after == rows_before + 1


def test_score_strict_json_includes_gate_block(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["score", str(project), "--record"])
    result = runner.invoke(
        main,
        ["score", str(project), "--strict", "--format", "json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "gate" in payload
    assert payload["gate"]["passed"] is True
    assert payload["gate"]["tolerance"] == 0.02


def test_trend_json_output(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["score", str(project), "--record"])
    result = runner.invoke(main, ["trend", str(project), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert "overall" in payload[0]["score"]


def test_exclude_silences_cycles_inside_generated_dir(tmp_path: Path):
    project = _make_project_with_generated_dir(tmp_path)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\nexclude: [baml_client]\n")
    result = CliRunner().invoke(main, ["cycles", str(project), "--strict"])
    assert result.exit_code == 0


def test_affected_stdin_accepts_git_root_paths_from_subdirectory(tmp_path: Path):
    repo = _repo_with_deleted_module(tmp_path, subdir="services/api")
    project = repo / "services/api"
    changed = "services/api/pkg/a.py"
    _git(repo, "restore", changed)
    (repo / changed).write_text("x = 2\n")
    assert not (project / changed).exists()
    assert (repo / changed).exists()
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert diff == changed + "\n"

    args = ["affected", str(project), "--stdin", "--quiet"]
    result = CliRunner().invoke(main, args, input=diff)
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [str(project / "tests/test_a.py")]
    assert "deleted or renamed" not in result.output

    _git(repo, "rm", "-f", changed)
    deleted = CliRunner().invoke(main, args, input=diff)
    assert deleted.exit_code == 0, deleted.output
    assert deleted.stdout == ""
    assert _DELETED_NOTE in deleted.stderr
    assert changed in deleted.stderr


def test_score_errors_on_oversized_scan(tmp_path: Path):
    project = _make_many_module_project(tmp_path, 5)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\nmax_modules: 2\n")
    result = CliRunner().invoke(main, ["score", str(project)])
    assert result.exit_code != 0
    assert "max_modules" in result.output
    assert "exclude" in result.output


def test_score_max_modules_zero_disables_guard(tmp_path: Path):
    project = _make_many_module_project(tmp_path, 5)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\nmax_modules: 0\n")
    result = CliRunner().invoke(main, ["score", str(project)])
    assert result.exit_code == 0


def test_score_default_limit_does_not_trip_small_project(tmp_path: Path):
    # No archy.yaml -> the default ceiling (10k) applies and a tiny project passes.
    project = _make_many_module_project(tmp_path, 5)
    result = CliRunner().invoke(main, ["score", str(project)])
    assert result.exit_code == 0


def test_render_trend_reads_history(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    CliRunner().invoke(main, ["score", str(project), "--record"])
    result = CliRunner().invoke(main, ["render", str(project), "--view", "trend"])
    assert result.exit_code == 0
    assert "<h2>overall</h2>" in result.output


def test_render_trend_without_history_still_succeeds(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["render", str(project), "--view", "trend"])
    assert result.exit_code == 0
    assert "archy score --record" in result.output


def test_render_rejects_invalid_last(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["render", str(project), "--view", "trend", "--last", "0"])
    assert result.exit_code != 0
    assert "--last" in result.output


def test_plain_diff_stays_exit_zero_on_a_new_cycle(tmp_path: Path):
    """Without --strict the exit code does not change: the gate is opt-in."""
    project = _make_ratchet_project(tmp_path)
    (project / "pkg" / "d.py").write_text("import pkg.e\n")
    (project / "pkg" / "e.py").write_text("import pkg.d\n")

    result = CliRunner().invoke(
        main, ["diff", str(project), "--baseline", str(project / "base.json")]
    )

    assert result.exit_code == 0
    assert "# strict:" not in result.output
    assert "score deltas: not available" in result.output


@pytest.mark.parametrize(
    ("command", "option"),
    [
        ("render", "--out"),
        ("snapshot", "--out"),
        ("diff", "--baseline"),
        ("dsm", "--diff"),
    ],
)
def test_file_path_option_given_a_directory_is_a_clean_error(
    tmp_path: Path, command: str, option: str
):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "mod.py").write_text("x = 1\n")
    folder = tmp_path / "somedir"
    folder.mkdir()

    # The real entry point, for the same reason as the malformed-baseline test:
    # CliRunner would hide a Traceback in Result.exception.
    proc = subprocess.run(
        [str(Path(sys.executable).parent / "archy"), command, str(project), option, str(folder)],
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout + proc.stderr

    assert proc.returncode == 2
    assert "Traceback" not in output
    assert option in output
    assert str(folder) in output
    assert "is a directory" in output


def test_cycles_exclude_type_checking_json_is_an_object_with_the_count(tmp_path: Path):
    project = _make_guarded_project(tmp_path)
    args = ["cycles", str(project), "--format", "json", "--exclude-type-checking"]
    payload = json.loads(CliRunner().invoke(main, args).output)
    assert payload == {"cycles": [], "excluded_type_checking_edges": 1}


def test_cycles_archy_yaml_key_turns_exclusion_on_and_the_flag_overrides_it(tmp_path: Path):
    project = _make_guarded_project(tmp_path)
    (project / "archy.yaml").write_text("exclude_type_checking_imports: true\n")
    on = CliRunner().invoke(main, ["cycles", str(project), "--strict"])
    assert on.exit_code == 0
    off = CliRunner().invoke(main, ["cycles", str(project), "--strict", "--include-type-checking"])
    assert off.exit_code == 1
