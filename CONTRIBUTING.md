# Contributing

## Community Compact

**Read this first: archy is active, and pointed at the loop rather than the
surface area.** It spent 2026-07-27 to 2026-09-02 in maintenance, deliberately,
after its premise was measured four times and came back rare each time. It
reopened on 2026-09-02 to test one more population, coding agents running a
small model on local hardware ([#408](https://github.com/hslee16/archy/issues/408)).
That question is now **closed, as six interventions, none positive**, two of
them measured as costing more than their control (Study 8 in
[`docs/WHAT_DIDNT_WORK.md`](docs/WHAT_DIDNT_WORK.md#study-8-pushing-the-fact-at-the-model-costs-more-twice)).
The project stayed active; the banner in [`README.md`](README.md) says where it
went instead.

This changes what you should expect, so it is said here rather than discovered
after you have spent a weekend: **work goes deeper into the loop archy already
serves, not wider.** In practice that means correctness of the answers archy
already gives: a lookup that matched nothing must refuse instead of answering a
zero, a check that could not have failed must not report like one that held,
every verdict carries its reason on every surface, and tests must be able to
fail. Most open issues are of that kind, and the ones labelled
[`good first issue`](https://github.com/hslee16/archy/labels/good%20first%20issue)
and
[`good second issue`](https://github.com/hslee16/archy/labels/good%20second%20issue)
are written to be picked up cold. New tools and new output formats stay gated
behind a usage signal, so a PR that adds one is still reviewed, but the honest
word for it is "contribution accepted", not "roadmap item".

What the maintainer commits to:

- Respond to new issues within a week (best effort; one person, day job).
- Review pull requests, including feature PRs. A narrow focus means I am not
  *driving* features outside it; it does not mean I will reject yours unread.
- Keep CI green and the released package working.
- No surprise re-licensing. archy is MIT and intended to stay that way. Any change would be announced in advance with rationale; released versions stay under their original license.
- No commercial-feature gating in the OSS package. If a hosted version of archy ever exists, the OSS package will never be the lesser product.

What the maintainer no longer commits to:

- A general forward roadmap. [`docs/ROADMAP.md`](docs/ROADMAP.md) and
  [`docs/FUTURE.md`](docs/FUTURE.md) stay closed and are kept as a record of
  what was considered and why; several items in them rest on a claim that has
  since been retracted, and reopening the project did not reopen them. What is
  planned lives in the open issues, not in a roadmap file.
- Building requested features myself, on any timeline.

What contributors are asked to do:

- **Open an issue before a large PR.** This matters more now, not less: a
  scope-check costs a comment, and I would rather say "that rests on a premise I
  retracted, here is the study" before you write it than after.
- Follow the style rules below (notably the no-em-dash rule; CI does not yet enforce it but reviewers will).
- Write tests for new behavior, especially anything graph-shape or score-affecting.
- **Check a new test can actually fail**, by changing the code it covers and
  confirming it goes red. A test that asserts something true regardless of the
  implementation is worse than no test: it reads as coverage. Sixteen such tests
  were found here in one audit, two of them hiding a live bug. `AGENTS.md` has
  the three shapes they take and `bench/mutate.py` checks in bulk.

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
uv run archy conventions . --emit-headers --check   # derived headers still true
```

`ruff check some/file.py` is a different command from `ruff check` and will pass
while CI fails. The last three commands are archy checking itself: a new fixture
or sample tree can introduce a real cycle or layer violation, and the fix is to
add it to `exclude:` in `archy.yaml` with a note saying why, not to weaken a
rule. The headers check fails when a public symbol or a mirror set changes
without the derived block being regenerated; run `uv run archy conventions .
--emit-headers --write` to regenerate it, and never hand-edit a block, because
the next regeneration discards the edit.

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
- Frozen pydantic models (`model_config = ConfigDict(frozen=True)`) for
  value objects; tuples (not lists) for their sequence fields (see `AGENTS.md`
  on `@computed_field` and `model_dump()`).
- ruff line length 100, target py310.
- Comments explain *why*, not *what*. If a comment merely narrates the
  code, delete it.
