# archy

[![PyPI](https://img.shields.io/pypi/v/archy.svg)](https://pypi.org/project/archy/)
[![Python](https://img.shields.io/pypi/pyversions/archy.svg)](https://pypi.org/project/archy/)
[![CI](https://github.com/hslee16/archy/actions/workflows/ci.yml/badge.svg)](https://github.com/hslee16/archy/actions/workflows/ci.yml)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/pypi/l/archy.svg)](LICENSE)

> Architectural sensor for Python codebases - keeps structure honest under AI-assisted development.

**Status:** v0.13.0. Usable today via:

| Mode | Command |
|---|---|
| Inspection | `archy graph`, `archy cycles` |
| CI governance | `archy check` (reads `archy.yaml`) |
| Transitive contracts | `archy contracts` (reads `.importlinter` or `archy.yaml`; requires `archy[contracts]`) |
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

### Transitive contracts (`archy contracts`)

`archy check` only sees direct edges. `archy contracts` wraps [import-linter](https://import-linter.readthedocs.io/) so the same layer story is enforced *transitively* (A → B → C still counts as A reaching C). It is the strictness upgrade for projects whose layers leak through indirect paths.

```bash
uv sync --extra contracts        # or: pip install 'archy[contracts]'
uv run archy contracts path/to/project
uv run archy contracts path/to/project --format json
```

**Config resolution.** `archy contracts` reads, in order:

1. The `--config` argument if passed.
2. `.importlinter` in the project root.
3. `archy.yaml`: each `forbid` rule becomes one Forbidden contract checked transitively. **No additional config required**: a project that already has `archy.yaml` can run `archy contracts` immediately to upgrade direct-edge enforcement to transitive.

Reach for `.importlinter` only when you need contract types `archy.yaml` does not express: Independence, Protected, AcyclicSiblings, or hand-tuned Layers. See [`.importlinter`](.importlinter) in this repo for a real-world example, and the [import-linter contract types reference](https://import-linter.readthedocs.io/en/stable/contract_types.html) for the full grammar.

### Compute a quality score

Composite of modularity, acyclicity, depth, and equality (geometric mean). See [`docs/SCORING.md`](docs/SCORING.md) for formulas and how to interpret the breakdown. These four axes were chosen after surveying ~15 alternatives from the package-metrics literature (Martin's `I`/`A`/`D`, Lakos's NCCD, MacCormack propagation cost, Structure101 fat/tangle, reflexion models, cognitive complexity, hotspots, logical coupling, dead/duplicate-code detection); Martin's `I` and the Stable Dependencies Principle check are also shipped as a per-module diagnostic and an `archy check` rule. See [`docs/RESEARCH_METRICS.md`](docs/RESEARCH_METRICS.md) for the full validation, what was shipped, and what was deferred and why.

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

`archy mcp` exposes twelve tools and one prompt to MCP-aware AI agents (Claude Code, the Anthropic API, etc.):

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
| `archy_graph_focus` | Bounded subgraph around one or more modules (qualnames or file paths). `depth` caps hops; `direction` is `in`/`out`/`both`. Each edge carries import line numbers. Use before editing for a richer view than `archy_impact`. |
| `archy_graph_summary` | Top-N modules by fan-in, fan-out, and PageRank, plus top external dependencies. Whole-project overview sized for LLM context. |
| `archy_graph` | Full dependency-graph dump matching `archy graph --format json`. Refuses graphs larger than `max_nodes` (default 500) to avoid blowing context; bump the limit explicitly when you really want everything. |

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
- uses: hslee16/archy@v0.13.0
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
    rev: v0.13.0
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

**Stability check (`sdp:`).** Optionally enable Robert Martin's Stable Dependencies Principle: a module should not import one that is *less stable* than itself. Stability is `I = Ce / (Ce + Ca)` where `Ce` is outgoing internal imports and `Ca` is incoming, so `I = 0` means "depended on, depends on nothing" (most stable) and `I = 1` means "depends on lots, nothing depends on this" (least stable).

```yaml
sdp:
  enabled: true
  tolerance: 0.0   # ignore violations within this I gap; default 0
  mode: error      # 'error' fails the gate (default); 'warn' reports but exits 0
```

When enabled, `archy check` flags every internal import edge whose target's `I` strictly exceeds the source's (plus tolerance). Per-module `I` is also surfaced in `archy graph --format json` whether or not `sdp:` is enabled, so you can audit before turning enforcement on.

**Gradual adoption.** Existing codebases will often have SDP violations on day one. Set `mode: warn` to report violations in the output (and `archy_check`'s `sdp_violations` payload) without failing the gate, then flip to `mode: error` once the count is at zero. Layer-rule violations always fail the gate regardless of `sdp.mode`.

## Development

```bash
uv sync                    # install runtime + dev deps from uv.lock
uv run ruff check          # lint
uv run ruff format         # format
uv run ty check            # type check
uv run pytest              # tests
```

One pytest case (`test_pagerank_matches_networkx_when_available`) compares archy's hand-rolled `_pagerank` against `nx.pagerank`, which needs numpy/scipy. The dependency is intentionally not in the default install (archy stays scientific-stack free); to run that test locally, sync the optional `parity` group:

```bash
uv sync --group parity     # pulls in numpy + scipy for the parity test
uv run pytest              # the test now runs instead of being skipped
```

## Roadmap

Next up:

- [ ] Call graph: second edge type alongside imports
- [ ] Design Structure Matrix (`archy dsm`)

Shipped: tree-sitter import graph, `__init__.py` re-export resolution, Tarjan cycle detection, YAML layer rules (`archy check`), composite score (`archy score`), JSONL history + `archy trend`, MCP server (`archy mcp`), GitHub Action + pre-commit hooks, blast-radius (`archy impact`), snapshot/diff agent loop (`archy snapshot` / `archy diff` + MCP `loop` prompt), import-linter contract wrap (`archy contracts`, `archy[contracts]`), graph-navigation MCP tools (`archy_graph_focus`, `archy_graph_summary`, `archy_graph`; design in [`docs/SPEC_GRAPH_MCP.md`](docs/SPEC_GRAPH_MCP.md)).

See [`docs/FUTURE.md`](docs/FUTURE.md) for the longer list and [`docs/LEARNINGS.md`](docs/LEARNINGS.md) for design notes.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for style rules. Notably: no em-dash characters (U+2014) anywhere in the repo.

## License

MIT, see [LICENSE](LICENSE).
