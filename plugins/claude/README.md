# archy Claude Code plugin

The lowest-friction install path for archy on Claude Code. Bundles:

- The archy MCP server registration (runs `uvx archy>=0.25 mcp` over stdio so users without a global archy install still get the tool surface; the `>=0.25` pin guarantees `archy_affected` is exposed)
- The canonical [`archy` skill](skills/archy/SKILL.md) (a byte-identical copy of [`skills/archy/SKILL.md`](../../skills/archy/SKILL.md) at the repo root; `tests/test_plugin.py` asserts the two stay in sync)

What this plugin does **not** ship (current Claude Code plugin constraints, May 2026):

- **Permission allowlist seeding.** The plugin manifest cannot pre-populate `permissions.allow`. Users have to either approve each `archy_*` tool on first call, paste the snippet in the project README into their own `~/.claude/settings.json`, or run `uvx archy install` (see [`docs/INSTALL.md`](../../docs/INSTALL.md)), which detects this plugin, skips re-registering the MCP server to avoid double-registration, and seeds the allowlist for you. `uvx archy uninstall` removes it again.
- **A project-level `CLAUDE.md` snippet.** The plugin contributes instruction context only through the bundled skill, which is the documented mechanism.

## Before you install

Two gotchas worth knowing up front:

**(1) If you already wired archy in manually, remove it first.** If `~/.claude/settings.json` already has an `mcpServers.archy` stanza from a previous manual install, every archy tool will appear twice once the plugin loads (once as `mcp__archy__*` from the manual stanza, once as `mcp__plugin_archy_archy__*` from the plugin). Both work, but the agent sees 32 tool descriptions instead of 16 and has to disambiguate. Remove the manual stanza before installing the plugin, or remove it after if you preferred the shorter prefix.

**(2) If you've installed archy as a `uv tool`, make sure it's recent.** `uvx` prefers an installed tool over fetching from PyPI, so a stale `uv tool install archy` (say, from a year ago) will mask the v0.25 release the plugin pins to. The `archy>=0.25` specifier in the manifest will refuse to use an older installed tool, but to avoid the confusion either remove the install (`uv tool uninstall archy`) and let `uvx` cache fresh, or refresh it deliberately (`uv tool upgrade archy`).

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

The MCP server command is `uvx archy>=0.25 mcp` rather than plain `archy mcp` so a user who has `uv` installed but has not separately installed archy still gets a working plugin. The `>=0.25` specifier pins to the release that introduced `archy_affected`, so a stale `uv tool install archy` from before that release can't quietly mask 3 of the 16 advertised tools. Users who installed archy globally (`pip install archy` or `uv tool install archy`) can switch the manifest's `command` to `archy` and drop the leading `args` entry; the plugin works either way once Claude Code resolves the MCP stanza.
