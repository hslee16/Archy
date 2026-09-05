# MCP directory submissions

Tracks where archy is listed as an MCP server. Every release, refresh the description on every listed directory to match the new version. Treat this file the way you treat `CHANGELOG.md`: it lives or dies with each release.

## Listed

| Directory | URL | Current version | Last verified | State |
|---|---|---|---|---|
| Official MCP registry | https://registry.modelcontextprotocol.io (`io.github.hslee16/archy`) | 0.46.2 | 2026-09-05 | auto-published on release |
| Glama | https://glama.ai/mcp/servers/hslee16/archy | tracks PyPI | 2026-09-05 | self-updating, no action |
| Smithery | https://smithery.ai/skills/alex-1c6e/archy (Smithery uses "skills" terminology in 2026) | listed | 2026-09-05 | **description stale** |

**Verify a listing by querying it, never by reading this table.** That is the
only method that has found anything here. The official registry sat at 0.13.3
for 33 minor versions while this file, the release checklist and a passing test
all said `server.json` was current, because every one of them checked the FILE
and none of them checked the REGISTRY. What eventually surfaced it was an
outside robot: `chryaner/does-it-install` had archy recorded as
`handshake_failed` on macOS, Linux and Windows, three weeks before anyone here
noticed (#462).

Run the published command too, not just the version. The registry's entry named
`uvx archy`, which is the bare console script: it printed usage, exited 2, and
every registry-driven install failed the handshake. A version number can look
perfect while the command it ships does not start.

Current state, each verified live on 2026-09-05:

- **Official registry** publishes itself now. `publish.yml`'s `registry` job runs
  on release via GitHub OIDC (#462), so the entry follows `server.json`, which
  `tests/test_server_json.py` keeps in step with `pyproject.toml` and now also
  checks declares the `mcp` subcommand.
- **Glama** renders the README and a live PyPI version badge, so it tracks
  releases on its own. Nothing to do per release.
- **Smithery**'s description is stale: it says "four-axis quality score" when the
  score has five axes (modularity, acyclicity, depth, equality, complexity). It
  predates the complexity axis. Fixing it needs a login, so it is maintainer-side.

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
Python architecture analysis: import graph, cycles, layer rules, five-axis score, refactor-priority hotspots, DSM, and an MCP server (13 tools) so coding agents see structural impact before they commit.

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

- [x] ~~Republish the official registry entry.~~ **Automated.** The `registry`
      job in `publish.yml` publishes `server.json` on every release. Still bump
      both version fields in `server.json`; `tests/test_server_json.py` fails CI
      if you forget. After the release, confirm the LIVE entry moved, because a
      green job is not the same fact as a changed listing.
- [ ] Update `Current version` columns in the table above.
- [ ] Glama: log in, navigate to https://glama.ai/mcp/servers/hslee16/archy, edit metadata to match the new tagline / short description if anything changed.
- [ ] Smithery: same flow at https://smithery.ai/skills/alex-1c6e/archy.
- [ ] mcp.so / PulseMCP / official registry / awesome-mcp-servers / awesome-claude-code-toolkit: only refresh when there is a substantive change (new MCP tool, new core feature). Patch releases skip these.
- [ ] Update `Last refreshed` to today's date.

## Source

The "submit to every MCP directory" guidance is from the 2026 MCP distribution playbook tracked in the local research notes. The "Smithery uses skills terminology in 2026" detail is confirmed empirically (current archy listing URL pattern).
