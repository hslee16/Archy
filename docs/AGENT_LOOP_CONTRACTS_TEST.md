# Test protocol: agent-loop with `archy_contracts`

Context: `docs/RESEARCH_METRICS.md` §10 (Forbidden + Independence contracts) and the conversation thread that produced this PR motivate the wrap. The load-bearing question we're testing is:

> **Does proactive architectural feedback via MCP actually change agent behavior?**

The hypothesis is that surfacing `archy_contracts` as an MCP tool lets coding agents catch their own contract violations before committing, without being explicitly prompted to. If agents only call the tool when a human asks them to, the value is limited; if agents call it unprompted as part of their working loop, that's the unique value-add over running `import-linter` in CI alone.

## Setup

1. Install archy with the contracts extra:

   ```bash
   uv pip install -e ".[contracts]"
   ```

2. Confirm `.importlinter` exists at the repo root with the three Forbidden contracts shipped in this PR. Sanity-check from the command line:

   ```bash
   uv run archy contracts .
   ```

   Expected output: 2 kept, 1 broken, with the broken one being `archy.diff -> archy.layers`. **This pre-existing violation is the test fixture** (an unrelated finding the wrap surfaced; see "Known finding" below).

3. Configure your AI agent (Claude Code, Cursor, etc.) to use archy as an MCP server. For Claude Code, add to `~/.claude/settings.json` under `mcpServers`:

   ```json
   "archy": {
     "command": "uv",
     "args": ["run", "archy", "mcp"],
     "cwd": "/Users/<you>/archy"
   }
   ```

4. Open a fresh agent session in the archy repo. Confirm `archy_contracts` is listed among the available tools.

## Scenarios

Run both scenarios from a clean working tree (no uncommitted changes).

### Scenario A - control: no contract awareness

The point of A is to establish baseline agent behavior with no architectural feedback loop.

**Prompt** (verbatim):

> In `src/archy/parser.py`, add a small debug helper: `def _debug_print_score(graph)` that imports `compute_score` from `archy.score`, calls it on `graph`, and prints the overall. Wire it into `parse_file` so it logs after each file is parsed. Don't worry about flag-gating; this is a one-off debugging aid.

This deliberately violates the `parser must not reach graph/policy/cli layers` contract: `parser` is a leaf, and importing `archy.score` makes it reach the graph layer.

**What to observe:**

- Does the agent just do it? (Expected: yes - it has no signal that this is forbidden.)
- Does it acknowledge any architectural concern? (Expected: no - there's no archy.yaml violation here either, since `parser` IS in the layer config but the rule is direct-edge and `archy.score → archy.parser` direction is what's forbidden, not reverse... actually let me re-read archy.yaml.)
- Capture the diff and the agent's response for comparison.

After capturing, **revert the change** (`git restore src/archy/parser.py`).

### Scenario B - experimental: with `archy_contracts` available

Same prompt, same starting state. The MCP server is the same as before; the only variable is whether the agent decides to call `archy_contracts` proactively.

**What to observe (in order of strength of evidence):**

1. **Strongest signal**: agent calls `archy_contracts` *before* writing the import, sees the contract, and either declines or warns.
2. **Medium signal**: agent calls `archy_contracts` *after* writing the import, sees the violation in the response, and reverts or restructures.
3. **Weak signal**: agent calls `archy_contracts` only when prompted explicitly ("did you check if this is allowed?").
4. **Null result**: agent never calls `archy_contracts` even when it has been told the tool exists.

If the result is (3) or (4), the hypothesis is *not* supported and we should reconsider the wrap.

## What "success" looks like

The minimum bar for shipping this beyond the experimental phase is that **at least one of (1) or (2) reproduces across 3 separate fresh sessions** - i.e., it's not a one-off behavior. If it only reproduces sometimes, the value is real but limited; that's a signal to invest in tool-description tuning.

## Known finding to set aside

The repo currently fails the `graph layer must not reach policy/cli layers` contract because `archy.diff` (graph layer) imports from `archy.layers` (policy layer). This is a real pre-existing architectural drift surfaced by the wrap when this PR was developed; `archy check` doesn't catch it because archy.yaml's layer rules don't include `archy.diff` in any layer. Tracking separately; not part of the agent-loop test.

## After the test

Capture findings in a follow-up doc or PR comment:

- Which scenario reproduced (A control / B unprompted / B prompted-only).
- Verbatim agent transcripts for both runs.
- Whether the `archy_contracts` tool description was clear enough or needed iteration.
- Recommendation: ship the wrap, drop it, or iterate on tool description first.
