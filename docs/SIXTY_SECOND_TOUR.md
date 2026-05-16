# 60-second tour

Zero to your first archy score in under a minute. Every command below is copy-paste runnable.

## Requirements

- Python 3.10 or newer. archy depends on `mcp>=1.27.1` which requires 3.10+. If `python --version` reports 3.9 or older, install a newer Python first (`brew install python@3.12` on macOS, `apt install python3.12` on Debian/Ubuntu, or use [uv](https://docs.astral.sh/uv/) which manages Python versions for you).
- A Python project to point archy at. Any package with `__init__.py` files will work; archy does not need build artifacts, virtualenvs, or run-time imports.

## Install

```bash
pip install archy
# or, if you prefer isolated CLI tools:
uv tool install archy
# or:
pipx install archy
```

Verify:

```bash
archy --version
```

## First score

From the root of any Python project:

```bash
archy score .
```

Expected output (numbers will differ for your project):

```
# archy score: 0.620
modularity:  0.583  (9 communities, raw Q=0.375)
acyclicity:  1.000  (0 cycles, tangle=0.000)
depth:       0.615  (max depth 5)
equality:    0.413  (Gini=0.587)
# graph: 38 modules, 67 edges
# propagation_cost: 0.1392  (diagnostic, not in score)
# calls: 425 resolved across 60 edge(s), 7.08/edge  (diagnostic)
# cc: 544 functions, mean=2.42, max=24  (diagnostic, per-function McCabe)
```

The headline number is the geometric mean of the four sub-axes (modularity, acyclicity, depth, equality). Higher is better; 1.0 is the ceiling. See [`SCORING.md`](SCORING.md) for how each axis is computed and how to read the bands.

## Three things to try next

1. **Find import cycles.**

   ```bash
   archy cycles . --strict
   ```

   Exit code 1 if any cycle exists. Drop `--strict` for inspection mode.

2. **Find the file most worth refactoring.**

   ```bash
   archy hotspots .
   ```

   Ranks modules by `cyclomatic_complexity * git_churn`. The top of the list is where refactoring effort pays back the most.

3. **Wire archy into your AI coding agent.**

   Add to your MCP client config (Claude Code, Cursor, Cline, any MCP client):

   ```json
   {
     "mcpServers": {
       "archy": { "command": "archy", "args": ["mcp"] }
     }
   }
   ```

   Then ask the agent to call `archy_snapshot` at the start of a session, `archy_high_risk_modules` before editing, and `archy_diff` after the edit. The full playbook is in [`AGENT_LOOP.md`](AGENT_LOOP.md).

## Where to next

- [`SCORING.md`](SCORING.md): formulas, interpretation, and the literature each axis comes from.
- [`CASE_STUDIES.md`](CASE_STUDIES.md): archy run against pydantic, fastapi, flask, pytest, and archy-on-archy.
- [`AGENT_LOOP.md`](AGENT_LOOP.md): the snapshot/impact/diff loop for AI-assisted coding.
- [`ROADMAP.md`](ROADMAP.md): what is coming, what is deferred, and what is explicitly rejected.
