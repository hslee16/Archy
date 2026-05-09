# archy

> Architectural sensor for Python codebases - keeps structure honest under AI-assisted development.

**Status:** v0.2.0. Usable today for inspection (`archy graph`, `archy cycles`), CI governance (`archy check` against an `archy.yaml`), and one-shot scoring (`archy score`) - see [`docs/CASE_STUDIES.md`](docs/CASE_STUDIES.md) for benchmarks against pydantic, fastapi, flask, pytest, and the dogfooded archy-on-archy run. The score follows sentrux's design (modularity, acyclicity, depth, equality, geometric mean); see [`docs/LEARNINGS.md`](docs/LEARNINGS.md) for the side-by-side comparison. **Not yet** a *trended* scoring tool - that's the next milestone (per-commit JSONL history + `archy trend`).

## Why

AI agents generate code at machine speed. Without a feedback loop on *structural* health - module coupling, import cycles, layer violations - codebases drift architecturally even when every individual change looks fine in review.

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
```

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
- [ ] Per-commit JSONL history + `archy trend`
- [ ] Pre-commit hook + GitHub Action
- [ ] MCP server

See [`docs/FUTURE.md`](docs/FUTURE.md) for the longer list and [`docs/LEARNINGS.md`](docs/LEARNINGS.md) for design notes.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for style rules. Notably: no em-dash characters (U+2014) anywhere in the repo.

## License

MIT - see [LICENSE](LICENSE).
