"""Tests for derived module headers (#428).

The header is an intervention on WHERE a fact is, so the properties that matter
are that it stays true (`--check` catches drift), that it is idempotent (a
second write is a no-op, or CI churns), and that it never eats the prose a human
wrote, which is the part archy cannot derive.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.conventions import compute_conventions
from archy.graph import discover_modules
from archy.headers import (
    MARKER,
    apply_header,
    compute_headers,
    existing_block,
    render_header,
)


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


def _wrapping_project(tmp_path: Path) -> Path:
    """A project whose `owns` field is too long to fit on one line, and the path to it.

    Both wrap regressions hid behind fixtures too small to wrap, so the fixture
    that forces it is the load-bearing part of those tests and belongs in one
    place rather than pasted into each.
    """
    root = _project(tmp_path, prose="Rule evaluation.\n")
    rules = root / "myapp" / "core" / "rules.py"
    rules.write_text(
        rules.read_text()
        + "".join(
            f"\n\ndef find_violations_of_kind_number_{i}(x):\n    return []\n" for i in range(12)
        )
    )
    return rules


def _assert_wraps(source: str) -> str:
    """The written block, having checked it actually wraps.

    Without this the wrap tests would keep passing if the fixture stopped
    wrapping, and would then be testing nothing.
    """
    block = existing_block(source)
    assert block is not None and len(block.splitlines()) > 1, "this fixture must wrap to be a test"
    return block


def _headers(root: Path):
    report = compute_conventions(root)
    return compute_headers(report, root, discover_modules(root))


def _write(root: Path):
    """`conventions --emit-headers --write` on `root`.

    The flag triple was spelled out at eight call sites, so a rename to any of
    them meant editing all eight (#451).
    """
    return CliRunner().invoke(main, ["conventions", str(root), "--emit-headers", "--write"])


def _check(root: Path):
    """`conventions --emit-headers --check` on `root`."""
    return CliRunner().invoke(main, ["conventions", str(root), "--emit-headers", "--check"])


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
    _write(root)

    after = (root / "myapp" / "core" / "rules.py").read_text()
    assert "Why this module exists." in after
    assert "archy:owns" in after


def test_write_is_idempotent(tmp_path: Path):
    """A second write that churns the file would make the CI check useless: every
    run would show a diff and nobody would read the one that mattered."""
    root = _project(tmp_path, prose="Rule evaluation.\n")
    _write(root)
    once = (root / "myapp" / "core" / "rules.py").read_text()
    _write(root)

    assert (root / "myapp" / "core" / "rules.py").read_text() == once


def test_check_passes_after_write_and_fails_on_drift(tmp_path: Path):
    root = _project(tmp_path, prose="Rule evaluation.\n")
    _write(root)

    clean = _check(root)
    assert clean.exit_code == 0

    rules = root / "myapp" / "core" / "rules.py"
    rules.write_text(rules.read_text() + "\n\ndef find_reach_violations(x):\n    return []\n")

    drifted = _check(root)
    assert drifted.exit_code == 1
    # A CI failure whose only content is "something drifted" costs the reader
    # the investigation the check was supposed to save.
    assert "myapp.core.rules" in drifted.output


def test_check_passes_after_write_when_a_header_wraps(tmp_path: Path):
    """The regression that made `--check` useless on every real module.

    `existing_block` kept only marker-prefixed lines, so every WRAPPED
    continuation was dropped and `--check` called a file stale the instant after
    `--write` produced it. Wrapping is the common case, not an edge case: most
    modules with more than a couple of public symbols exceed the width.
    """
    rules = _wrapping_project(tmp_path)

    _write(tmp_path)
    _assert_wraps(rules.read_text())

    result = _check(tmp_path)
    assert result.exit_code == 0, result.output


def test_repeated_writes_are_idempotent_when_a_header_wraps(tmp_path: Path):
    """The idempotence test above cannot catch this, because its fixture is too
    small to wrap. `apply_header` stripped marker lines only, so a wrapped
    header's continuations survived as "prose" and the SECOND write left them
    stranded above the regenerated block: the command corrupted the docstring it
    was meant to refresh. Both readers now share one definition of the block.
    """
    rules = _wrapping_project(tmp_path)
    _write(tmp_path)
    once = rules.read_text()
    _assert_wraps(once)

    _write(tmp_path)
    twice = rules.read_text()

    assert twice == once
    assert twice.count("archy:owns") == 1
    # The prose has to survive both passes, not just the first.
    assert "Rule evaluation." in twice


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


def test_headers_go_only_where_the_census_looked(tmp_path: Path):
    """A derived block in a module the census set aside states a fact about a
    population it never measured. On archy itself the first cut wrote into all
    143 modules from a census of 79, tests included."""
    root = _project(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("")
    (root / "tests" / "test_rules.py").write_text(
        "from myapp.core.rules import find_violations\n\n\n"
        "def test_it():\n    assert find_violations(1) == []\n"
    )

    modules = [h.module for h in _headers(root)]

    assert "myapp.core.rules" in modules
    assert not [m for m in modules if "test" in m], modules


def test_module_lookup_refuses_the_header_flags_instead_of_dropping_them(tmp_path: Path):
    """`--module` returns a single-module view and returns EARLY, so the header
    flags were parsed and silently discarded: `--module X --write` exited 0
    having written nothing. Someone running that in a script would believe a
    header had been written. Same failure the flag guard exists to prevent, one
    code path over."""
    root = _project(tmp_path)
    for flag in ("--write", "--check", "--emit-headers"):
        args = ["conventions", str(root), "--module", "myapp.core.rules", flag]
        if flag != "--emit-headers":
            args.append("--emit-headers")
        result = CliRunner().invoke(main, args)
        assert result.exit_code != 0, f"{flag} was silently ignored"


def test_apply_header_gives_a_docstring_to_a_module_without_one(tmp_path: Path):
    source = "import sys\n\n\ndef f():\n    return sys\n"
    out = apply_header(source, "archy:owns        f")

    assert existing_block(out) == "archy:owns        f"
    assert "import sys" in out


@pytest.mark.parametrize("prefix", ["", "r", "u", "R", "U"])
@pytest.mark.parametrize("quote", ['"', "'"])
def test_apply_header_expands_one_quote_docstrings(prefix: str, quote: str):
    literal = f"{prefix}{quote}single quoted doc{quote}"
    code = "\n\ndef sq_fn():\n    pass\n"
    source = literal + code
    assert ast.get_docstring(ast.parse(source)) == "single quoted doc"
    assert '"""' not in source and "'''" not in source
    block = "archy:owns        sq_fn"

    out = apply_header(source, block)

    assert out.startswith(f"{prefix}{quote * 3}single quoted doc\n\n")
    assert out.endswith(quote * 3 + code)
    assert existing_block(out) == block
    assert apply_header(out, block) == out


@pytest.mark.parametrize("literal", [r'"line\nnext\t\"quote\""', r"'it\'s escaped'", r'r"a\nb"'])
def test_apply_header_keeps_one_quote_escapes_as_written(literal: str):
    source = literal + "\n"
    before = ast.get_docstring(ast.parse(source), clean=False)
    assert before is not None
    assert '"""' not in source and "'''" not in source

    out = apply_header(source, "archy:owns        f")

    inner = literal[2:-1] if literal.startswith("r") else literal[1:-1]
    assert inner in out
    assert ast.get_docstring(ast.parse(out), clean=False) == before + "\n\narchy:owns        f\n"


@pytest.mark.parametrize("literal", ['("""human prose""")', "('''human prose''')"])
def test_apply_header_keeps_parenthesized_triple_quoted_docstrings(literal: str):
    source = literal + "\n"
    assert ast.get_docstring(ast.parse(source)) == "human prose"

    out = apply_header(source, "archy:owns        f")

    assert ast.get_docstring(ast.parse(out), clean=False) == "human prose\n\narchy:owns        f\n"
    assert out.endswith(")\n")
    assert apply_header(out, "archy:owns        f") == out


@pytest.mark.parametrize(
    "docstring",
    ['"single " "quoted doc"', '("single "\n    "quoted doc")'],
)
def test_apply_header_refuses_docstrings_it_cannot_isolate(docstring: str):
    source = f"{docstring}\n\ndef sq_fn():\n    pass\n"
    assert ast.get_docstring(ast.parse(source)) == "single quoted doc"
    assert '"""' not in source and "'''" not in source

    with pytest.raises(ValueError, match="docstring must be a single string literal"):
        apply_header(source, "archy:owns        sq_fn")


@pytest.mark.parametrize(
    "docstring",
    [
        '(  # "doc"\n    "doc"\n)',
        '"doc"; value = (\n    1\n)',
        '"""doc"""; import os',
    ],
)
def test_apply_header_splices_the_literal_itself(docstring: str):
    source = f"{docstring}\n\ndef sq_fn():\n    pass\n"
    assert ast.get_docstring(ast.parse(source)) == "doc"

    out = apply_header(source, "archy:owns        sq_fn")

    assert ast.get_docstring(ast.parse(out), clean=False) == "doc\n\narchy:owns        sq_fn\n"
    assert apply_header(out, "archy:owns        sq_fn") == out


def test_write_one_quote_docstring_then_check(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    target = pkg / "sq.py"
    source = '"single quoted doc"\n\ndef sq_fn():\n    pass\n'
    assert ast.get_docstring(ast.parse(source)) == "single quoted doc"
    assert '"""' not in source and "'''" not in source
    target.write_text(source)

    first = _write(tmp_path)

    assert first.exit_code == 0, first.output
    assert "wrote 1 header(s)" in first.output
    once = target.read_text()
    assert '"""single quoted doc\n' in once
    assert "def sq_fn():\n    pass\n" in once
    checked = _check(tmp_path)
    assert checked.exit_code == 0, checked.output
    second = _write(tmp_path)
    assert second.exit_code == 0, second.output
    assert "wrote 0 header(s)" in second.output
    assert target.read_text() == once


@pytest.mark.parametrize("fmt", ["text", "json"])
@pytest.mark.parametrize(
    "docstring",
    ['"single " "quoted doc"', '("single "\n    "quoted doc")'],
)
def test_write_names_refused_modules_and_continues(tmp_path: Path, fmt: str, docstring: str):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    source = f"{docstring}\n\ndef sq_fn():\n    pass\n"
    assert ast.get_docstring(ast.parse(source)) == "single quoted doc"
    assert '"""' not in source and "'''" not in source
    (pkg / "bad.py").write_text(source)
    (pkg / "good.py").write_text("def good_fn():\n    pass\n")

    result = CliRunner().invoke(
        main, ["conventions", str(tmp_path), "--emit-headers", "--write", "--format", fmt]
    )

    assert result.exit_code == 1, result.output
    reason = "docstring must be a single string literal"
    if fmt == "json":
        payload = json.loads(result.output)
        assert payload["errors"] == {"pkg.bad": reason}
        assert payload["written"] == ["pkg.good"]
    else:
        assert f"#   pkg.bad: {reason}" in result.output
        assert "wrote 1 header(s) into 2 scanned module(s)" in result.output
    assert (pkg / "bad.py").read_text() == source
    assert existing_block((pkg / "good.py").read_text()) == "archy:owns        good_fn"


def test_render_wraps_long_lists_under_the_value_column(tmp_path: Path):
    root = _project(tmp_path)
    header = next(h for h in _headers(root) if h.module == "myapp.core.rules")
    wide = header.model_copy(update={"owns": tuple(f"symbol_number_{i}" for i in range(40))})

    lines = render_header(wide).splitlines()
    assert len(lines) > 1
    # The value column starts at len("archy:") + len("mirrored-by") + 1 == 18:
    # the marker, the widest field label, and one space. Assert the COLUMN, not
    # just "starts with a space": the indent is a positive constant, so a
    # continuation starts with a space for ANY non-zero indent, and
    # `indent = " "` passed the old assertion on every test in this file (#441).
    column = len(MARKER) + max(len(f) for f in ("owns", "mirrored-by", "gates")) + 1
    assert column == 18
    assert lines[0].startswith(f"{MARKER}owns".ljust(column))
    assert lines[0][column] != " "
    continuations = [line for line in lines[1:] if not line.startswith(MARKER)]
    assert continuations, "fixture did not wrap, so the column is not under test"
    for line in continuations:
        assert line.startswith(" " * column)
        assert line[column] != " "
