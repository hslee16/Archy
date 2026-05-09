# Case Studies

Real-world runs of archy. Useful as regression evidence and as a place to point new contributors at "what does the output actually look like."

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
