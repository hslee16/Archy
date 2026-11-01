"""Install with --target all must not half-write when a later client fails (#591)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.install.base import Scope, render_plan, write_rendered
from archy.install.runner import resolve_targets, run_install
from archy.install.writer import InstallError, RealWriteSystem


def _tree_snapshot(root: Path) -> dict[str, str]:
    """Relative path -> text for every file under root (stable for before/after)."""
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = path.read_text(encoding="utf-8")
    return out


def test_install_all_two_bad_configs_names_both_and_writes_nothing(simulate_os):
    fake = simulate_os("linux")
    cursor = fake.home / ".cursor" / "mcp.json"
    cursor.parent.mkdir(parents=True, exist_ok=True)
    cursor.write_text("{ not json\n", encoding="utf-8")
    codex = fake.home / ".codex" / "config.toml"
    codex.parent.mkdir(parents=True, exist_ok=True)
    codex.write_text("[[[\n", encoding="utf-8")
    before = _tree_snapshot(fake.home)

    # Reach the multi-error branch via the library API (same path as CLI).
    with pytest.raises(InstallError) as excinfo:
        run_install(resolve_targets("all"), Scope.GLOBAL, write_system=RealWriteSystem())

    message = str(excinfo.value)
    assert "cursor:" in message
    assert "JSON decode error" in message
    assert "codex:" in message
    assert "TOML decode error" in message
    assert before == _tree_snapshot(fake.home)

    result = CliRunner().invoke(main, ["install", "--target", "all", "-y"])
    assert result.exit_code == 1
    assert "cursor:" in result.output
    assert "codex:" in result.output
    assert "Traceback" not in result.output
    assert before == _tree_snapshot(fake.home)


def test_install_all_one_bad_config_leaves_others_unwritten(simulate_os):
    fake = simulate_os("linux")
    cursor = fake.home / ".cursor" / "mcp.json"
    cursor.parent.mkdir(parents=True, exist_ok=True)
    cursor.write_text("{ not json\n", encoding="utf-8")
    before = _tree_snapshot(fake.home)

    with pytest.raises(InstallError) as excinfo:
        run_install(resolve_targets("all"), Scope.GLOBAL, write_system=RealWriteSystem())

    message = str(excinfo.value)
    assert "cursor:" in message
    assert "JSON decode error" in message
    # Good clients must not have been written before the failure.
    assert not (fake.home / ".claude.json").exists()
    assert not (fake.home / ".claude" / "settings.json").exists()
    assert before == _tree_snapshot(fake.home)


def test_render_plan_then_write_is_apply_plan(simulate_os):
    """Sanity: separable render/write composes to apply_plan for a good client."""
    fake = simulate_os("linux")
    adapters = resolve_targets("cursor")
    from archy.install.runner import plan_for

    plan = plan_for(adapters[0], Scope.GLOBAL, project_root=None, seed_permissions=True)
    ws = RealWriteSystem()
    pairs = render_plan(plan, ws)
    assert pairs
    written = write_rendered(pairs, ws)
    assert (fake.home / ".cursor" / "mcp.json").exists()
    assert written
