# archy

> Architectural sensor for Python codebases — keeps structure honest under AI-assisted development.

**Status:** early scaffold. Not yet usable.

## Why

AI agents generate code at machine speed. Without a feedback loop on *structural* health — module coupling, import cycles, layer violations — codebases drift architecturally even when every individual change looks fine in review.

`archy` is a small tool that watches a Python codebase, builds a live module-dependency graph, and surfaces drift through a single trended score plus a handful of actionable sub-metrics. It's designed to run in CI, in pre-commit, and (eventually) as an MCP server so coding agents can read their own architectural impact before committing.

## Scope

- **Python only.** The cross-language story is deliberately someone else's problem.
- **Tree-sitter powered.** Robust to in-flight edits and partial files; survives syntax errors that would crash `ast`.
- **Score that trends over time.** A single number per commit, persisted, plotted. Trend matters more than the absolute value.
- **Rules as YAML.** "Layer X cannot import Y." No DSL, no plugins (yet).

## Non-goals

- Multi-language analysis
- Replacing linters, type checkers, or test runners
- Generating code or auto-fixing violations

## Quick start

```bash
uv sync
uv run archy graph path/to/your/python/project --internal-only
uv run archy graph path/to/your/python/project --format json > graph.json
uv run archy graph path/to/your/python/project --format dot | dot -Tsvg > graph.svg
```

### Development

```bash
uv sync                    # install runtime + dev deps from uv.lock
uv run ruff check          # lint
uv run ruff format         # format
uv run ty check            # type check
uv run pytest              # tests
```

## Roadmap

- [x] Tree-sitter-based import graph
- [ ] Cycle detection (Tarjan SCC)
- [ ] Layer/boundary rules from YAML config
- [ ] Single-score computation + JSONL history
- [ ] CLI: `archy check`, `archy score`, `archy trend`
- [ ] Pre-commit hook + GitHub Action
- [ ] MCP server

See [`docs/FUTURE.md`](docs/FUTURE.md) for the longer list and [`docs/LEARNINGS.md`](docs/LEARNINGS.md) for design notes.

## License

MIT — see [LICENSE](LICENSE).
