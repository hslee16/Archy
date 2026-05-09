# Contributing

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
