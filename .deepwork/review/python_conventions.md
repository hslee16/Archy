# Python Conventions for archy

A concise, actionable reference for reviewing Python changes. Pulled from the
existing source under `src/archy/` and `tests/`, plus tooling config in
`pyproject.toml`.

## Tooling baseline

- Target version: **Python 3.10+** (`requires-python = ">=3.10"`, ruff `target-version = "py310"`)
- Line length: **100** (ruff `line-length = 100`)
- Linter: ruff. Type checker: mypy.
- Tests: pytest (`testpaths = ["tests"]`).

## Style and structure

- Use `from __future__ import annotations` at the top of every module.
- Use **PEP 604** built-in generic / union syntax (`list[Foo]`, `str | None`),
  not `typing.List` / `Optional`. The project targets py310+.
- snake_case for functions, variables, modules. PascalCase for classes.
  Underscore-prefix (`_handle_import`) for private/helper functions.
- One concise module-level docstring at the top describing intent. Class and
  public-function docstrings only when the behavior isn't obvious from the
  signature.
- Imports: stdlib first, third-party next, local last; one blank line between
  groups. Single-line imports preferred; group `from X import a, b` only when
  related.

## Data modeling

- Prefer `@dataclass(frozen=True)` for value objects (see `ImportRef`,
  `ParseResult`). Use tuples (not lists) for sequences inside frozen
  dataclasses so they remain hashable.
- Keep dataclass fields explicit and typed. Add a docstring on the dataclass
  when fields have non-obvious semantics (e.g. relative-dot encoding).

## Filesystem and IO

- Use `pathlib.Path`, not `os.path`. Read bytes via `path.read_bytes()` for
  tree-sitter parsing (it expects bytes, not str).

## Error handling

- Tree-sitter parsing must tolerate syntax errors — surface partial results via
  `has_errors` on the result type, do not raise.
- Don't catch broad `Exception` unless re-raising or there's a specific
  documented reason.

## Tests

- Live in `tests/`, named `test_<module>.py`.
- One assertion concept per test where practical; small, focused names.
- Use plain `assert` statements; no `unittest.TestCase` boilerplate.

## Comments and docstrings

- Comments explain **why** something is the way it is — non-obvious constraints,
  tree-sitter quirks, or design decisions. They do not narrate **what** the code
  does.
- Keep comments accurate. When code changes, update or remove comments that no
  longer match.
