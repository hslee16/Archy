# Case Studies

Real-world runs of archy. Useful as regression evidence and as a place to point new contributors at "what does the output actually look like."

## archy across its own releases (v0.8.2 → v0.11.0)

`archy score` run by the v0.11.0 binary against each tagged version of
archy's own source tree. Same scoring methodology end-to-end, so the
numbers are directly comparable.

| Release | Modules | Edges | Overall | Modularity | Acyclicity | Depth | Equality |
| ------- | ------: | ----: | ------: | ---------: | ---------: | ----: | -------: |
| v0.8.2  |      13 |    26 |   0.536 |      0.466 |      1.000 | 0.667 |    0.266 |
| v0.9.0  |      13 |    27 |   0.550 |      0.459 |      1.000 | 0.667 |    0.299 |
| v0.10.0 |      14 |    29 |   0.555 |      0.470 |      1.000 | 0.667 |    0.303 |
| v0.11.0 |      14 |    29 |   0.555 |      0.470 |      1.000 | 0.667 |    0.303 |

What moved:

- **v0.8.2 → v0.9.0 (+0.014)**: equality went from 0.266 to 0.299
  while modularity dropped slightly. The release added the
  archy.yaml-as-contracts-config reader (#42) and converted all
  internal dataclasses to pydantic models (#44); the structural
  effect on the graph was a redistribution of fan-out (one new
  internal edge but a wider spread), which is exactly the kind of
  change the equality axis is meant to reward.
- **v0.9.0 → v0.10.0 (+0.005)**: the instability metric + SDP
  violation check shipped (#48). One new module (`instability.py`)
  and two new internal edges. Modularity ticks back up because the
  new module is a clean leaf (high I), and equality moves a hair
  because fan-out gets one more sink.
- **v0.10.0 → v0.11.0 (+0.000)**: no source changes - the v0.11.0
  release was entirely benchmark + docs refresh, which is the
  expected null result. A useful baseline: a release that's
  *supposed* to be docs-only and that doesn't accidentally drift
  the score is a quiet success.

Acyclicity has been pinned at 1.000 across all four releases because
the layer rules in `archy.yaml` are enforced in CI - any commit that
would introduce a cycle fails the `archy check` gate before merging.
Depth is also constant at 0.667 (max chain length 3 through the
SCC condensation), which is the natural ceiling for a 14-module
project organized as parser → graph → policy → cli.

Equality stays the weakest axis - `archy.cli` aggregates all user-
facing surfaces and naturally has the highest fan-out. That's
expected for a CLI app, and explicit acceptance of it is why the
score uses geometric mean rather than minimum: a deliberately
weak axis shouldn't tank the composite if every other axis is
strong.

## archy on archy (dogfooding, v0.1.0)

> Historical, retained for reference. Module counts and CLI output below
> reflect the v0.1.0 surface; current archy is larger. For a current
> archy-on-archy snapshot see the score table in
> [`docs/SCORING.md`](SCORING.md).

archy enforces its own architecture in CI via `archy check .` against
[`archy.yaml`](../archy.yaml). The intended layering is a pure dependency
tree:

```
parser  →  (nothing internal)
graph   →  parser
policy  →  graph
cli     →  parser, graph, policy
```

`graph` covers the analysis primitives (`archy.graph`, `archy.cycles`,
`archy.score`, `archy.history`, `archy.trend`); `policy` covers
`archy.layers` (the rule engine). `cli` is the only layer allowed to
depend on every lower layer. Lower layers must not depend on higher
ones - six `forbid` rules encode the full anti-set.

```bash
$ uv run archy graph . --internal-only
# 13 internal module(s), 12 import edge(s)
archy
archy.cli
  -> [int] archy
  -> [int] archy.cycles
  -> [int] archy.graph
  -> [int] archy.layers
archy.cycles
archy.graph
  -> [int] archy.parser
archy.layers
archy.parser
...

$ uv run archy cycles .
# No cycles found (min_size=2).

$ uv run archy check .
# No layer violations (config: /Users/.../archy/archy.yaml).
```

The CI step (`.github/workflows/ci.yml`) makes any layer-crossing PR fail
fast, so AI-assisted refactors can't quietly invert the dependency tree.

## `__init__.py` re-export resolution - multi-library benchmark (v0.0.2)

After landing re-export resolution (so that `from pkg import Foo`, where
`pkg/__init__.py` does `from .impl import Foo`, attributes the edge to
`pkg.impl` rather than `pkg`), we ran archy against 9 mature codebases and
compared edges before vs after.

**Command per library**: `archy graph path/to/<library> --format json`. Counts
include both internal and external edges; the "edges into pkg root" column
counts edges whose target is the top-level package qualname (the most direct
indicator of phantom-cycle pressure caused by re-exports).

| Library | Internal | Before edges | After edges | Δ | Edges into pkg root |
|---|---:|---:|---:|---:|---|
| flask | 54 | 293 | 303 | +10 | **22 → 1** |
| pydantic | 402 | 2799 | 2986 | +187 | **165 → 61** |
| fastapi | 48 | 280 | 284 | +4 | 1 → 1 (9 `_compat` edges redirected) |
| pytest | 247 | 1667 | 1669 | +2 | 128 → 128 |
| requests | 37 | 263 | 263 | 0 | 5 → 5 |
| starlette | 67 | 590 | 590 | 0 | 0 → 0 |
| httpx | 60 | 319 | 319 | 0 | 32 → 32 |
| click | 22 | 203 | 203 | 0 | 3 → 3 |
| rich | 213 | 1167 | 1167 | 0 | 23 → 23 |

### Reading the results

- **Flask** is the cleanest demo: 22 phantom edges into `flask` collapse to 1.
  `flask/__init__.py` re-exports the public surface (`Flask`, `request`,
  `Blueprint`, etc.) from internal modules; pre-fix, every consumer got
  attributed to `flask` itself.
- **Pydantic**: 165 → 61 edges into `pydantic` (-63%). Pydantic uses a wide
  `__init__.py` public surface and the fix recovers the per-file dependencies.
- **FastAPI**: only 1 edge into `fastapi` either way (FastAPI internally
  prefers `from fastapi.<sub> import …` over the bare form), but the
  `_compat` cluster gets a clear correction - 9 edges that pointed at the
  `fastapi._compat` package now route to the actual `fastapi._compat.shared`
  / `fastapi._compat.v2` files where the names live.
- **Unchanged libraries** (requests, starlette, httpx, click, rich, pytest)
  fall into two camps: either their `__init__.py` is empty / minimal (so
  there are no re-exports to resolve), or consumers fully-qualify imports
  (`from rich.console import Console`) instead of using the public surface.
  No-op in those cases is the correct behavior.

## Cycle detection - multi-library benchmark (v0.0.3)

`archy cycles <path>` reports each strongly-connected component of size ≥ 2
(Tarjan SCC via `networkx.strongly_connected_components`). Run against the
same 9 libraries as the re-export benchmark, post re-export resolution.

| Library | Internal | Cycles | Largest cycle | Modules in cycles |
|---|---:|---:|---:|---:|
| pydantic | 402 | 2 | 46 | 64 |
| rich | 213 | 2 | 53 | 55 |
| pytest | 247 | 1 | 33 | 33 |
| flask | 54 | 2 | 19 | 21 |
| httpx | 60 | 2 | 11 | 17 |
| click | 22 | 2 | 11 | 13 |
| fastapi | 48 | 2 | 7 | 11 |
| starlette | 67 | 1 | 14 | 14 |
| requests | 37 | 1 | 8 | 8 |

### Notable findings

- **FastAPI** retains a 7-module core cycle even after re-export resolution
  (`fastapi ↔ applications ↔ dependencies.utils ↔ exception_handlers ↔
  openapi.utils ↔ routing ↔ utils`) plus a 4-module compat cluster
  (`_compat.v2 ↔ datastructures ↔ openapi.models ↔ params`). The earlier
  case-study prediction that the core cycle was *entirely* an artifact of
  re-export resolution turned out to be too optimistic - it was *partly* an
  artifact (visibility on this only became possible after the re-export fix
  routed the obvious phantom edges away).
- **Pydantic** has a 46-module cycle anchored at the `pydantic` package
  root, plus an 18-module cycle inside `pydantic.v1` (the legacy
  compatibility layer). Both look like real coupling.
- **Rich** has a 53-module cycle that includes `rich` itself and most of
  its public surface - typical for a library where the package node is
  imported widely and most modules have at least one back-edge into the
  shared namespace.
- **Requests, starlette** show a single mid-sized cycle each - interesting
  candidates for refactoring exercises.
- **Click** and **httpx** have small, tight cycles (≤ 11 modules).

### Why this matters

These numbers are *baseline* readings, not value judgments. Some of these
cycles will be deliberate (compat layers, plugin entry points), some will
be accidents that have accumulated over years. Trended over commits, they
become a structural-drift sensor - that's the eventual scoring story.


## FastAPI (v0.0.1, import graph only)

**Repo:** [`fastapi/fastapi`](https://github.com/fastapi/fastapi) at HEAD on 2026-05-09
**Command:** `archy graph path/to/fastapi/fastapi --internal-only`

### Results

| Metric | Value |
|---|---|
| Internal modules | 48 |
| External packages | 38 |
| Total edges | 280 |
| Internal-only edges | 110 |
| Parse errors | 0 |
| Cold runtime | ~120 ms |

### Top external dependencies (by inbound edges)

| Edges | Package |
|---|---|
| 30 | `starlette` |
| 24 | `typing` |
| 16 | `collections` |
| 14 | `pydantic` |
| 13 | `annotated_doc` |
| 7 | `enum` |
| 7 | `typing_extensions` |

`starlette` dominating is exactly right - FastAPI is structurally a starlette extension. `pydantic` showing up at 14 inbound matches FastAPI's dual-model story.

### Top internal fan-out (potential god-modules)

| Out-degree | Module |
|---|---|
| 14 | `fastapi.openapi.utils` |
| 11 | `fastapi.applications` |
| 10 | `fastapi.dependencies.utils` |
| 10 | `fastapi.routing` |
| 9 | `fastapi` |

### Top internal fan-in (stable interface modules)

| In-degree | Module |
|---|---|
| 12 | `fastapi.exceptions` |
| 9 | `fastapi._compat` |
| 8 | `fastapi.openapi.models` |
| 8 | `fastapi.types` |

### Strongly connected components (size > 1) - preview of cycle detection

Two SCCs found:

1. **`_compat` cluster (5 modules)** - `_compat ↔ _compat.v2 ↔ datastructures ↔ openapi.models ↔ params`. Likely a real cycle in the pydantic v1/v2 compatibility layer.

2. **Core API cluster (7 modules)** - `fastapi ↔ applications ↔ dependencies.utils ↔ exception_handlers ↔ openapi.utils ↔ routing ↔ utils`. **At least partly an artifact of `__init__.py` re-exports**: `fastapi/__init__.py` re-exports a public surface; submodules then `from fastapi import X` to use those names; the resulting back-edge to `fastapi` closes a phantom cycle.

This was the trigger for promoting `__init__.py` re-export resolution into the near-term roadmap (see `docs/FUTURE.md`). After that PR lands, this case study should be re-run; the expected outcome is the second SCC dropping to 0–1 modules and only the `_compat` cycle remaining as a real finding.

**Update:** re-export resolution shipped in v0.0.2 (see case study above). The prediction was *partly* right: 9 phantom `_compat` edges did get redirected, but the core 7-module SCC (`fastapi ↔ applications ↔ dependencies.utils ↔ exception_handlers ↔ openapi.utils ↔ routing ↔ utils`) survived as a real cycle (see the v0.0.3 cycle benchmark above). The "entirely a re-export artifact" framing was too optimistic.

### Why this case study matters

- **Validates the parser on a non-toy codebase** - 0 parse errors across the FastAPI source tree, sub-second runtime.
- **Surfaces a real correctness bug in the resolver** - the `__init__.py` over-reporting wouldn't have shown up on archy's own tests, because archy's own `__init__.py` is empty. Real codebases use `__init__.py` as a public surface, and our resolver needs to follow.
- **Provides a regression target** - re-running this command after the next PR lets us measure whether the fix actually works.

## Composite quality score - multi-library benchmark (v0.2.0)

> **Historical, retained for reference.** The acyclicity formula
> changed in v0.7.x from `1 / (1 + cycle_count)` to
> `1 - tangle_ratio`, so the absolute numbers below are not directly
> comparable to current `archy score` output. For the current
> benchmark and bands see [`docs/SCORING.md`](SCORING.md). The
> qualitative observations below (about which axis dominates
> variance, which libraries score lowest and why) still hold under
> the new formula, with the caveat that pytest and fastapi rise
> markedly because their cycles cover only a small fraction of large
> codebases.

`archy score <path>` reports a composite quality score (current archy: five axes including complexity; this historical run uses the original four-axis formula). Same 9 libraries, same checkouts as the cycle benchmark. See [`docs/SCORING.md`](SCORING.md) for the current formula and a refreshed 29-project benchmark.

| Library | Score | Modularity | Acyclicity | Depth | Equality |
|---|---:|---:|---:|---:|---:|
| starlette | 0.546 | 0.456 | 0.500 | 0.667 | 0.584 |
| httpx | 0.491 | 0.580 | 0.333 | 0.615 | 0.489 |
| click | 0.488 | 0.495 | 0.333 | 0.667 | 0.515 |
| rich | 0.485 | 0.546 | 0.333 | 0.615 | 0.494 |
| flask | 0.482 | 0.557 | 0.333 | 0.571 | 0.510 |
| requests | 0.467 | 0.490 | 0.500 | 0.533 | 0.365 |
| pytest | 0.465 | 0.519 | 0.500 | 0.471 | 0.382 |
| pydantic | 0.460 | 0.641 | 0.333 | 0.533 | 0.392 |
| fastapi | 0.423 | 0.522 | 0.333 | 0.615 | 0.300 |

Notes:

- Acyclicity dominates the variance: 1 cycle drops it to 0.5, 2 cycles to 0.333. Most libraries have 1-2 cycles.
- Pydantic has the highest raw modularity (0.641 normalized) but low equality (0.392): heavy fan-out concentrated in a few modules pulls the geometric mean down.
- FastAPI is the bottom-scorer mainly because of low equality (0.300): `applications.py`, `routing.py`, and `openapi.utils.py` carry most of the public surface, creating a steep out-degree distribution.
- Numbers are not directly comparable to sentrux's display because archy reports floats in `[0, 1]` and sentrux reports integers `0-10000`; multiply archy's `overall` by 10000 for parity.

### archy on archy (v0.2.0)

> Historical, retained for reference. The v0.2.0 acyclicity formula was
> `1/(1+cycle_count)`, which v0.7.x replaced with `1 - tangle_ratio`;
> the v0.2.0 score below (0.677) is not directly comparable to current
> output. Current archy-on-archy numbers in
> [`docs/SCORING.md`](SCORING.md).

`archy score .` on the archy repo:

```
# archy score: 0.677
modularity:  0.606  (5 communities, raw Q=0.409)
acyclicity:  1.000  (0 cycles)
depth:       0.727  (max depth 3)
equality:    0.476  (Gini=0.524)
# graph: 15 modules, 15 edges
```

archy outscores every library in the benchmark above, mostly because it has zero cycles (the layer rules block them in CI) and a shallow dependency tree. Equality is the weakest axis: `archy.cli` aggregates all user-facing surfaces and naturally has higher fan-out than the modules below it. Expected for a CLI app.

For the design-side comparison with sentrux's quality-signal model, see [`docs/LEARNINGS.md`](LEARNINGS.md#v020---score-comparison-with-sentrux).

## Large real-world repos (v0.27.0)

A validation pass on three large, widely-used codebases (the same trees used for the persistent-index benchmark), run with the v0.27.0 scorer. The point is not the leaderboard; it is that archy finds *real* structural issues in heavily-reviewed, production code that per-file review does not track.

| Repo | Modules | Edges (internal) | Overall | Acyclicity | Cycles found | Max depth | cc_max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Django | 2,910 | 9,602 | 0.556 | 0.922 | 17 | 24 | 94 |
| pytorch | 4,488 | 27,192 | 0.536 | 0.699 | 22 | 21 | 201 |
| Home Assistant | 17,299 | 93,799 | 0.626 | 0.950 | 108 | 17 | 95 |

(On Home Assistant the project-wide `propagation_cost` is expensive enough at ~17k modules / ~94k internal edges that scoring is a multi-second operation even with the parse cache warm; that is exactly the motivation for the assembled-graph-blob follow-up noted in [`FUTURE.md`](FUTURE.md).)

What this shows:

- **archy surfaces real import cycles in all three** (Django 17, pytorch 22, Home Assistant 108). These are tangles in code that thousands of developers review; they are invisible to a per-file diff review because a cycle is a whole-graph property. pytorch's acyclicity of 0.70 quantifies a materially more tangled import graph than Django's 0.92 or Home Assistant's 0.95 (HA carries the most *cycles* in absolute terms, but as a small fraction of its 17k modules, so its acyclicity axis stays high).
- **It localizes extreme complexity hotspots**: pytorch has a function with cyclomatic complexity **201** (`cc_max`), the kind of "edit at your peril" site `archy_hotspots` and `archy_high_risk_modules` are built to flag before an agent touches it.
- **The numbers are stable and comparable** because the cache-backed build is byte-identical to a cold build (see the persistent-index work), so these can be re-run in CI on every commit to trend architecture erosion over time.

This is the mission in one table: a single per-commit number plus a short list of concrete, agent-actionable structural findings, on codebases far too large to hold in any review (or context window) at once.
