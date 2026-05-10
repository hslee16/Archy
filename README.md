# archy

[![PyPI](https://img.shields.io/pypi/v/archy.svg)](https://pypi.org/project/archy/)
[![Python](https://img.shields.io/pypi/pyversions/archy.svg)](https://pypi.org/project/archy/)
[![CI](https://github.com/hslee16/archy/actions/workflows/ci.yml/badge.svg)](https://github.com/hslee16/archy/actions/workflows/ci.yml)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/pypi/l/archy.svg)](LICENSE)

> Architectural sensor for Python codebases - keeps structure honest under AI-assisted development.

**Status:** v0.8.2. Usable today via:

| Mode | Command |
|---|---|
| Inspection | `archy graph`, `archy cycles` |
| CI governance | `archy check` (reads `archy.yaml`) |
| Transitive contracts | `archy contracts` (reads `.importlinter`; requires `archy[contracts]`) |
| One-shot score | `archy score` |
| Trended score | `archy score --record` + `archy trend` |
| MCP server | `archy mcp` |

How the score is computed and how to read it: [`docs/SCORING.md`](docs/SCORING.md). Benchmarks against pydantic, fastapi, flask, pytest, and archy-on-archy: [`docs/CASE_STUDIES.md`](docs/CASE_STUDIES.md). Design rationale and comparison with sentrux: [`docs/LEARNINGS.md`](docs/LEARNINGS.md).

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

Tarjan SCCs of size >= 2, plus self-loops (a module importing itself). Use `--strict` in CI to fail on any cycle.

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

Composite of modularity, acyclicity, depth, and equality (geometric mean). See [`docs/SCORING.md`](docs/SCORING.md) for formulas and how to interpret the breakdown.

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

### Blast radius

List internal modules that transitively depend on a given file. Useful before refactoring or removing a module.

```bash
uv run archy impact path/to/project --file app/libs/db.py
uv run archy impact path/to/project --file app/libs/db.py --file app/services/auth.py --format json
```

### Snapshot and diff (agent feedback loop)

Capture a baseline at the start of an editing session, then diff after edits to see exactly which cycles or layer rules changed. See [`docs/AGENT_LOOP.md`](docs/AGENT_LOOP.md) for the full playbook (also available via the MCP server's `loop` prompt).

```bash
uv run archy snapshot path/to/project   # writes .archy/baseline.json
# ... edit code ...
uv run archy diff path/to/project       # score deltas + added/resolved cycles & violations
```

### Run as an MCP server

Stdio transport, so AI agents can call archy directly. See [MCP server](#mcp-server-archy-mcp) below.

```bash
uv run archy mcp
```

## MCP server (`archy mcp`)

`archy mcp` exposes nine tools and one prompt to MCP-aware AI agents (Claude Code, the Anthropic API, etc.):

| Tool | Purpose |
|---|---|
| `archy_score` | Compute the four-metric score; optional `record=True` and `strict=True` for the same regression-gate behaviour the CLI offers. |
| `archy_cycles` | Find import cycles. |
| `archy_check` | Run layer rules from `archy.yaml`. |
| `archy_contracts` | Run import-linter contracts (transitive Layers, Forbidden, Independence, Protected, AcyclicSiblings). Stricter than `archy_check`; requires `archy[contracts]`. |
| `archy_trend` | Read recent score history. |
| `archy_impact` | Given changed file paths, return the modules that transitively import them (blast radius). |
| `archy_snapshot` | Capture score, cycles, and violations to `.archy/baseline.json`. Call at session start. |
| `archy_diff` | Compare current state against the snapshot; returns added/resolved cycles & violations and per-component score deltas. |
| `archy_record_baseline` | Convenience wrapper for `archy_score(record=True)`; mirrors sentrux's `session_start`. |

The server also exposes a `loop` **prompt** with the agent feedback-loop playbook (snapshot at start, impact before edit, diff after edit). Discoverable via the standard MCP `prompts/list` call. See [`docs/AGENT_LOOP.md`](docs/AGENT_LOOP.md) for the human-readable version.

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
- uses: hslee16/archy@v0.8.2
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
    rev: v0.8.2
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

**Excluding directories.** Add an optional `exclude:` list of directory basenames to skip codegen output, vendored code, etc. Each name is matched anywhere in the project tree (same mechanism as the built-in skips for `.venv`, `node_modules`, `__pycache__`):

```yaml
exclude:
  - baml_client
  - generated
```

`exclude:` applies to every analysis (`graph`, `cycles`, `score`, `check`) and the equivalent MCP tools.

**Namespace packages (`roots:`).** archy discovers packages by walking `__init__.py` files. PEP 420 namespace packages (no `__init__.py`) are invisible by default. Declare them as roots so descendants get qualified names:

```yaml
roots:
  - app           # `app/main.py` becomes `app.main`
  - src/service   # `src/service/db.py` becomes `service.db`
```

Without `roots:`, a project like `app/libs/db.py` (no `app/__init__.py`) is either skipped entirely or shows up as a top-level `libs.db`, which makes layer rules like `app.libs.**` match nothing.

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

- [ ] `archy graph` MCP tool: expose the dep graph itself for agent-side reasoning
- [ ] Score deltas (`archy_evolution`): per-component diffs vs. the last recorded run
- [ ] Call graph: second edge type alongside imports
- [ ] Design Structure Matrix (`archy dsm`)

Shipped: tree-sitter import graph, `__init__.py` re-export resolution, Tarjan cycle detection, YAML layer rules (`archy check`), composite score (`archy score`), JSONL history + `archy trend`, MCP server (`archy mcp`), GitHub Action + pre-commit hooks, blast-radius (`archy impact`), snapshot/diff agent loop (`archy snapshot` / `archy diff` + MCP `loop` prompt), import-linter contract wrap (`archy contracts`, `archy[contracts]`).

See [`docs/FUTURE.md`](docs/FUTURE.md) for the longer list and [`docs/LEARNINGS.md`](docs/LEARNINGS.md) for design notes.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for style rules. Notably: no em-dash characters (U+2014) anywhere in the repo.

## License

MIT, see [LICENSE](LICENSE).
