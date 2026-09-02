"""Tests for derived module headers (#428).

The header is an intervention on WHERE a fact is, so the properties that matter
are that it stays true (`--check` catches drift), that it is idempotent (a
second write is a no-op, or CI churns), and that it never eats the prose a human
wrote, which is the part archy cannot derive.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from archy.cli import main
from archy.conventions import compute_conventions
from archy.graph import discover_modules
from archy.headers import apply_header, compute_headers, existing_block, render_header


def _project(tmp_path: Path, *, prose: str = "") -> Path:
    for pkg in ("myapp", "myapp/core", "myapp/cli"):
        (tmp_path / pkg).mkdir(parents=True, exist_ok=True)
        (tmp_path / pkg / "__init__.py").write_text("")
    doc = f'"""{prose}"""\n\n' if prose else ""
    (tmp_path / "myapp" / "core" / "rules.py").write_text(
        f"{doc}from __future__ import annotations\n\n\ndef find_violations(x):\n    return []\n"
    )
    (tmp_path / "myapp" / "core" / "report.py").write_text(
        "from myapp.core.rules import find_violations\n\n\n"
        "def render(x):\n    return find_violations(x)\n"
    )
    (tmp_path / "myapp" / "cli" / "run.py").write_text(
        "import sys\n\nfrom myapp.core.rules import find_violations\n\n\n"
        "def main(x):\n    if find_violations(x):\n        sys.exit(1)\n"
    )
    return tmp_path


def _headers(root: Path):
    report = compute_conventions(root)
    return compute_headers(report, root, discover_modules(root))


def test_header_names_what_the_module_owns_and_what_mirrors_it(tmp_path: Path):
    by_module = {h.module: h for h in _headers(_project(tmp_path))}

    rules = by_module["myapp.core.rules"]
    assert "find_violations" in rules.owns
    # The mirror set is DERIVED. A header asserting a relation the tree does not
    # have would be a generated lie, which is worse than no header.
    assert any("myapp.cli.run" in m and "myapp.core.report" in m for m in rules.mirrored_by)


def test_header_records_that_a_finding_here_gates(tmp_path: Path):
    by_module = {h.module: h for h in _headers(_project(tmp_path))}
    assert any("exit 1" in g for g in by_module["myapp.cli.run"].gates)


def test_modules_with_nothing_derived_get_no_header(tmp_path: Path):
    """A block on every module including the empty ones is the line a reader
    learns to skip, and then skips where it mattered."""
    (tmp_path / "myapp").mkdir()
    (tmp_path / "myapp" / "__init__.py").write_text("")
    (tmp_path / "myapp" / "nothing.py").write_text("_x = 1\n")

    assert [h.module for h in _headers(tmp_path)] == []


def test_write_preserves_prose_a_human_wrote(tmp_path: Path):
    """The prose is the part archy cannot derive. A generator that deletes it
    trades a fact it can compute for one it cannot."""
    root = _project(tmp_path, prose="Rule evaluation.\n\nWhy this module exists.\n")
    CliRunner().invoke(main, ["conventions", str(root), "--emit-headers", "--write"])

    after = (root / "myapp" / "core" / "rules.py").read_text()
    assert "Why this module exists." in after
    assert "archy:owns" in after


def test_write_is_idempotent(tmp_path: Path):
    """A second write that churns the file would make the CI check useless: every
    run would show a diff and nobody would read the one that mattered."""
    root = _project(tmp_path, prose="Rule evaluation.\n")
    args = ["conventions", str(root), "--emit-headers", "--write"]
    CliRunner().invoke(main, args)
    once = (root / "myapp" / "core" / "rules.py").read_text()
    CliRunner().invoke(main, args)

    assert (root / "myapp" / "core" / "rules.py").read_text() == once


def test_check_passes_after_write_and_fails_on_drift(tmp_path: Path):
    root = _project(tmp_path, prose="Rule evaluation.\n")
    CliRunner().invoke(main, ["conventions", str(root), "--emit-headers", "--write"])

    clean = CliRunner().invoke(main, ["conventions", str(root), "--emit-headers", "--check"])
    assert clean.exit_code == 0

    rules = root / "myapp" / "core" / "rules.py"
    rules.write_text(rules.read_text() + "\n\ndef find_reach_violations(x):\n    return []\n")

    drifted = CliRunner().invoke(main, ["conventions", str(root), "--emit-headers", "--check"])
    assert drifted.exit_code == 1
    # A CI failure whose only content is "something drifted" costs the reader
    # the investigation the check was supposed to save.
    assert "myapp.core.rules" in drifted.output


def test_check_and_write_are_refused_together(tmp_path: Path):
    root = _project(tmp_path)
    result = CliRunner().invoke(
        main, ["conventions", str(root), "--emit-headers", "--write", "--check"]
    )
    assert result.exit_code != 0


def test_write_flags_need_emit_headers(tmp_path: Path):
    """Silently ignoring a flag someone typed is how a user comes to believe
    their source was rewritten when it was not."""
    root = _project(tmp_path)
    result = CliRunner().invoke(main, ["conventions", str(root), "--write"])
    assert result.exit_code != 0


def test_apply_header_gives_a_docstring_to_a_module_without_one(tmp_path: Path):
    source = "import sys\n\n\ndef f():\n    return sys\n"
    out = apply_header(source, "archy:owns        f")

    assert existing_block(out) == "archy:owns        f"
    assert "import sys" in out


def test_render_wraps_long_lists_under_the_value_column(tmp_path: Path):
    root = _project(tmp_path)
    header = next(h for h in _headers(root) if h.module == "myapp.core.rules")
    wide = header.model_copy(update={"owns": tuple(f"symbol_number_{i}" for i in range(40))})

    lines = render_header(wide).splitlines()
    assert len(lines) > 1
    # Continuations line up under the values, never under the label, so a long
    # list still reads as one field.
    assert all(line.startswith(" ") for line in lines[1:] if not line.startswith("archy:"))
