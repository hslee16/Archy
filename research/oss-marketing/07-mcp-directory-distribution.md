# MCP-ecosystem distribution (2026 directories)

**Sources:**
- https://dev.to/toolstem/i-built-the-mcp-server-greg-isenberg-recommends-in-his-2026-distribution-playbook-heres-day-7-3c33
- https://glama.ai/mcp/servers
- https://smithery.ai/
**Type:** Vendor + community guide
**Date accessed:** 2026-05-16

## Key insights

- **Six canonical MCP directories** in 2026: Smithery, Glama, mcp.so, PulseMCP, the official `modelcontextprotocol/servers` registry, `awesome-mcp-servers`. Plus Apify Store for the agent-runtime niche.
- **Glama has ~24k servers** in its registry (was 10k earlier in 2026 per older summary). It's mostly a scrape + score model; you submit metadata and it auto-scores. Already done for archy (the Glama badge is in the README).
- **Smithery is the discovery surface for "AI-native apps"**; more curated than Glama, separate submission flow, different audience (more agent-builder, less directory-browser).
- **Listing in all six is "clerical work"; ~1 week of effort, real cost but linear.** No magic; you have to actually do it.
- The official `modelcontextprotocol/servers` registry submission requires a PR to the upstream repo. Curated.
- `awesome-mcp-servers` is a single GitHub README; PR with a one-line entry under the right section.
- Ranking signals across directories vary: Glama scores on code-quality + permission scope + freshness. Smithery weights install-count and reviews.

## Applicability to archy

- **Current state (confirmed 2026-05-16):**
  - Glama: https://glama.ai/mcp/servers/hslee16/archy; done
  - Smithery: https://smithery.ai/skills/alex-1c6e/archy; done (note: Smithery uses "skills" not "servers" terminology in 2026)
  - mcp.so, PulseMCP, official registry, awesome-mcp-servers: not yet submitted
- **Action: a one-week clerical sprint to land in the other 5 directories.** Each has its own submission flow but the inputs are identical (name, description, install command, MCP config snippet, screenshot/demo).
- **Pre-submit checklist** (do once, reuse everywhere):
  - 50-char tagline: "Architectural sensor for Python codebases under AI-assisted coding"
  - 200-char description: include "Python", "architecture", "import graph", "MCP", "Claude Code", "Cursor"; those are the searched terms
  - Install: `pip install archy` then `{"mcpServers":{"archy":{"command":"archy","args":["mcp"]}}}`; copyable
  - Screenshot: `archy_graph_summary` output rendered nicely, or a Claude-Code session showing `archy_high_risk_modules` being called
  - Demo video (optional but Smithery weights it): 60s screencap of a Claude-Code session using archy tools to avoid a regression
- **Tag selection matters.** Across directories the discoverable buckets are: "code analysis", "python", "developer tools", "code quality", "architecture". Pick all that apply consistently across submissions.
- **Update cadence:** every release, push a one-line update to each directory. This is the kind of thing that should be a `make publish-mcp-directories` script; not done manually each time. Realistic v1: a `docs/MCP_DIRECTORIES.md` checklist file with submission URLs and last-update dates per directory.
