<!-- mcp-name: io.github.hslee16/archy -->

<p align="center"><img src="docs/assets/logo-wordmark.png" alt="archy" width="420"></p>

[![PyPI](https://img.shields.io/pypi/v/archy.svg)](https://pypi.org/project/archy/)
[![Python](https://img.shields.io/pypi/pyversions/archy.svg)](https://pypi.org/project/archy/)
[![CI](https://github.com/hslee16/archy/actions/workflows/ci.yml/badge.svg)](https://github.com/hslee16/archy/actions/workflows/ci.yml)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/pypi/l/archy.svg)](LICENSE)
[![Glama](https://glama.ai/mcp/servers/hslee16/archy/badges/score.svg)](https://glama.ai/mcp/servers/hslee16/archy)

> Architectural sensor for Python codebases - keeps structure honest under AI-assisted development.

```bash
pip install archy
archy score .         # one-shot architectural health number
archy hotspots .      # refactor priority = complexity x git churn
archy mcp             # expose 14 tools to Claude Code, Cursor, any MCP client
```

![archy demo](docs/demo.gif)

**Free, MIT licensed, no commercial version planned.** Built and maintained by [Alex Lee](https://github.com/hslee16/Archy).

**Status:** v0.19.0. Usable today via:

| Mode | Command |
|---|---|
| Inspection | `archy graph`, `archy cycles` |
| CI governance | `archy check` (reads `archy.yaml`) |
| Transitive contracts | `archy contracts` (reads `.importlinter` (canonical) or falls back to `archy.yaml`; requires `archy[contracts]`) |
| One-shot score | `archy score` |
| Trended score | `archy score --record` + `archy trend` |
| Refactor priority | `archy hotspots` (CC x git churn) |
| MCP server | `archy mcp` |

How the score is computed and how to read it: [`docs/SCORING.md`](docs/SCORING.md). Benchmarks against pydantic, fastapi, flask, pytest, and archy-on-archy: [`docs/CASE_STUDIES.md`](docs/CASE_STUDIES.md). Design rationale and comparison with sentrux: [`docs/LEARNINGS.md`](docs/LEARNINGS.md).

## In the wild

archy is used in production by the projects listed in [`ADOPTERS.md`](ADOPTERS.md). If you're running archy on a real codebase, please open a PR to add yourself, or file an issue and I'll add you.

## Why

I built archy because I kept watching coding agents generate code that passed review but rotted the import graph underneath. The score on a feature change would look fine; six weeks later the cycle count had doubled and nobody noticed until a refactor blew up. I wanted a single number per commit that would have caught it.

AI agents generate code at machine speed. Without a feedback loop on *structural* health (module coupling, import cycles, layer violations), codebases drift architecturally even when every individual change looks fine in review.

`archy` watches a Python codebase, builds a live module-dependency graph, and surfaces drift through a single trended score plus a handful of actionable sub-metrics. It's designed to run in CI, in pre-commit, and as an MCP server (`archy mcp`) so coding agents can read their own architectural impact before committing.

The agent-feedback framing is empirically supported by 2025-2026 research: the Navigation Paradox paper shows large LLM context windows do not eliminate the need for structural graph navigation, LocAgent's ablation finds graph edges materially improve code-localization accuracy, and the coding-agent failure-mode literature names the specific patterns (scope drift, cross-file reasoning failure) that an architectural feedback loop is built to catch. Citations, a failure-mode-to-archy-capability mapping, and the resulting roadmap priorities are in [`docs/RESEARCH_METRICS.md` §14c](docs/RESEARCH_METRICS.md).

## Scope

- **Python only.** The cross-language story belongs to [sentrux](https://github.com/sentrux/sentrux); that division is settled. archy goes deep on Python (transitive contracts, SDP, NCCD, `if TYPE_CHECKING:` semantics) rather than broad across languages; see [`docs/LEARNINGS.md`](docs/LEARNINGS.md) §"Competitive landscape".
- **Tree-sitter powered.** Robust to in-flight edits and partial files; survives syntax errors that would crash `ast`.
- **Score that trends over time.** A single number per commit, persisted, plotted. Trend matters more than the absolute value.
- **Rules as YAML.** "Layer X cannot import Y." No DSL, no plugins (yet).

## Non-goals

- Multi-language analysis
- Replacing linters, type checkers, or test runners
- Generating code or auto-fixing violations

## Quick start

**Requires Python 3.10+** (archy depends on `mcp>=1.27.1` which is 3.10-only). If you only have system Python 3.9 or older, install a newer Python first or use [uv](https://docs.astral.sh/uv/) which manages versions for you.

```bash
pip install archy
# or: uv tool install archy
# or: pipx install archy
```

All examples below use the installed `archy` command. If you're working from a checkout, prefix them with `uv run` (e.g. `uv run archy graph .`).

See [`docs/SIXTY_SECOND_TOUR.md`](docs/SIXTY_SECOND_TOUR.md) for the copy-paste path from zero to first score.

### Inspect the graph

```bash
archy graph path/to/project --internal-only
archy graph path/to/project --format json > graph.json
archy graph path/to/project --format dot | dot -Tsvg > graph.svg
```

### Find import cycles

Tarjan SCCs of size >= 2, plus self-loops (a module importing itself). Use `--strict` in CI to fail on any cycle.

```bash
archy cycles path/to/project
archy cycles path/to/project --format json
archy cycles path/to/project --strict
```

### Enforce layer rules

Reads `archy.yaml` from the repo root. Exits 1 on any violation. See [Layer rules](#layer-rules-archy-check) below.

```bash
archy check path/to/project
archy check path/to/project --format json
archy check path/to/project --config custom.yaml
```

### Transitive contracts (`archy contracts`)

`archy check` only sees direct edges. `archy contracts` wraps [import-linter](https://import-linter.readthedocs.io/) so the same layer story is enforced *transitively* (A → B → C still counts as A reaching C). It is the strictness upgrade for projects whose layers leak through indirect paths.

```bash
pip install 'archy[contracts]'
archy contracts path/to/project
archy contracts path/to/project --format json
```

**Config resolution.** `archy contracts` reads, in order:

1. The `--config` argument if passed.
2. `.importlinter` in the project root: the **canonical** contracts config.
3. `archy.yaml`: best-effort fallback. Each `forbid:` rule becomes one Forbidden contract checked transitively. Emits a `UserWarning` because this path cannot express `ignore_imports`, so any legitimate transitive edge (e.g., a service layer reaching `psycopg` *through* a sanctioned `app.libs.db.*` module) will be reported as a violation with no way to whitelist it.

Two configs, one concern each:

- **`archy.yaml`** owns layer definitions, direct-edge gating (`archy check`), `sdp:`, `exclude:`, and `roots:`.
- **`.importlinter`** owns transitive contracts: all five contract types (Forbidden, Layers, Independence, Protected, AcyclicSiblings) and `ignore_imports` whitelists.

Reach for `.importlinter` as soon as you need transitive enforcement at all; the archy.yaml fallback is a zero-config onramp, not a feature target. See [`.importlinter`](.importlinter) in this repo for a real-world example, and the [import-linter contract types reference](https://import-linter.readthedocs.io/en/stable/contract_types.html) for the full grammar.

Common case: forbid services from reaching `psycopg` but allow the sanctioned db library to do so:

```ini
[importlinter]
root_package = app

[importlinter:contract:services-must-not-reach-psycopg]
name = services must not reach psycopg
type = forbidden
source_modules =
    app.services
forbidden_modules =
    psycopg
ignore_imports =
    app.libs.db.engine -> psycopg
```

### Compute a quality score

Composite of modularity, acyclicity, depth, and equality (geometric mean). See [`docs/SCORING.md`](docs/SCORING.md) for formulas and how to interpret the breakdown. These four axes were chosen after surveying ~15 alternatives from the package-metrics literature (Martin's `I`/`A`/`D`, Lakos's NCCD, MacCormack propagation cost, Structure101 fat/tangle, reflexion models, cognitive complexity, hotspots, logical coupling, dead/duplicate-code detection); Martin's `I` and the Stable Dependencies Principle check are also shipped as a per-module diagnostic and an `archy check` rule. See [`docs/RESEARCH_METRICS.md`](docs/RESEARCH_METRICS.md) for the full validation, what was shipped, and what was deferred and why.

```bash
archy score path/to/project
archy score path/to/project --format json
```

### Track score over time

Persist per-commit scores to `.archy/history.jsonl` and chart the trend.

```bash
archy score path/to/project --record
archy trend path/to/project
archy trend path/to/project --last 30 --format json
```

### Regression gate

Fail if the current score drops more than `--strict-tolerance` (default 0.02) below the most recent recorded run.

```bash
archy score path/to/project --strict
archy score path/to/project --strict --record           # check then record
archy score path/to/project --strict --strict-tolerance 0.0
```

### Blast radius

List internal modules that transitively depend on a given file. Useful before refactoring or removing a module.

```bash
archy impact path/to/project --file app/libs/db.py
archy impact path/to/project --file app/libs/db.py --file app/services/auth.py --format json
```

### Snapshot and diff (agent feedback loop)

Capture a baseline at the start of an editing session, then diff after edits to see exactly which cycles or layer rules changed. See [`docs/AGENT_LOOP.md`](docs/AGENT_LOOP.md) for the full playbook (also available via the MCP server's `loop` prompt).

```bash
archy snapshot path/to/project   # writes .archy/baseline.json
# ... edit code ...
archy diff path/to/project       # score deltas + added/resolved cycles & violations
```

### Run as an MCP server

Stdio transport, so AI agents can call archy directly. See [MCP server](#mcp-server-archy-mcp) below.

```bash
archy mcp
```

## MCP server (`archy mcp`)

`archy mcp` exposes fourteen tools and one prompt to MCP-aware AI agents (Claude Code, the Anthropic API, etc.):

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
| `archy_high_risk_modules` | Top-N internal modules by `edit_risk`: geometric mean of propagation cost, normalized fan-in, and Martin's instability. Each entry breaks the composite back out. Call before a non-trivial edit to decide whether to scope down or pause for review. |
| `archy_hotspots` | Rank internal modules by `cc_sum x git-commit-count` (Tornhill / CodeScene's "Code Red"). Each entry is `{module, path, cc_sum, churn, score}`; zero-CC and zero-churn rows are filtered. `since` is passed straight to `git log --since`. Answers "where is the refactoring leverage?"; the structural cousin `archy_high_risk_modules` answers "is this edit dangerous?" without needing git. If the project isn't under git, returns an empty list plus a `note` so the agent can pivot. |

The server also exposes a `loop` **prompt** with the agent feedback-loop playbook (snapshot at start, impact before edit, diff after edit). Discoverable via the standard MCP `prompts/list` call. See [`docs/AGENT_LOOP.md`](docs/AGENT_LOOP.md) for the human-readable version.

Wire it into Claude Code (or Cursor, Windsurf, OpenCode, any MCP client) with this stanza in your config:

```json
{
  "mcpServers": {
    "archy": { "command": "archy", "args": ["mcp"] }
  }
}
```

If you're running from a checkout instead of an install, use:

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
- uses: hslee16/archy@v0.19.0
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
    rev: v0.19.0
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

- [ ] Promote call-density or per-function CC to a score axis (both ship as diagnostics in v0.16/v0.17; orthogonality vs. existing axes validated on the 27-project benchmark, see [`docs/RESEARCH_METRICS.md` §16](docs/RESEARCH_METRICS.md) and §17)
- [x] `archy hotspots = CC x per-file churn` (shipped in v0.18.0: per-file refactor-priority list from `cc_sum * commit_count` over the git history, single `git log --name-only` pass; filters zero-CC and zero-churn rows)
- [ ] Design Structure Matrix (`archy dsm`)

Shipped:

**Foundations**

- Tree-sitter import graph; `__init__.py` re-export resolution; Tarjan cycle detection.
- YAML layer rules (`archy check`); composite score (`archy score`); JSONL history + `archy trend`.
- MCP server (`archy mcp`); GitHub Action + pre-commit hooks.

**Agent loop**

- Blast-radius: `archy impact`.
- Snapshot/diff: `archy snapshot` / `archy diff` + MCP `loop` prompt.
- Import-linter contract wrap: `archy contracts`, `archy[contracts]`.
- Graph-navigation MCP tools: `archy_graph_focus`, `archy_graph_summary`, `archy_graph` (design in [`docs/SPEC_GRAPH_MCP.md`](docs/SPEC_GRAPH_MCP.md)).
- Per-module `edit_risk` composite + `archy_high_risk_modules` MCP tool: geometric mean of propagation cost, normalized fan-in, and instability; surfaced on every graph payload.

**Diagnostics**

- **v0.16, call-graph edges** as a second edge type: `kinds`, `call_lines`, `call_count` on every edge; `total_calls` / `calls_per_edge` on `archy score`; static import-alias resolution per LocAgent's invoke-edge framing.
- **v0.17, per-function cyclomatic complexity**: per-module `function_count` / `cc_sum` / `cc_max` / `cc_mean` on every internal node; project-wide `function_count` / `cc_total` / `cc_max` / `cc_mean` on `archy score`; tree-sitter McCabe walker in `src/archy/complexity.py`.
- **v0.18, `archy hotspots`**: per-file refactor-priority ranking from `cc_sum x git-commit-count`; single `git log --name-only` pass; Tornhill/CodeScene's "Code Red" formulation; filters zero-CC and zero-churn rows.

See [`docs/FUTURE.md`](docs/FUTURE.md) for the longer list and [`docs/LEARNINGS.md`](docs/LEARNINGS.md) for design notes.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for style rules. Notably: no em-dash characters (U+2014) anywhere in the repo.

## License

MIT, see [LICENSE](LICENSE).
