# archy

> Architectural sensor for Python codebases - keeps structure honest under AI-assisted development.

**Status:** v0.4.0. Usable today as a CLI for inspection (`archy graph`, `archy cycles`), CI governance (`archy check` against an `archy.yaml`), one-shot scoring (`archy score`), trended scoring over time (`archy score --record` + `archy trend`), AND as an MCP server (`archy mcp`) so AI agents can call archy as a structural sensor in their own feedback loop. See [`docs/CASE_STUDIES.md`](docs/CASE_STUDIES.md) for benchmarks against pydantic, fastapi, flask, pytest, and the dogfooded archy-on-archy run. The score follows sentrux's design (modularity, acyclicity, depth, equality, geometric mean); see [`docs/LEARNINGS.md`](docs/LEARNINGS.md) for the side-by-side comparison.

## Why

AI agents generate code at machine speed. Without a feedback loop on *structural* health - module coupling, import cycles, layer violations - codebases drift architecturally even when every individual change looks fine in review.

`archy` is a small tool that watches a Python codebase, builds a live module-dependency graph, and surfaces drift through a single trended score plus a handful of actionable sub-metrics. It's designed to run in CI, in pre-commit, and as an MCP server (`archy mcp`) so coding agents can read their own architectural impact before committing.

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

# Find import cycles (Tarjan SCCs of size >= 2)
uv run archy cycles path/to/your/python/project
uv run archy cycles path/to/your/python/project --format json
uv run archy cycles path/to/your/python/project --strict   # exit 1 if any cycles

# Enforce layer rules from archy.yaml; exit 1 on any violation
uv run archy check path/to/your/python/project
uv run archy check path/to/your/python/project --format json
uv run archy check path/to/your/python/project --config custom.yaml

# Composite quality score (modularity * acyclicity * depth * equality, geometric mean)
uv run archy score path/to/your/python/project
uv run archy score path/to/your/python/project --format json

# Persist scores over time and chart the trend
uv run archy score path/to/your/python/project --record
uv run archy trend path/to/your/python/project
uv run archy trend path/to/your/python/project --last 30 --format json

# CI gate: fail if score drops more than 0.02 below the most recent recorded run
uv run archy score path/to/your/python/project --strict
uv run archy score path/to/your/python/project --strict --record  # check then record
uv run archy score path/to/your/python/project --strict --strict-tolerance 0.0

# Run archy as an MCP server on stdio so AI agents can call it directly
uv run archy mcp
```

### MCP server (`archy mcp`)

`archy mcp` exposes five tools to MCP-aware AI agents (Claude Code,
the Anthropic API, etc.):

| Tool | Purpose |
|---|---|
| `archy_score` | Compute the four-metric score; optional `record=True` and `strict=True` for the same regression-gate behaviour the CLI offers. |
| `archy_cycles` | Find import cycles. |
| `archy_check` | Run layer rules from `archy.yaml`. |
| `archy_trend` | Read recent score history. |
| `archy_record_baseline` | Convenience wrapper for `archy_score(record=True)`; mirrors sentrux's `session_start`. |

Wire it into Claude Code with this stanza in your config:

```json
{
  "mcpServers": {
    "archy": { "command": "uv", "args": ["run", "archy", "mcp"] }
  }
}
```

`--strict` reads the last row from `.archy/history.jsonl` and compares the
current score against it. Drops beyond the tolerance fail with exit code 1.
The default tolerance (0.02) matches the threshold sentrux's `gate` uses.
This gives archy parity with sentrux's regression-gate use case while
keeping the long-term JSONL history for `archy trend`.

### Layer rules (`archy check`)

Drop an `archy.yaml` at the repo root declaring layers and forbidden directions:

```yaml
layers:
  domain:
    modules:
      - "myapp.domain.**"
  application:
    modules:
      - "myapp.application.**"
  infra:
    modules:
      - "myapp.infra.**"
      - "myapp.adapters.**"

forbid:
  - {from: domain, to: application}
  - {from: domain, to: infra}
  - {from: application, to: infra}
```

Patterns are dotted-name globs: `*` matches one segment, `**` matches zero
or more. `myapp.domain.**` covers the package itself and every descendant.
Modules must belong to at most one layer. `archy check` discovers
`archy.yaml` from PATH upward unless `--config` is given; exits 1 on
violation.

archy enforces its own architecture this way - see [`archy.yaml`](archy.yaml)
at the repo root and the `archy check .` step in `.github/workflows/ci.yml`.

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
- [x] `__init__.py` re-export resolution
- [x] Cycle detection (Tarjan SCC)
- [x] Layer/boundary rules from YAML config (`archy check`)
- [x] Single-score computation (`archy score`) - four sub-metrics, geometric mean
- [x] Per-commit JSONL history + `archy trend` - sparkline + last-N table
- [x] MCP server (`archy mcp`) - five tools an AI agent can call
- [ ] Pre-commit hook + GitHub Action

See [`docs/FUTURE.md`](docs/FUTURE.md) for the longer list and [`docs/LEARNINGS.md`](docs/LEARNINGS.md) for design notes.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for style rules. Notably: no em-dash characters (U+2014) anywhere in the repo.

## License

MIT - see [LICENSE](LICENSE).
