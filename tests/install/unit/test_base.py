"""Unit tests for instruction upsert, apply_plan, and the detect() ladder."""

from __future__ import annotations

from pathlib import Path

import pytest

from archy.install.base import (
    INSTRUCTIONS_BEGIN,
    INSTRUCTIONS_END,
    AgentAdapter,
    FileAction,
    Scope,
    apply_plan,
    apply_uninstall,
    delete_file,
    instructions_block,
    remove_instructions,
    upsert_instructions,
)
from archy.install.writer import DryRunWriteSystem


def test_upsert_into_empty_file():
    out = upsert_instructions(None)
    assert out == instructions_block()
    assert out.count(INSTRUCTIONS_BEGIN) == 1
    assert out.count(INSTRUCTIONS_END) == 1


def test_upsert_appends_after_existing_content():
    out = upsert_instructions("# My rules\n\nKeep it tidy.\n")
    assert out.startswith("# My rules")
    assert INSTRUCTIONS_BEGIN in out


def test_upsert_is_idempotent():
    once = upsert_instructions("# rules\n")
    twice = upsert_instructions(once)
    assert once == twice


def test_upsert_replaces_existing_block_in_place():
    existing = f"top\n\n{INSTRUCTIONS_BEGIN}\nstale body\n{INSTRUCTIONS_END}\nbottom\n"
    out = upsert_instructions(existing)
    assert out.count(INSTRUCTIONS_BEGIN) == 1
    assert "stale body" not in out
    assert out.startswith("top")
    assert out.rstrip().endswith("bottom")


def test_upsert_recovers_from_truncated_block():
    existing = f"top\n{INSTRUCTIONS_BEGIN}\nhalf written"  # no END marker
    out = upsert_instructions(existing)
    assert out.count(INSTRUCTIONS_BEGIN) == 1
    assert out.count(INSTRUCTIONS_END) == 1
    assert out.startswith("top")


def test_apply_plan_routes_renders_through_write_system():
    ws = DryRunWriteSystem()
    plan = [
        FileAction(
            path=Path("/a/x.json"), kind="mcp", render=lambda _e: "X", unrender=lambda _e: ""
        ),
        FileAction(
            path=Path("/a/y.md"), kind="instructions", render=lambda _e: "Y", unrender=delete_file
        ),
    ]
    written = apply_plan(plan, ws)
    assert written == [Path("/a/x.json"), Path("/a/y.md")]
    assert [(r.path, r.content) for r in ws.records] == [
        (Path("/a/x.json"), "X"),
        (Path("/a/y.md"), "Y"),
    ]


def test_remove_instructions_strips_block_and_keeps_user_content():
    existing = f"# mine\n\n{INSTRUCTIONS_BEGIN}\nbody\n{INSTRUCTIONS_END}\n"
    out = remove_instructions(existing)
    assert out is not None
    assert INSTRUCTIONS_BEGIN not in out
    assert out.startswith("# mine")


def test_remove_instructions_returns_none_when_only_block_remains():
    assert remove_instructions(upsert_instructions(None)) is None


def test_remove_instructions_idempotent_on_clean_file():
    assert remove_instructions("# just mine\n") == "# just mine\n"


def test_apply_uninstall_strips_existing_and_deletes_owned(tmp_path):
    shared = tmp_path / "shared.txt"
    owned = tmp_path / "owned.txt"
    absent = tmp_path / "absent.txt"
    shared.write_text("keep+archy", encoding="utf-8")
    owned.write_text("archy only", encoding="utf-8")
    ws = DryRunWriteSystem()
    plan = [
        FileAction(path=shared, kind="mcp", render=lambda _e: "x", unrender=lambda _e: "keep"),
        FileAction(path=owned, kind="mcp", render=lambda _e: "x", unrender=delete_file),
        FileAction(path=absent, kind="mcp", render=lambda _e: "x", unrender=delete_file),
    ]
    touched = apply_uninstall(plan, ws)
    # apply_uninstall must tolerate files that were never written, so a partial
    # or re-run uninstall does not error on the missing ones (idempotency).
    assert touched == [shared, owned]
    assert [(r.path, r.content) for r in ws.records] == [(shared, "keep")]
    assert ws.removed == [owned]


class _Probe(AgentAdapter):
    id = "probe"
    name = "Probe"
    cli_name = "probe-cli"

    def __init__(self, root: Path):
        self._root = root

    def config_paths(self, scope: Scope, project_root: Path | None = None) -> list[Path]:
        return [self._root / "probe" / "config.json"]

    def plan(self, scope, *, project_root=None, seed_permissions=True):
        return []


def test_detect_false_when_nothing_present(simulate_os):
    fake = simulate_os("linux")
    adapter = _Probe(fake.home)
    assert adapter.detect() is False


def test_detect_true_when_config_dir_exists(simulate_os):
    fake = simulate_os("linux")
    adapter = _Probe(fake.home)
    (fake.home / "probe").mkdir(parents=True)
    assert adapter.detect() is True


def test_detect_true_when_cli_on_path(simulate_os, monkeypatch):
    from archy.install import base

    fake = simulate_os("linux")
    adapter = _Probe(fake.home)
    monkeypatch.setattr(base.shutil, "which", lambda name: "/usr/bin/probe-cli")
    assert adapter.detect() is True


@pytest.mark.parametrize("scope", [Scope.GLOBAL, Scope.LOCAL])
def test_scope_enum_values(scope):
    assert scope.value in {"global", "local"}
