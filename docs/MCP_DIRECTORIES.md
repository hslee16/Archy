# MCP directory submissions

Tracks where archy is listed as an MCP server. Every release, refresh the description on every listed directory to match the new version. Treat this file the way you treat `CHANGELOG.md`: it lives or dies with each release.

## Listed

| Directory | URL | Current version | Last refreshed |
|---|---|---|---|
| Glama | https://glama.ai/mcp/servers/hslee16/archy | (verify) | (verify) |
| Smithery | https://smithery.ai/skills/alex-1c6e/archy (Smithery uses "skills" terminology in 2026) | (verify) | (verify) |

## Pending (target: next clerical sprint)

| Directory | Submission path | Notes |
|---|---|---|
| mcp.so | https://mcp.so/submit | Form-based |
| PulseMCP | https://www.pulsemcp.com/submit | Form-based |
| Official `modelcontextprotocol/servers` registry | PR to https://github.com/modelcontextprotocol/servers | Upstream review; curated |
| `awesome-mcp-servers` | PR to https://github.com/punkpeye/awesome-mcp-servers | One-line entry under the right section |
| `awesome-claude-code-toolkit` | PR to https://github.com/rohitg00/awesome-claude-code-toolkit | One-line MCP-server entry |

## Reusable submission kit

Use the same kit for every directory. Update the version line per release; everything else is stable.

### Name
archy

### Tagline (max 60 chars)
Architectural sensor for Python codebases (CLI + MCP server)

### Short description (max 200 chars)
Python architecture analysis: import graph, cycles, layer rules, five-axis score, refactor-priority hotspots, DSM, and an MCP server (17 tools) so coding agents see structural impact before they commit.

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
Output of `archy_graph_summary` against a real repo, or a Claude-Code session showing `archy_high_risk_modules` being called.

### Demo video (60s, optional but Smithery weights it)
A screencap of a Claude-Code session using archy tools to avoid a regression: snapshot at start, edit a load-bearing module, diff at end to see the score drop and the added cycle.

## Per-release refresh checklist

When cutting a new version (Nx.y.z):

- [ ] Update `Current version` columns in the table above.
- [ ] Glama: log in, navigate to https://glama.ai/mcp/servers/hslee16/archy, edit metadata to match the new tagline / short description if anything changed.
- [ ] Smithery: same flow at https://smithery.ai/skills/alex-1c6e/archy.
- [ ] mcp.so / PulseMCP / official registry / awesome-mcp-servers / awesome-claude-code-toolkit: only refresh when there is a substantive change (new MCP tool, new core feature). Patch releases skip these.
- [ ] Update `Last refreshed` to today's date.

## Source

The "submit to every MCP directory" guidance is from the 2026 MCP distribution playbook tracked in the local research notes. The "Smithery uses skills terminology in 2026" detail is confirmed empirically (current archy listing URL pattern).
