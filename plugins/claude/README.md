# archy Claude Code plugin

The lowest-friction install path for archy on Claude Code. Bundles:

- The archy MCP server registration (runs `uvx archy mcp` over stdio so users without a global archy install still get the tool surface)
- The canonical [`archy` skill](skills/archy/SKILL.md) (a byte-identical copy of [`skills/archy/SKILL.md`](../../skills/archy/SKILL.md) at the repo root; `tests/test_plugin.py` asserts the two stay in sync)

What this plugin does **not** ship (current Claude Code plugin constraints, May 2026):

- **Permission allowlist seeding.** The plugin manifest cannot pre-populate `permissions.allow`. Users have to either approve each `archy_*` tool on first call, or paste the snippet in the project README into their own `~/.claude/settings.json`. The eventual `archy install` installer (Phase 1 remainder of [`docs/SPEC_INDEX_AND_INSTALL.md`](../../docs/SPEC_INDEX_AND_INSTALL.md)) will automate this for users who want it.
- **A project-level `CLAUDE.md` snippet.** The plugin contributes instruction context only through the bundled skill, which is the documented mechanism.

## Install (local development)

From a checkout of this repo:

```bash
claude --plugin-dir /path/to/archy/plugins/claude
```

Restart Claude Code after installing for the MCP server to be picked up.

## Install (marketplace)

Not yet published. When the archy marketplace entry is live, the install will be:

```bash
claude plugin install archy@<marketplace>
```

## Layout

```
plugins/claude/
├── .claude-plugin/
│   └── plugin.json          # manifest: MCP server registration + metadata
├── skills/
│   └── archy/
│       └── SKILL.md         # bundled skill; mirror of skills/archy/SKILL.md
└── README.md                # this file
```

## Why uvx and not archy directly?

The MCP server command is `uvx archy mcp` rather than plain `archy mcp` so a user who has `uv` installed but has not separately installed archy still gets a working plugin. Users who installed archy globally (`pip install archy` or `uv tool install archy`) can switch the manifest's `command` to `archy` and drop the leading `args` entry; the plugin works either way once Claude Code resolves the MCP stanza.
