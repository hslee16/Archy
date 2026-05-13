# Learnings

Running notes from building archy. Updated as we go.

## v0.0.1 - import graph

### Tree-sitter Python API (post-0.23)

The modern API is genuinely cleaner than the 0.21 era.

```python
import tree_sitter_python as tsp
from tree_sitter import Language, Parser, Query, QueryCursor

PY = Language(tsp.language())
parser = Parser(PY)
tree = parser.parse(source_bytes)

query = Query(PY, "(import_statement) @i")
cursor = QueryCursor(query)
captures = cursor.captures(tree.root_node)  # dict[name, list[Node]]
```

Three things that tripped me up:

1. **Field-typed captures were noisier than capturing whole statements and walking fields.** I started by capturing `(import_statement name: (dotted_name) @abs_module)` plus several variants for aliased/from/relative imports. The grouping back into "one statement" got ambiguous fast - there was no way to tell which `@abs_module` belonged to which statement when one statement imported multiple names. Switching to "capture the whole `import_statement` / `import_from_statement` node, then use `node.children_by_field_name('name')` and `node.child_by_field_name('module_name')`" was simpler and survives every form.

2. **`from X import a, b` is genuinely ambiguous statically.** `a` could be a name in `X`'s namespace or a submodule. Without semantic analysis (which would mean executing `X/__init__.py`), you have to guess. The pragmatic rule: when `X` is internal, prefer submodule edges where they exist; otherwise attribute the edge to `X` itself. Sentrux's plugin config doesn't actually disambiguate this either - it just records `module_name` and lets the resolver figure it out.

3. **Error recovery is the headline feature.** Tree-sitter produces a partial tree on syntax errors with `ERROR` and `MISSING` nodes. The query still runs; clean imports still come through. Surfaced via `tree.root_node.has_error` and propagated as `ParseResult.has_errors` and the graph's `parse_errors` list. This was the whole reason to pick tree-sitter over `ast` and it works exactly as advertised.

### Package discovery: `src/` layout matters

A directory is a "package root" when it contains `__init__.py` AND its parent does not. That single rule covers both flat layouts and the `src/<pkg>/__init__.py` convention sentrux's Python plugin documents - `src` itself isn't a package, so `src/myapp` becomes the root and module qualnames look right (`myapp.core`, not `src.myapp.core`).

### Relative imports: dot count semantics

`from . import x` means "stay in current package." `from .. import x` means "go up one." So the walk-up count is `leading_dots - 1`. Off-by-one footgun; got it wrong on the first pass and the test for relative imports caught it.

### What "external" means in this graph

We collapse external imports to their top-level package: `import requests.adapters` becomes an edge to `requests`. This matches sentrux's behavior and keeps the external surface area tractable. Trade-off: we lose granularity inside third-party packages, but we don't care about their internal structure for our metrics.

### Sentrux's quality-signal design

Their `docs/quality-signal-design.md` is excellent. The five root-cause metrics aren't arbitrary - they're presented as the five independent structural properties of a directed graph (modularity, acyclicity, depth, equality, redundancy). The argument for **geometric mean** as the aggregator is the strongest part: it's the unique aggregation function satisfying Pareto optimality + symmetry + independence, which means the only way to game the score is to actually improve every dimension. That's the property worth preserving when archy gets to scoring. (Empirical caveat from the v0.7.x rollout: `docs/SCORING.md` §Empirical axis independence finds 4 of 6 axis pairs at moderate Pearson correlation on the 23-project benchmark, so "independent" holds in design but not strictly in measurement; all six pairs still sit below the OECD `|r| > 0.7` redundancy threshold.)

We will likely not match their full metric set in v1 - redundancy in particular requires AST-level dead-code and duplicate-function detection that's a lot more work than the import graph. Modularity (Newman's Q), acyclicity (Tarjan SCC count), and depth (longest-path DAG) are all derivable from the graph we already build. Those three plus a fan-out concentration metric (Gini of out-degrees, since we don't yet have per-function CC) get us most of the way without leaving graph-theory land.

### Performance note

The real-world test was the governingdocs backend: 665 internal modules, 356 internal edges, runs in well under a second cold. Tree-sitter parses are fast; the bottleneck is filesystem traversal. No optimization needed yet.

## v0.2.0 - score: comparison with sentrux

archy's score follows sentrux's [`quality-signal-design.md`](https://github.com/sentrux/sentrux/blob/main/docs/quality-signal-design.md) closely - same model, same aggregation, four of the five sub-metrics implemented identically. Sentrux is pure Rust; archy is Python on top of `networkx`. Sub-metric formulas match where they should:

| Sub-metric | sentrux | archy v0.2.0 | Notes |
|---|---|---|---|
| Modularity | Newman's Q over greedy partition; `(Q + 0.5) / 1.5` mapped onto `[0, 1]` | identical (clamped to `[0, 1]` after the linear map) | We adopted sentrux's normalization explicitly so cross-tool numbers stay comparable. |
| Acyclicity | `1 / (1 + cycle_count)` from Tarjan SCC of size > 1 | **diverged in v0.7.x**: `1 - tangle_ratio` where `tangle_ratio = nodes_in_cycles / total_nodes`. Same SCC source via `archy.cycles.find_cycles`. | The new form follows Structure101's "Tangle" metric and reads as fraction-of-codebase-in-cycles rather than a count. See `docs/SCORING.md` §Acyclicity and `docs/RESEARCH_METRICS.md` §6. Cross-tool numbers no longer line up here. |
| Depth | `1 / (1 + max_depth / 8)` over longest path | identical, computed on `nx.condensation(graph)` so cycles collapse to single nodes first | Sentrux uses iterative DFS from entry points; networkx's `dag_longest_path_length` is equivalent for a DAG. |
| Equality | `1 - Gini(out-degree)` with `G = Σ (2i - n - 1) x_i / (n * Σ x_i)` | identical | Both projects report `1 - Gini` so a higher number is better. |
| Redundancy | `1 - (dead + duplicate) / total_functions` | **not implemented** | FUTURE.md keeps it deferred: dynamic dispatch, decorators, and `if __name__ == "__main__":` gates make purely-static dead-code detection too noisy. |
| Aggregation | geometric mean of 5 | geometric mean of 4 | Same rationale (Pareto + symmetry + independence). |
| Display scale | integer 0-10000 | float `[0, 1]` to three decimals | Cosmetic. |

What we get from being faithful: a v0.2.0 archy score on a given codebase is directly comparable to whatever sentrux would produce on its four-metric subset. What we lose: redundancy. When `archy redundancy` ships (FUTURE.md, deferred), aggregation will widen back to five and the numeric scale will line up with sentrux's.

The deeper agreement is methodological - sentrux's argument for geometric mean (the only aggregator that's Pareto-optimal, symmetric, and independent) is the load-bearing claim. That's why score gaming works only by improving every axis.

## v0.3.0 - history persistence: comparison with sentrux

archy and sentrux solve overlapping but different problems with persistence:

| | sentrux | archy v0.3.0 |
|---|---|---|
| File | `.sentrux/baseline.json` | `.archy/history.jsonl` |
| Format | Single pretty-JSON record | JSONL, one row per recorded run |
| Retention | One point only (overwritten on each `gate --save`) | All recorded runs |
| Verbs | `gate --save` writes; `gate` (no flag) compares current vs saved | `score --record` appends; `trend` reads back; `score --strict` compares current vs last recorded row |
| Scope | Within-session regression gating for AI agent loops | Long-term drift visualization + per-commit regression gating |

Sentrux is optimized for the cybernetic feedback loop its README pitches: agent saves a baseline, makes changes, runs `gate`, sees pass/fail, self-corrects. A single rolling file is sufficient and JSONL would be wasteful.

archy keeps both capabilities. JSONL is a strict superset - we get long-term history *and* per-commit regression gating from the same file. `archy score --strict` reads the last row and compares against it (the same logic as sentrux's `gate`); `archy trend` reads the full history (which sentrux can't, because it overwrites). The default tolerance (0.02) matches sentrux's threshold so cross-tool intuition transfers.

The trade-off: a JSONL history grows unboundedly. For most projects (one record per commit, ~250 bytes/row) that's a few hundred KB per year of churn. We considered rotating but defaulted to letting it accumulate; users who want bounded history can post-process with `tail -n 1000 history.jsonl > history.jsonl` or similar.

## v0.3.x follow-ups - chain follower + self-loop reporting

Two small refinements to features shipped earlier.

**Multi-hop re-export chains.** v0.2 only resolved one hop: if `pkg/__init__.py` did `from .sub import Foo` and `pkg.sub/__init__.py` did `from .impl import Foo`, consumers of `pkg.Foo` landed at `pkg.sub` rather than the `pkg.sub.impl` source. The fix is a fixed-point pass over the re-export map after the per-package build:

```
for (pkg, name) -> target in maps:
    visited = {pkg}
    while target has a deeper re-export for `name` and target not in visited:
        visited.add(target); target = deeper_target
    # capped at max_depth=8 to short-circuit pathological loops
```

The visited-set + max-depth guard handle the malicious "evil twin" case where two `__init__.py` files re-export each other under the same name, which would otherwise loop forever. `max_depth=8` is enough for real codebases (FastAPI's deepest re-export chain we have seen is 3) and short enough that cycle-induced misbehaviour is bounded.

**Self-loop reporting.** `find_cycles` previously required `min_size >= 2`, which meant a module that imports itself - rare but possible, particularly through `__init__.py` re-exports - was silently dropped. The new semantics: self-loops are *always* reported regardless of `min_size`; the gate only suppresses incidental DAG-singleton SCCs (which never represent a cycle anyway). This was a behaviour-change for `find_cycles(g, min_size=1)` - it no longer reports isolated nodes - but that mode was uninteresting in practice (it was effectively "list all SCCs," not "list cycles"), so the change makes the function's name match what it does.

## v0.4.0 - MCP server: comparison with sentrux

archy and sentrux both ship an MCP server so AI agents can call the analyzer directly. The surfaces overlap but trade off scope vs. statefulness.

| | sentrux | archy |
|---|---|---|
| Tools | 9 (`scan`, `health`, `session_start`, `session_end`, `rescan`, `check_rules`, `evolution`, `dsm`, `test_gaps`) | 9 (`archy_score`, `archy_cycles`, `archy_check`, `archy_contracts`, `archy_trend`, `archy_impact`, `archy_snapshot`, `archy_diff`, `archy_record_baseline`) |
| Session model | Stateful: `session_start` saves an in-process baseline; `session_end` compares against it. | File-based: `archy_snapshot` writes `.archy/baseline.json`, `archy_diff` compares current state against it. The long-lived score history (`.archy/history.jsonl`) is separate and feeds `archy_trend` / `archy_score --strict`. |
| Data behind tools | Snapshot-and-diff against an in-memory baseline. | The CLI's existing JSON shapes - same data agents would read by piping CLI output. |
| Dependencies | Pure Rust, no Python runtime. | Python `mcp` SDK over stdio. |
| Distribution | Single binary. | `uv run archy mcp` (or `pipx run archy mcp` once on PyPI). |

Why the smaller surface: archy's CLI primitives are already orthogonal (`graph` is the building block; `cycles`, `check`, `score`, `trend`, `impact`, `snapshot`, `diff` are projections). Wrapping each as an MCP tool gives the agent the same composable primitives. `archy_snapshot` + `archy_diff` provide the file-based equivalent of sentrux's stateful session pair, and `archy_impact` adds a forward-looking blast-radius query with no sentrux equivalent. The remaining sentrux tools (`evolution`, `dsm`, `test_gaps`, `rescan`) are either FUTURE.md items (`evolution` ≈ trend deltas, `dsm` ≈ a richer graph projection) or out-of-scope (`test_gaps` requires a coverage source archy doesn't ingest).

Why stateless: the `.archy/history.jsonl` file is already the source of truth for trend and gating, so making the MCP surface stateless avoids two ways to compare scores. An agent that wants the sentrux session feel calls `archy_record_baseline(path)` at session start and `archy_score(path, strict=True)` at session end - same pattern, different storage shape.

## v0.13.x and beyond - external empirical validation

When archy's agent-feedback-loop framing was first written, it was a positioning bet on a thesis: AI coding agents need a structural-graph feedback loop, not just larger context windows. As of mid-2026 a small but converging external literature directly supports that bet. Three pieces matter most for archy's design rationale:

- **The Navigation Paradox** (Feb 2026, [arxiv:2602.20048](https://arxiv.org/html/2602.20048v1)) builds an MCP-based graph navigation tool (CodeCompass) shaped almost identically to archy's `archy_graph_*` family and finds that larger context windows do not eliminate the need for structural navigation: the failure mode shifts from retrieval capacity to *navigational salience*. This is direct external validation of archy's MCP graph-tool surface as a category.
- **LocAgent** (ACL 2025, [aclanthology:2025.acl-long.426](https://aclanthology.org/2025.acl-long.426/)) ablates four edge types in a code knowledge graph and finds invoke (call) edges contribute the most to LLM-agent code-localization accuracy, more than import edges. This sharpens archy's call-graph roadmap item from "second edge type, nice to have" to "the missing edge type with the strongest measured contribution in an agent context."
- **Coding-agent failure-mode literature** (Columbia DAPLab, Anthropic, Stack Overflow synthesis, 2026) names the recurring patterns - scope drift, context exhaustion, cross-file reasoning failure, deprecated-pattern propagation - that an architectural feedback loop is supposed to catch. Mapping these to archy's surface yields a concrete priority ordering: cycle/violation gating (shipping), propagation-cost-weighted blast radius (roadmap), per-module risk composite (roadmap).

Detailed citations, a failure-mode-to-archy-capability mapping, and the implied roadmap priority order live in [`RESEARCH_METRICS.md §14c`](RESEARCH_METRICS.md). The roadmap entries themselves are in [`FUTURE.md`](FUTURE.md).

## Competitive landscape (May 2026 survey)

Sentrux was the inspiration and is treated as a peer throughout this document, but it is not the only adjacent tool. A May 2026 web survey grouped the field into five buckets. Recording it here so the design rationale doesn't drift toward "us vs. sentrux" when the actual landscape is wider.

**1. Real-time architectural sensors for AI agents.** Closest in *positioning*.
- [sentrux](https://github.com/sentrux/sentrux) - 52 languages, Rust + tree-sitter, single `quality_signal` (0-10000), live treemap. Breadth-first by design. Archy's depth-first counterpart on the Python side.
- [`camilooscargbaptista/architect`](https://github.com/camilooscargbaptista/architect) - 2026 Claude Code plugin, language-generic, 0-100 score, 9 MCP tools, anti-pattern detection. Closest in *shape* (AI-agent-facing, MCP, single score) but generic across languages and less rigorous than archy or sentrux.

**2. Python architectural-contract tools.** The enforcement slice archy already integrates with.
- [import-linter](https://github.com/seddonym/import-linter) - de-facto contract enforcer, five contract types (Layers, Forbidden, Independence, Protected, AcyclicSiblings). Archy wraps it via `archy contracts` (v0.8.x). `.importlinter` is the canonical contracts config; archy.yaml's `forbid:` fallback is a zero-config onramp only (cannot express `ignore_imports`, so any project that needs to whitelist a legitimate transitive edge must use `.importlinter`).
- [grimp](https://github.com/python-grimp/grimp) - the import-graph library powering import-linter.
- [tach](https://github.com/tach-org/tach) (gauge.sh) - Rust-implemented Python-target enforcer; `tach sync` / `tach check`, layered architecture, VS Code + pre-commit. The most active mainstream competitor for *enforcement*. No score, no trend, no MCP.
- [layer-linter](https://pypi.org/project/layer-linter/) - deprecated, folded into import-linter.

**3. Architecture-as-tests (pytest-style).** Different DX, same target.
- [PyTestArch](https://pypi.org/project/PyTestArch/), [pytest-archon](https://github.com/jwbargsten/pytest-archon), [ArchUnitPython](https://github.com/LukasNiessen/ArchUnitPython), [pytest-imports](https://github.com/nwilbert/pytest-imports) - all ArchUnit-inspired; rules expressed as unit tests. No trended score, no MCP.

**4. Visualization / cycle detection (older generation).**
- [pydeps](https://github.com/thebjorn/pydeps) - Graphviz visualization, cycle highlighting. No enforcement.
- [pycycle](https://github.com/bndr/pycycle) - cycles only.
- snakefood - Python-2 era, unmaintained.
- [modulegraph](https://pypi.org/project/modulegraph/), [importlab](https://github.com/google/importlab) - bytecode/AST graph libraries.

**5. Complexity / health trends (adjacent, not import-graph).**
- [wily](https://pypi.org/project/wily/) - trends *complexity* across git history; closest to archy's "trended score" idea but at the function/cyclomatic level, not architectural.
- radon, lizard, [cohesion](https://github.com/mschwager/cohesion) - point-in-time complexity / LCOM metrics.
- [deptry](https://github.com/fpgmaas/deptry) - package-level unused/missing deps; orthogonal.

**6. Commercial / enterprise.** Different buyer, similar idea.
- [CodeScene](https://codescene.com/product/code-health) - `CodeHealth` score, behavior-aware (combines git history with code). Strongest commercial story for "single trended health metric."
- [Structure101](https://structure101.com/) (acquired by Sonar) - tangles + excessive complexity as TD measures.
- Sonargraph, NDepend (.NET), Lattix - DSM-based architectural analysis, enterprise pricing.

### What this implies for archy's design

The only tools sharing **all** of {Python-native, import-graph, layer rules, trended score, MCP server} are archy and the generic Claude Code `architect` plugin. Cross-referenced against the most-cited competitors:

| Capability | archy | sentrux | tach | import-linter | pytest-archon | CodeScene |
|---|---|---|---|---|---|---|
| Python-deep (transitive contracts) | ✅ | ❌ | partial | ✅ | partial | ❌ |
| Multi-language | ❌ | ✅ (52) | ❌ | ❌ | ❌ | ✅ |
| Single trended score | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ |
| MCP server for agents | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Tree-sitter (in-flight edits) | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| YAML rules (no code) | ✅ | partial | ✅ | ✅ (INI/TOML) | ❌ | n/a |
| OSS | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |

Archy's defensible positioning is **Python depth**: transitive contracts via import-linter, SDP-violation rule, NCCD/propagation cost, `if TYPE_CHECKING:` awareness, type-hint coverage, decorator/descriptor-aware call resolution - the things a 52-language tool structurally cannot fold in without privileging Python. Sentrux's defensible positioning is **language breadth**. That division is settled; the two tools are not on a collision course and the roadmap does not need to defend against multi-language pressure.

The realistic threats on archy's wedge come from inside its own bucket:
- **tach** adding a trended score + MCP server (technically small additions on its existing Rust foundation).
- **sentrux** adding deeper Python semantics, which would erode the depth wedge for users with Python-only repos.

The defense in both cases is to keep moving on Python-canonical metrics (Martin's SDP, MacCormack's propagation cost, the import-linter contract algebra, LocAgent's invoke-edges finding) that are differentiated by their Python-specific resolution, not by their existence.
