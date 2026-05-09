# Case Studies

Real-world runs of archy. Useful as regression evidence and as a place to point new contributors at "what does the output actually look like."

## archy on archy (dogfooding, v0.1.0)

archy enforces its own architecture in CI via `archy check .` against
[`archy.yaml`](../archy.yaml). The intended layering is a pure dependency
tree:

```
parser  →  (nothing internal)
graph   →  parser
policy  →  graph
cli     →  parser, graph, policy
```

`graph` covers `archy.graph` and `archy.cycles`; `policy` covers
`archy.layers` (the rule engine). `cli` is the only layer allowed to
depend on every lower layer. Lower layers must not depend on higher
ones — six `forbid` rules encode the full anti-set.

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

## `__init__.py` re-export resolution — multi-library benchmark (v0.0.2)

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
  `_compat` cluster gets a clear correction — 9 edges that pointed at the
  `fastapi._compat` package now route to the actual `fastapi._compat.shared`
  / `fastapi._compat.v2` files where the names live.
- **Unchanged libraries** (requests, starlette, httpx, click, rich, pytest)
  fall into two camps: either their `__init__.py` is empty / minimal (so
  there are no re-exports to resolve), or consumers fully-qualify imports
  (`from rich.console import Console`) instead of using the public surface.
  No-op in those cases is the correct behavior.

### Known limitation

Re-export *chains* are followed only one hop. If `pkg/__init__.py` re-exports
from `pkg.sub`, and `pkg.sub/__init__.py` re-exports from `pkg.sub.impl`,
consumers of `pkg.Foo` resolve to `pkg.sub`, not `pkg.sub.impl`. Multi-hop
following is filed as a follow-up.

## Cycle detection — multi-library benchmark (v0.0.3)

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
  re-export resolution turned out to be too optimistic — it was *partly* an
  artifact (visibility on this only became possible after the re-export fix
  routed the obvious phantom edges away).
- **Pydantic** has a 46-module cycle anchored at the `pydantic` package
  root, plus an 18-module cycle inside `pydantic.v1` (the legacy
  compatibility layer). Both look like real coupling.
- **Rich** has a 53-module cycle that includes `rich` itself and most of
  its public surface — typical for a library where the package node is
  imported widely and most modules have at least one back-edge into the
  shared namespace.
- **Requests, starlette** show a single mid-sized cycle each — interesting
  candidates for refactoring exercises.
- **Click** and **httpx** have small, tight cycles (≤ 11 modules).

### Why this matters

These numbers are *baseline* readings, not value judgments. Some of these
cycles will be deliberate (compat layers, plugin entry points), some will
be accidents that have accumulated over years. Trended over commits, they
become a structural-drift sensor — that's the eventual scoring story.


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

`starlette` dominating is exactly right — FastAPI is structurally a starlette extension. `pydantic` showing up at 14 inbound matches FastAPI's dual-model story.

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

### Strongly connected components (size > 1) — preview of cycle detection

Two SCCs found:

1. **`_compat` cluster (5 modules)** — `_compat ↔ _compat.v2 ↔ datastructures ↔ openapi.models ↔ params`. Likely a real cycle in the pydantic v1/v2 compatibility layer.

2. **Core API cluster (7 modules)** — `fastapi ↔ applications ↔ dependencies.utils ↔ exception_handlers ↔ openapi.utils ↔ routing ↔ utils`. **At least partly an artifact of `__init__.py` re-exports**: `fastapi/__init__.py` re-exports a public surface; submodules then `from fastapi import X` to use those names; the resulting back-edge to `fastapi` closes a phantom cycle.

This was the trigger for promoting `__init__.py` re-export resolution into the near-term roadmap (see `docs/FUTURE.md`). After that PR lands, this case study should be re-run; the expected outcome is the second SCC dropping to 0–1 modules and only the `_compat` cycle remaining as a real finding.

### Why this case study matters

- **Validates the parser on a non-toy codebase** — 0 parse errors across the FastAPI source tree, sub-second runtime.
- **Surfaces a real correctness bug in the resolver** — the `__init__.py` over-reporting wouldn't have shown up on archy's own tests, because archy's own `__init__.py` is empty. Real codebases use `__init__.py` as a public surface, and our resolver needs to follow.
- **Provides a regression target** — re-running this command after the next PR lets us measure whether the fix actually works.
