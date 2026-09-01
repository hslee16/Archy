@AGENTS.md

The guidance for coding agents working on this repository lives in
[`AGENTS.md`](AGENTS.md), which is the cross-tool convention. This file exists so
that Claude Code, which looks for `CLAUDE.md`, finds it too. The `@AGENTS.md`
line above imports it.

Keep the content in `AGENTS.md`. Adding notes here instead will hide them from
every agent that does not read `CLAUDE.md`, which is the problem this split
exists to solve.
