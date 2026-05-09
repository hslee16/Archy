# archy

> Architectural sensor for Python codebases - keeps structure honest under AI-assisted development.

**Status:** v0.4.1. Usable today via:

| Mode | Command |
|---|---|
| Inspection | `archy graph`, `archy cycles` |
| CI governance | `archy check` (reads `archy.yaml`) |
| One-shot score | `archy score` |
| Trended score | `archy score --record` + `archy trend` |
| MCP server | `archy mcp` |

Benchmarks against pydantic, fastapi, flask, pytest, and archy-on-archy live in [`docs/CASE_STUDIES.md`](docs/CASE_STUDIES.md). Score design follows sentrux (modularity, acyclicity, depth, equality, geometric mean); see [`docs/LEARNINGS.md`](docs/LEARNINGS.md).

## Why

AI agents generate code at machine speed. Without a feedback loop on *structural* health (module coupling, import cycles, layer violations), codebases drift architecturally even when every individual change looks fine in review.

`archy` watches a Python codebase, builds a live module-dependency graph, and surfaces drift through a single trended score plus a handful of actionable sub-metrics. It's designed to run in CI, in pre-commit, and as an MCP server (`archy mcp`) so coding agents can read their own architectural impact before committing.

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
```

### Inspect the graph

```bash
uv run archy graph path/to/project --internal-only
uv run archy graph path/to/project --format json > graph.json
uv run archy graph path/to/project --format dot | dot -Tsvg > graph.svg
```

### Find import cycles

Tarjan SCCs of size >= 2. Use `--strict` in CI to fail on any cycle.

```bash
uv run archy cycles path/to/project
uv run archy cycles path/to/project --format json
uv run archy cycles path/to/project --strict
```

### Enforce layer rules

Reads `archy.yaml` from the repo root. Exits 1 on any violation. See [Layer rules](#layer-rules-archy-check) below.

```bash
uv run archy check path/to/project
uv run archy check path/to/project --format json
uv run archy check path/to/project --config custom.yaml
```

### Compute a quality score

Composite of modularity, acyclicity, depth, and equality (geometric mean).

```bash
uv run archy score path/to/project
uv run archy score path/to/project --format json
```

### Track score over time

Persist per-commit scores to `.archy/history.jsonl` and chart the trend.

```bash
uv run archy score path/to/project --record
uv run archy trend path/to/project
uv run archy trend path/to/project --last 30 --format json
```

### Regression gate

Fail if the current score drops more than `--strict-tolerance` (default 0.02) below the most recent recorded run.

```bash
uv run archy score path/to/project --strict
uv run archy score path/to/project --strict --record           # check then record
uv run archy score path/to/project --strict --strict-tolerance 0.0
```

### Run as an MCP server

Stdio transport, so AI agents can call archy directly. See [MCP server](#mcp-server-archy-mcp) below.

```bash
uv run archy mcp
```

## MCP server (`archy mcp`)

`archy mcp` exposes five tools to MCP-aware AI agents (Claude Code, the Anthropic API, etc.):

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

### Regression-gate semantics

`--strict` reads the last row from `.archy/history.jsonl` and compares the current score against it. Drops beyond the tolerance fail with exit code 1. The default tolerance (0.02) matches the threshold sentrux's `gate` uses. This gives archy parity with sentrux's regression-gate use case while keeping the long-term JSONL history for `archy trend`.

## CI integration

### GitHub Action

archy ships a composite action you can drop into any workflow:

```yaml
- uses: hslee16/archy@v0.4.1
  with:
    command: score      # score | check | cycles
    path: .
    strict: "true"      # fail on regression (score) or any cycle (cycles)
```

Inputs (all optional unless noted):

| Input | Default | Notes |
|---|---|---|
| `command` | `score` | `score`, `check`, or `cycles` |
| `path` | `.` | Project root to analyze |
| `strict` | `true` | `score`/`cycles`: fail on regression / any cycle |
| `strict-tolerance` | `0.02` | `score --strict` tolerance |
| `record` | `false` | `score`: append result to `.archy/history.jsonl` |
| `config` | (auto) | `check`: path to `archy.yaml` |
| `python-version` | `3.10` | Python to install |

### Pre-commit hook

Add to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/hslee16/archy
    rev: v0.4.1
    hooks:
      - id: archy-check          # layer rules from archy.yaml
      - id: archy-score-strict   # regression gate against last recorded score
      - id: archy-cycles         # fail on any import cycle
```

`archy-score-strict` reads `.archy/history.jsonl`; commit a baseline first with `archy score . --record`.

## Layer rules (`archy check`)

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

**Pattern syntax.** Dotted-name globs: `*` matches one segment, `**` matches zero or more. `myapp.domain.**` covers the package itself and every descendant. Modules must belong to at most one layer.

**Discovery.** `archy check` walks PATH upward to find `archy.yaml` unless `--config` is given. Exits 1 on violation.

archy enforces its own architecture this way; see [`archy.yaml`](archy.yaml) at the repo root and the `archy check .` step in `.github/workflows/ci.yml`.

## Development

```bash
uv sync                    # install runtime + dev deps from uv.lock
uv run ruff check          # lint
uv run ruff format         # format
uv run ty check            # type check
uv run pytest              # tests
```

## Roadmap

Next up:

- [ ] `archy_impact` MCP tool: blast-radius analysis for a set of changed files
- [ ] `archy graph` MCP tool: expose the dep graph itself for agent-side reasoning
- [ ] Score deltas (`archy_evolution`): per-component diffs vs. the last recorded run
- [ ] Design Structure Matrix (`archy dsm`)

Shipped: tree-sitter import graph, `__init__.py` re-export resolution, Tarjan cycle detection, YAML layer rules (`archy check`), composite score (`archy score`), JSONL history + `archy trend`, MCP server (`archy mcp`), GitHub Action + pre-commit hooks.

See [`docs/FUTURE.md`](docs/FUTURE.md) for the longer list and [`docs/LEARNINGS.md`](docs/LEARNINGS.md) for design notes.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for style rules. Notably: no em-dash characters (U+2014) anywhere in the repo.

## License

MIT, see [LICENSE](LICENSE).
