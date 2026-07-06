# archy Claude Code plugin

The lowest-friction install path for archy on Claude Code. Bundles:

- The archy MCP server registration. It launches through a small Node shim (`bin/archy-mcp.mjs`) that finds a working way to run `archy mcp` over stdio: an installed `archy` (any method), else `uvx archy>=0.36,<1.0 mcp`, else `python -m archy mcp`. The lower bound guarantees the full current 0.x tool set, including `archy_what_to_refactor_next`, is exposed, and the `<1.0` cap keeps a future breaking 1.0 from being auto-pulled. `uv` is no longer a hard requirement (see ["Why a Node shim"](#why-a-node-shim) below).
- The canonical [`archy` skill](skills/archy/SKILL.md) (a byte-identical copy of [`skills/archy/SKILL.md`](../../skills/archy/SKILL.md) at the repo root; `tests/test_plugin.py` asserts the two stay in sync)

What this plugin does **not** ship (current Claude Code plugin constraints, May 2026):

- **Permission allowlist seeding.** The plugin manifest cannot pre-populate `permissions.allow`. Users have to either approve each `archy_*` tool on first call, paste the snippet in the project README into their own `~/.claude/settings.json`, or run `uvx archy install` (see [`docs/INSTALL.md`](../../docs/INSTALL.md)), which detects this plugin, skips re-registering the MCP server to avoid double-registration, and seeds the allowlist for you. `uvx archy uninstall` removes it again.
- **A project-level `CLAUDE.md` snippet.** The plugin contributes instruction context only through the bundled skill, which is the documented mechanism.

## Before you install

Two gotchas worth knowing up front:

**(1) If you already wired archy in manually, remove it first.** If `~/.claude/settings.json` already has an `mcpServers.archy` stanza from a previous manual install, every archy tool will appear twice once the plugin loads (once as `mcp__archy__*` from the manual stanza, once as `mcp__plugin_archy_archy__*` from the plugin). Both work, but the agent sees 28 tool descriptions instead of 14 and has to disambiguate. Remove the manual stanza before installing the plugin, or remove it after if you preferred the shorter prefix.

**(2) If you've installed archy as a `uv tool`, make sure it's recent.** When the shim falls through to `uvx`, `uvx` prefers an installed tool over fetching from PyPI, so a stale `uv tool install archy` (say, from a year ago) will mask the release the plugin pins to. The `archy>=0.36,<1.0` specifier the shim passes will refuse to use an older installed tool, but to avoid the confusion either remove the install (`uv tool uninstall archy`) and let `uvx` cache fresh, or refresh it deliberately (`uv tool upgrade archy`). (If you have a current `archy` on `PATH`, the shim uses it directly and never reaches `uvx`.)

## Install (local development)

From a checkout of this repo:

```bash
claude --plugin-dir /path/to/archy/plugins/claude
```

Restart Claude Code after installing for the MCP server to be picked up.

## Install (marketplace)

Add the archy marketplace, then install the plugin, from inside Claude Code:

```text
/plugin marketplace add hslee16/archy
/plugin install archy@archy
```

This pulls the plugin straight from the GitHub repo, with updates managed by
Claude Code.

## Layout

```
plugins/claude/
├── .claude-plugin/
│   └── plugin.json          # manifest: MCP server registration + metadata
├── bin/
│   └── archy-mcp.mjs        # Node shim that resolves a way to run `archy mcp`
├── skills/
│   └── archy/
│       └── SKILL.md         # bundled skill; mirror of skills/archy/SKILL.md
└── README.md                # this file
```

## Why a Node shim?

The manifest runs `node ${CLAUDE_PLUGIN_ROOT}/bin/archy-mcp.mjs` rather than `uvx archy ... mcp` directly. The shim removes the previous **hard `uv` requirement**: before, a user without `uv` on `PATH` got a silent "server failed to start" and none of the `archy_*` tools loaded. Now the shim tries, in order, an installed `archy` (from any of `pipx`/`uv tool`/`pip`), then `uvx archy>=0.36,<1.0 mcp` (which fetches archy on demand if `uv` is present), then `python -m archy mcp`; if none are available it prints an actionable message telling you exactly what to install. The version bound is unchanged in meaning: the lower bound exposes the full current tool set (e.g. `archy_what_to_refactor_next`) and the `<1.0` cap stops a future breaking 1.0 from being auto-pulled.

`node` is the launcher rather than a shell script because Claude Code spawns MCP servers without a shell and there is no per-OS override in the plugin manifest, so a POSIX `.sh` would not run on Windows. `node` is the one interpreter Claude Code guarantees on `PATH` across macOS, Linux, and Windows, so a single `.mjs` entry point covers all three: it spawns `.exe` launchers (how `uv`, `pip`, and `python` ship on Windows) directly, and routes the rarer `.cmd`/`.bat` shims through `cmd.exe`. It also skips unusable matches (a directory or non-executable file sharing the name, or the 0-byte Windows Store `python` alias) and falls through to the next candidate.

**No prerequisite for the common case.** If you have `uv` *or* any `archy` install, the tools just work. If you have neither, you will see a one-line hint to run `curl -LsSf https://astral.sh/uv/install.sh | sh` (uv bootstraps its own Python) or `pipx install archy`, then restart Claude Code. The shim's version specifier (`archy>=0.36,<1.0`) lives in `bin/archy-mcp.mjs`; `tests/test_plugin.py` pins it so the README and launcher cannot drift.
