"""The MCP prompt archy serves: how to use it as an architectural feedback loop."""

from __future__ import annotations

from archy.mcp_compat import Server

_AGENT_LOOP_PROMPT = """\
# archy agent loop

archy turns the project's structural health into a number you can act on
between edits. A persistent parse cache, kept warm by a background file
watcher, makes calls fast (warm graph builds are a few seconds even on
10k+ module repos). Using archy around edits is optional; the step whose
verdict can block a commit is the diff, so run it at verification points:
before you commit, at the end of a task, and whenever an edit adds or removes
a cross-package import. You never need to
worry about staleness: every tool re-syncs the changed files on demand, so a
result always reflects the current code (the CLI `archy index status` reports
the cache state if you ever want to confirm it out of band). The loop is:

1. **Snapshot** at session start so you have a baseline:
   `archy_snapshot(path)`

   Read `invariant_brief` in the result first: the declared layers and
   the forbidden edges between them, whether the graph is currently
   acyclic, the baseline score per axis, and the load-bearing modules
   (highest `edit_risk`, treat as high blast radius). Knowing these up
   front lets you avoid proposing a cross-layer or cycle-introducing
   edit in the first place, instead of discovering it in step 4's diff.
2. **Look up impact** before editing a module so you know who breaks if
   the change is wrong:
   `archy_impact(path, files=[<file you plan to edit>])`

   For a bounded, bidirectional neighborhood with edge line numbers,
   use `archy_graph(path, focus=[<file or qualname>])` instead.
   `archy_graph(path)` gives a top-N overview (summary by default) when
   you don't yet know which module to look at; pass
   `response_format='full'` only when you actually need the whole node/
   edge dump. Before a non-trivial edit, call
   `archy_what_to_refactor_next(path, lens='structural')` to see whether
   your target sits in the project's central-and-fragile zone (high blast
   radius combined with high instability); if it does, scope down or pause
   for review.

   If the edit changes imports (adds or removes a dependency), call
   `archy_simulate(path, add=[{from, to}], remove=[...])` first: it
   returns the would-be cycles, layer violations, and score delta with
   no file written, so you can abandon or reshape a plan that introduces
   a cycle before touching code instead of catching it in step 4.
3. **Edit** the code as you normally would.
4. **Diff** after the edit to see what got better, what got worse, and
   exactly which cycles or layer rules changed:
   `archy_diff(path)`
5. Read `summary.headline` first - one structured sentence
   ("overall +X; driven by Y; cycles +A/-B"). Then walk
   `summary.top_regressions` in order; each item carries a `risk` score
   (0-1, from `compute_edit_risk`) so the most central/fragile breakage
   surfaces first. The raw `score_delta`, `cycles`, `violations`, and
   `sdp_violations` blocks remain available for when you need the full
   list, but the summary is the right starting point: it's the same
   ranking you would have done by hand. If the headline shows
   `overall -...` or any `top_regressions` exist, inspect the named
   modules, fix or revert, then loop back to step 4. Recurse until the
   diff is clean (empty `top_regressions`).

`archy_score(path, strict=True)` is a one-shot gate against the last
recorded run; it's lighter than the snapshot/diff loop and useful as
the final pre-commit check.

`archy_dsm(path, ...)` returns the Design Structure Matrix: a row/col
matrix the agent reads positionally, not a scalar. Use it when you
need *where*, not *how much*: orienting in a new repo
(`group_by='community'`), localizing a cycle to specific back-edges
(`group_by='topological'`), or inspecting cross-layer traffic
(`group_by='layer', weight='calls'`). It is summary-by-default (block
structure, counts, and the back-edges, without the full cell list);
pass `response_format='full'` for the complete matrix. Narrow large
projects with `focus=<qualname>` or `package=<prefix>`. Passing
`baseline_path` to a previously saved DSM JSON returns a structured
diff whose `new_back_edges` field flags cycles the most recent edit
just introduced.
"""


def _register_prompts(server: Server) -> None:
    @server.prompt(
        name="loop",
        description=(
            "How to use archy as an architectural feedback loop while editing code. "
            "Read this at session start so subsequent tool calls follow the right "
            "snapshot -> edit -> diff cadence."
        ),
    )
    def loop() -> str:
        return _AGENT_LOOP_PROMPT
