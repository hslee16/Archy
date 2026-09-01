# MCP directory submissions

Tracks where archy is listed as an MCP server. Every release, refresh the description on every listed directory to match the new version. Treat this file the way you treat `CHANGELOG.md`: it lives or dies with each release.

## Listed

| Directory | URL | Current version | Last refreshed |
|---|---|---|---|
| Official MCP registry | https://registry.modelcontextprotocol.io (`io.github.hslee16/archy`) | **0.13.3, STALE** | 2026-05-13 |
| Glama | https://glama.ai/mcp/servers/hslee16/archy | (verify) | (verify) |
| Smithery | https://smithery.ai/skills/alex-1c6e/archy (Smithery uses "skills" terminology in 2026) | (verify) | (verify) |

**The official registry entry is live and badly stale.** Verified 2026-07-25:
`status: active`, `isLatest: true`, version `0.13.3`, published 2026-05-13 and
never refreshed. archy is on 0.42.0, so the public entry is 29 minor versions
behind and predates `archy render` and the 11-tool consolidation. It was
previously (and wrongly) tracked below as a pending submission. Republishing is
tracked in [#340](https://github.com/hslee16/archy/issues/340); it needs the
maintainer's GitHub identity, because the registry authenticates ownership
against the `<!-- mcp-name: io.github.hslee16/archy -->` comment in `README.md`.

`server.json` is the file the registry consumes. It silently drifted for 23
releases; `tests/test_server_json.py` now fails the build if it does so again.

## Pending

| Directory | Submission path | Notes |
|---|---|---|
| mcp.so | https://mcp.so/submit | Form-based; asks for submitter identity, so maintainer-side ([#340](https://github.com/hslee16/archy/issues/340)) |
| PulseMCP | https://www.pulsemcp.com/submit | Form-based; same ([#340](https://github.com/hslee16/archy/issues/340)) |
| `awesome-mcp-servers` | PR to https://github.com/punkpeye/awesome-mcp-servers | One-line entry under the right section ([#324](https://github.com/hslee16/archy/issues/324)) |
| `awesome-claude-code-toolkit` | PR to https://github.com/rohitg00/awesome-claude-code-toolkit | One-line MCP-server entry ([#324](https://github.com/hslee16/archy/issues/324)) |

## Reusable submission kit

Use the same kit for every directory. Update the version line per release; everything else is stable.

### Name
archy

### Tagline (max 60 chars)
Architectural sensor for Python codebases (CLI + MCP server)

### Short description (max 200 chars)
Python architecture analysis: import graph, cycles, layer rules, five-axis score, refactor-priority hotspots, DSM, and an MCP server (12 tools) so coding agents see structural impact before they commit.

> **The tagline and descriptions in this kit still need a positioning pass** and
> should not be submitted anywhere until they get one ([#340](https://github.com/hslee16/archy/issues/340)).
> They predate [#316](https://github.com/hslee16/archy/issues/316) and read as a
> *category* description ("architectural sensor for Python codebases"), which is
> exactly the framing the README lead moved away from in
> [#334](https://github.com/hslee16/archy/pull/334). The differentiator to carry
> is normative vs descriptive: archy holds the structure you *declared* and
> reports when an edit breaks it, where navigation-first graph tools describe the
> structure that exists. The tool count was corrected from 14 to 11 (the
> [#265](https://github.com/hslee16/archy/issues/265) consolidation folded
> `archy_trend`, `archy_status`, and `archy_contracts` into other tools);
> `grep -c "@server.tool(" src/archy/mcp.py` is the check.

### Long description
archy watches a Python codebase, builds a live module-dependency graph, and surfaces drift through a single trended score plus a handful of actionable sub-metrics. Designed to run in CI, in pre-commit, and as an MCP server (`archy mcp`) so coding agents (Claude Code, Cursor, Codex, opencode, Continue, any MCP client) can read their own architectural impact before committing. A persistent parse cache kept warm by a background file watcher keeps tool calls fast (low seconds even on 10k+ module repos), and `archy install` auto-wires the server into any supported agent. Tree-sitter powered, so robust to in-flight edits and partial files.

### Install / config snippet
```bash
pip install archy        # or: uv tool install archy
uvx archy install        # auto-wire the MCP server into your agent(s)
```

Or wire it manually into any MCP client:

```json
{
  "mcpServers": {
    "archy": { "command": "uvx", "args": ["archy", "mcp"] }
  }
}
```

### Cross-tool framing line (use everywhere)
archy is MCP-native: works with any MCP client, not just Claude. Don't position as "for Claude Code" anywhere.

### Tags / categories (pick what each directory supports)
`code-analysis`, `python`, `developer-tools`, `code-quality`, `architecture`, `mcp`, `import-graph`, `static-analysis`

### Screenshot
`archy render --view dsm` against a real repo (new in v0.42.0): one self-contained HTML page, and the flagged cells show the differentiator faster than a terminal dump of a score. Fall back to `archy_graph` summary output only where a directory rejects HTML-derived images.

### Demo video (60s, optional but Smithery weights it)
A screencap of a Claude-Code session using archy tools to avoid a regression: snapshot at start, edit a load-bearing module, diff at end to see the score drop and the added cycle.

## Per-release refresh checklist

When cutting a new version (Nx.y.z):

- [ ] Bump `server.json` (**both** the top-level `version` and
      `packages[0].version`) and republish the official registry entry. This is
      the step that was missed for 23 consecutive releases;
      `tests/test_server_json.py` now fails CI if the file drifts from
      `pyproject.toml`, but publishing is still manual.
- [ ] Update `Current version` columns in the table above.
- [ ] Glama: log in, navigate to https://glama.ai/mcp/servers/hslee16/archy, edit metadata to match the new tagline / short description if anything changed.
- [ ] Smithery: same flow at https://smithery.ai/skills/alex-1c6e/archy.
- [ ] mcp.so / PulseMCP / official registry / awesome-mcp-servers / awesome-claude-code-toolkit: only refresh when there is a substantive change (new MCP tool, new core feature). Patch releases skip these.
- [ ] Update `Last refreshed` to today's date.

## Source

The "submit to every MCP directory" guidance is from the 2026 MCP distribution playbook tracked in the local research notes. The "Smithery uses skills terminology in 2026" detail is confirmed empirically (current archy listing URL pattern).
