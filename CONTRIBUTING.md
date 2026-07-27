# Contributing

## Community Compact

**Read this first: archy is in maintenance as of 2026-07-27.** Feature work has
stopped, deliberately, after its premise was measured four times and came back
rare each time. The reasoning is in [`README.md`](README.md) and in full in
[`docs/WHAT_DIDNT_WORK.md`](docs/WHAT_DIDNT_WORK.md). This does not close the
project to contributions, but it does change what you should expect, so it is
said here rather than discovered after you have spent a weekend.

What the maintainer commits to:

- Respond to new issues within a week (best effort; one person, day job).
- Review pull requests, including feature PRs. Maintenance means I am not
  *driving* new features; it does not mean I will reject yours unread.
- Keep CI green and the released package working.
- No surprise re-licensing. archy is MIT and intended to stay that way. Any change would be announced in advance with rationale; released versions stay under their original license.
- No commercial-feature gating in the OSS package. If a hosted version of archy ever exists, the OSS package will never be the lesser product.

What the maintainer no longer commits to:

- A forward roadmap. [`docs/ROADMAP.md`](docs/ROADMAP.md) and
  [`docs/FUTURE.md`](docs/FUTURE.md) are kept as a record of what was
  considered and why, and are explicitly marked closed. Several items in them
  rest on a claim that has since been retracted.
- Building requested features myself, on any timeline.

What contributors are asked to do:

- **Open an issue before a large PR.** This matters more now, not less: a
  scope-check costs a comment, and I would rather say "that rests on a premise I
  retracted, here is the study" before you write it than after.
- Follow the style rules below (notably the no-em-dash rule; CI does not yet enforce it but reviewers will).
- Write tests for new behavior, especially anything graph-shape or score-affecting.

The `good first issue` tickets are real, deliberately left open, and a genuinely
good place to start. Several have already been landed by people who are not me.

For governance details (decision process, how this evolves with multiple committers, right-to-fork) see [`GOVERNANCE.md`](GOVERNANCE.md). For conduct expectations see [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## Style

### No em dashes

Do not use the em-dash character (Unicode codepoint U+2014) anywhere in
the repository. Not in code, comments, docstrings, commit messages, PR
descriptions, or documentation. Use a regular hyphen-minus (`-`) instead,
or restructure the sentence, or use a colon, semicolon, or parentheses.

This rule applies uniformly to:

- Markdown files (`README.md`, `docs/*.md`, etc.)
- Python source and tests
- YAML config (`archy.yaml`, CI workflows)
- Commit messages and PR bodies

CI does not currently enforce this. The local DeepWork review setup flags
violations. To search for any em-dash in the repo without embedding the
forbidden character in the command itself:

```bash
# Python: scans every text file under . and lists those containing U+2014.
python3 -c "from pathlib import Path; [print(p) for p in Path('.').rglob('*') if p.is_file() and chr(0x2014) in p.read_text(errors='ignore')]"

# zsh / bash 4.2+: $'\u2014' expands to the em-dash codepoint at runtime.
grep -rn $'\u2014' --include='*.md' --include='*.py' \
  --include='*.yaml' --include='*.yml' --include='*.toml' .
```

### Before you push

Run what CI runs, not a subset:

```bash
uv run ruff check          # no path argument
uv run ruff format --check
uv run ty check
uv run pytest
uv run archy check .       # archy enforces its own layer rules
uv run archy cycles . --strict
```

`ruff check some/file.py` is a different command from `ruff check` and will pass
while CI fails. The last two commands are archy checking itself: a new fixture
or sample tree can introduce a real cycle or layer violation, and the fix is to
add it to `exclude:` in `archy.yaml` with a note saying why, not to weaken a
rule.

### If review finds something in code you did not touch

File it as its own issue rather than widening your PR (precedent: #317, #318,
#335). It keeps your diff reviewable, and the finding does not get lost. Check
the tracker first, since someone may already be on it.

### Other conventions

See `.deepwork/review/python_conventions.md` (local-only) for the broader
Python style notes the review tooling enforces. The short version:

- `from __future__ import annotations` at the top of every module.
- PEP 604 type syntax (`list[X]`, `str | None`).
- `pathlib.Path` over `os.path`.
- Frozen dataclasses for value objects; tuples (not lists) for their
  sequence fields.
- ruff line length 100, target py310.
- Comments explain *why*, not *what*. If a comment merely narrates the
  code, delete it.
