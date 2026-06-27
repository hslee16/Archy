# Installing archy into your AI agents

archy ships as an MCP server. "Installing" it means telling your AI coding
agents to launch that server (`uvx archy mcp`) and giving the agent the context
to use archy's tools. This guide covers every way to do that, what each one
writes, and how to remove it.

## TL;DR

```bash
uvx archy install        # detect your agents, confirm, wire them all up
```

Then restart your agent client(s). To remove everything later:

```bash
uvx archy uninstall
```

## What gets installed (and what does not)

`archy install` edits configuration files in the agent clients you already
have. It does **not** install a binary, and it does **not** install the Claude
Code plugin. Per client it writes up to three things:

1. **An MCP server registration** pointing at `uvx archy mcp` (stdio). This is
   the functional wiring: the client launches the archy server on startup.
   Because it uses `uvx`, archy itself is fetched and run on demand from PyPI;
   nothing is copied onto your PATH.
2. **An instructions/rules file** with a short, marker-fenced block telling the
   agent *when* to call the tools (the snapshot -> impact -> diff loop).
3. **A permission allowlist (Claude Code only)** so you are not prompted to
   approve each of the 13 `mcp__archy__*` tools on first use.

It does **not** phone home, create an account, auto-update, or install the
`archy` CLI. (If you want the `archy` command on your PATH for direct CLI use,
that is a separate step: see [Installing the archy CLI](#installing-the-archy-cli-itself).)

## Install surfaces

There are three ways to wire archy in, plus the optional CLI install.

### 1. `archy install` (recommended, all clients)

Auto-detects which clients are present and configures each one. Idempotent, and
unrelated config in shared files is preserved.

```bash
uvx archy install                              # detect, confirm, wire all
uvx archy install --target cursor,codex --yes  # non-interactive, specific clients
uvx archy install --target all --yes           # every known client, detected or not
uvx archy install --location local             # configure just the current project
uvx archy install --print-config claude        # preview one client's config, write nothing
uvx archy install --no-permissions             # skip the Claude allowlist seed
```

`pipx run archy install` works too if you use pipx instead of uv.

### 2. Claude Code plugin (Claude Code only)

The bundled plugin at [`plugins/claude/`](../plugins/claude/) packages the MCP
server registration and the canonical `archy` skill as one installable unit.
From a checkout:

```bash
claude --plugin-dir /path/to/archy/plugins/claude
```

Or install it from inside Claude Code via the marketplace (updates managed by
Claude Code):

```text
/plugin marketplace add hslee16/archy
/plugin install archy@archy
```

See [plugin vs installer](#plugin-vs-installer-which-should-i-use) for when to
choose which.

### 3. Manual MCP stanza (any client)

For a client the installer does not know about, add this stanza to its MCP
config by hand:

```json
{
  "mcpServers": {
    "archy": { "command": "uvx", "args": ["archy", "mcp"] }
  }
}
```

Running from a checkout instead of PyPI? Use `{"command": "uv", "args": ["run", "archy", "mcp"]}`.

### Installing the archy CLI itself

The installer does not need archy on your PATH (the MCP stanza uses `uvx`). But
if you want to run `archy score`, `archy dsm`, etc. directly:

```bash
uv tool install archy     # or: pipx install archy, or: pip install archy
```

## Plugin vs installer: which should I use?

Both point at the same `uvx archy mcp` entry point; they are different
distribution channels.

| | `archy install` | Claude Code plugin |
|---|---|---|
| Clients | Claude Code, Cursor, Codex, opencode, Continue | Claude Code only |
| Acquisition | run a command in your shell | `/plugin marketplace add hslee16/archy` then `/plugin install archy@archy` |
| Seeds Claude `permissions.allow` | yes | no (loader limitation) |
| Ships the `archy` skill as a first-class skill | no (drops a `CLAUDE.md` block) | yes |
| Updates | you re-run / edit config | managed by Claude Code |
| Scriptable / CI | yes (`--yes`, `--target`, `--print-config`) | no |

They compose: if the plugin is installed, `archy install` detects it, skips
re-registering the MCP server (so tools do not appear twice), and only seeds the
permission allowlist the plugin cannot write. Practical guidance:

- **On Claude Code today:** `uvx archy install` is the simplest complete setup
  (it includes the permission seed). Use the plugin if you also want the skill
  as a first-class object.
- **On any other client:** the installer is the only option.

## What each client receives

`--location global` configures every project; `--location local` writes
project-scoped files under the current directory (or `--project-root`).

| Client | MCP config (global / local) | Instructions (global / local) | Permissions |
|---|---|---|---|
| Claude Code | `~/.claude.json` / `<project>/.mcp.json` | `~/.claude/CLAUDE.md` / `<project>/CLAUDE.md` | `~/.claude/settings.json` (or `<project>/.claude/settings.json`) |
| Cursor | `~/.cursor/mcp.json` / `<project>/.cursor/mcp.json` | `.cursor/rules/archy.mdc` (owned) | n/a |
| Codex CLI | `~/.codex/config.toml` / `<project>/.codex/config.toml` | `~/.codex/AGENTS.md` / `<project>/AGENTS.md` | n/a |
| opencode | `~/.config/opencode/opencode.json` / `<project>/opencode.json` | `AGENTS.md` (alongside config) | n/a |
| Continue | `~/.continue/mcpServers/archy.yaml` / `<project>/.continue/...` (owned) | `.continue/rules/archy.md` (owned) | n/a |

On Windows the homedir-anchored paths resolve under `%USERPROFILE%`, and
opencode's global config lives under `%APPDATA%\opencode\`. "owned" marks files
archy creates exclusively; everything else is a shared file archy merges into.

## Uninstalling

`archy uninstall` is the exact inverse and takes the same flags:

```bash
uvx archy uninstall                       # remove from detected clients (after confirm)
uvx archy uninstall --target all --yes     # remove from every known client
uvx archy uninstall --location local       # remove this project's config
uvx archy uninstall --dry-run              # list what would change, do nothing
```

For **shared** files (`~/.claude.json`, `mcp.json`, `config.toml`,
`opencode.json`, `settings.json`, your own `CLAUDE.md`/`AGENTS.md`) it removes
only archy's contribution and leaves the rest byte-for-byte. For files archy
**owns** (Continue's `archy.yaml`, the `archy.mdc`/`archy.md` rule files, and any
instruction file that contained nothing but archy's block) it deletes the file.
Uninstall is idempotent: running it on a clean machine does nothing.

## Flag reference

Both commands share `--target`, `--location`, `--project-root`, `--yes`, and
`--no-permissions`. `install` adds `--print-config <id>` (preview one client);
`uninstall` adds `--dry-run` (preview all removals).

- `--target` accepts `auto` (detected clients only), `all` (every known client),
  or a comma list of ids (`claude,cursor,codex,opencode,continue`).
- `--location` is `global` (default) or `local`.
- `--no-permissions` leaves Claude's allowlist untouched in both directions.

## Troubleshooting

- **Tools appear twice in Claude Code** (`mcp__archy__*` and
  `mcp__plugin_archy_archy__*`): you have both a manual `mcpServers.archy` stanza
  and the plugin. Remove one. `archy install` avoids creating this by detecting
  the plugin and skipping the manual stanza.
- **`archy_what_to_refactor_next` (or other newer tools) missing:** a stale `uv tool install
  archy` can shadow the PyPI release `uvx` would fetch. Run `uv tool upgrade
  archy`, or `uv tool uninstall archy` and let `uvx` fetch fresh.
- **`Could not write ...: the file may be open in a running client`** (Windows):
  Electron clients hold an exclusive handle on their config. Close the client and
  re-run.
- **Permission prompts still appear in Claude Code:** the allowlist seed only
  applies to the scope you installed. Re-run with the matching `--location`, or
  paste the `permissions.allow` block manually.

## For maintainers: adding a client

Each client is a small adapter in
[`src/archy/install/adapters/`](../src/archy/install/adapters/) declaring how to
detect the client, where to write its config, and the paired `render` /
`unrender` (install / uninstall) for each file. Add the class to
[`registry.py`](../src/archy/install/registry.py) and mirror the per-adapter
tests. The design and cross-OS detection strategy are in
[`SPEC_INDEX_AND_INSTALL.md`](SPEC_INDEX_AND_INSTALL.md) Part 4; the test
strategy is in [`SPEC_INSTALL_TESTING.md`](SPEC_INSTALL_TESTING.md).
