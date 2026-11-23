"""`diff --strict` fails on a `required:` violation the baseline does not carry (#746).

`archy check` already fails on it, and strict already fails when the contract
is removed (#585); passing when it is newly broken let a CI ratchet miss the
regression `required:` exists for.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from archy.cli import main
from archy.diff import DiffReport, read_snapshot
from archy.mcp_runs import run_diff

_YAML = """\
required:
  - source: pkg.commands.*
    must_reach: pkg.registry
    reason: every command registers itself
"""

_REASON = "pkg.commands.b does not transitively reach 'pkg.registry'"


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "repro-required"
    for rel, text in (
        ("pkg/__init__.py", ""),
        ("pkg/registry.py", "X = 1\n"),
        ("pkg/commands/__init__.py", ""),
        ("pkg/commands/a.py", "from pkg import registry\n"),
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (root / "archy.yaml").write_text(_YAML)
    return root


def _snapshot(root: Path, out: str = "base.json") -> Path:
    result = CliRunner().invoke(
        main, ["snapshot", str(root), "--findings-only", "--out", str(root / out)]
    )
    assert result.exit_code == 0, result.output
    return root / out


def _required_rows(path: Path) -> int:
    snap = read_snapshot(path)
    assert snap is not None
    return len(snap.required_violations)


def _required_count(root: Path) -> int:
    """Required-reach rows a fresh findings-only snapshot records for the tree now."""
    path = _snapshot(root, "now.json")
    try:
        return _required_rows(path)
    finally:
        path.unlink()


def _surfaces(root: Path):
    """CLI text, CLI JSON `strict`, and MCP `archy_diff` `strict` via model_dump."""
    args = ["diff", str(root), "--strict", "--baseline", str(root / "base.json")]
    text = CliRunner().invoke(main, args)
    raw = CliRunner().invoke(main, [*args, "--format", "json"])
    assert raw.exit_code == text.exit_code, raw.output
    cli = json.loads(raw.output)["strict"]
    report = run_diff(root, baseline="base.json", strict=True)
    assert isinstance(report, DiffReport)
    return text, cli, report.model_dump()["strict"]


def test_a_new_required_violation_fails_strict_on_every_surface(tmp_path: Path):
    root = _project(tmp_path)
    _snapshot(root)
    (root / "pkg/commands/b.py").write_text("X = 1\n")
    assert _required_rows(root / "base.json") == 0
    assert _required_count(root) == 1

    text, cli, mcp = _surfaces(root)

    assert text.exit_code == 1, text.output
    assert "# strict: FAIL" in text.output
    assert _REASON in text.output
    for strict in (cli, mcp):
        assert strict["passed"] is False
        assert _REASON in strict["reason"]
        assert [f["kind"] for f in strict["findings"]] == ["required"]
        assert list(strict["findings"][0]["modules"]) == ["pkg.commands.b"]


def test_a_carried_required_violation_passes_strict(tmp_path: Path):
    root = _project(tmp_path)
    (root / "pkg/commands/b.py").write_text("X = 1\n")
    _snapshot(root)
    assert _required_rows(root / "base.json") == 1
    # An edit that adds a compliant command leaves the same one failing row.
    (root / "pkg/commands/c.py").write_text("from pkg import registry\n")
    assert _required_count(root) == 1

    text, cli, mcp = _surfaces(root)

    assert text.exit_code == 0, text.output
    assert "# strict: PASS" in text.output
    for strict in (cli, mcp):
        assert strict["passed"] is True
        assert list(strict["findings"]) == []


def test_fixing_a_carried_required_violation_passes_strict(tmp_path: Path):
    root = _project(tmp_path)
    (root / "pkg/commands/b.py").write_text("X = 1\n")
    _snapshot(root)
    (root / "pkg/commands/b.py").write_text("from pkg import registry\n")
    assert _required_count(root) == 0

    text, cli, mcp = _surfaces(root)

    assert text.exit_code == 0, text.output
    assert cli["passed"] is True
    assert mcp["passed"] is True
