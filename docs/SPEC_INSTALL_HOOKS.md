# Spec: opt-in agent hooks (`archy install --hooks`)

Companion to [`SPEC_INDEX_AND_INSTALL.md`](SPEC_INDEX_AND_INSTALL.md) Part 4 (the installer registry). That work wires archy's MCP server into an agent so the tools are *available*; this proposes an opt-in step that makes the feedback loop *automatic* by registering a lifecycle hook that runs archy after the agent edits code.

Status: **proposed, not built.** Decided 2026-05-23 as the follow-up to the persistent-index work. The local archy-on-archy version (a Claude Code `Stop` hook in this repo's gitignored `.claude/settings.local.json`) already exists; this spec is about generalizing it across the five installer clients for end users.

## Motivation

archy's thesis is that an agent consults it on *every* change, not just at a CI gate. MCP tools make that possible but not automatic: the agent still has to remember to call `archy_diff` / `archy_score`. A post-edit (or end-of-turn) hook that runs `archy score --strict` or `archy diff` closes that gap at the platform level: the structural check runs whether or not the agent remembered. The persistent parse cache (Phase 2 part 1) is what makes this affordable: a warm gate is sub-second to a few seconds even on huge repos, so a hook on every turn is not painful.

## Cross-agent reality (verified 2026-05)

Hooks are not a uniform substrate the way MCP config is. Each client has its own event model and file format, and one client has no hooks at all.

| Client | Hooks? | Where | Useful event(s) | Block semantics |
|---|---|---|---|---|
| Claude Code | yes | `settings.json` (`hooks`) | `Stop`, `PostToolUse`, `SessionStart` | exit 2 (stderr to model) or `{"decision":"block","reason"}` |
| Cursor | yes (1.7+) | `.cursor/hooks.json` | `afterFileEdit`, `stop`, `beforeShellExecution` | stdout JSON (`permission`, `continue`, `agentMessage`) |
| Codex CLI | yes | `hooks.json` or `[hooks]` in `config.toml` | `post-edit`, `pre-commit`, `notify` (agent-turn-complete) | per-event; `notify` is fire-and-forget |
| opencode | yes | TypeScript plugin (`.opencode/plugin/*`) | 25+ lifecycle events | programmatic (plugin returns) |
| Continue | **no** | n/a | n/a | n/a |

Sources: [Claude Code hooks](https://code.claude.com/docs/en/hooks), [Cursor hooks](https://cursor.com/docs/hooks), [Codex hooks](https://developers.openai.com/codex/hooks), [opencode plugins](https://opencode.ai/docs/plugins/). Continue ships config-as-code (rules, MCP) but no shell-hook lifecycle.

Implication: unlike the MCP stanza (a near-identical blob per client), each hook is a **bespoke per-adapter integration** with a different event name, file format, and block contract. There is no shared "stop hook" to emit once.

## Design

### Opt-in, never silent

A hook that runs on every turn is far more intrusive than writing config, so it is **never** part of the default `archy install`. It is gated behind an explicit flag:

```bash
archy install --hooks                 # add the gate hook to detected, hook-capable clients
archy install --hooks --target codex  # one client
archy uninstall --hooks               # remove only the hooks (symmetric)
```

Without `--hooks`, behavior is exactly today's installer.

### Advisory by default, blocking opt-in

Default hook behavior is **advisory**: run the gate, surface the result, never fail the user's turn. A `--hooks-block` modifier (or `archy.yaml` setting) upgrades to blocking where the client supports it (Claude exit 2, Cursor stdout `permission:"deny"`), so a structural regression actually halts the turn until addressed. Advisory is the safe default because a false block (tooling hiccup, missing baseline) must never hold a user's workflow hostage.

### The gate command

One universal command, run from the project root: `archy score --strict` (regression gate against the last recorded run) or, richer, `archy diff` (localizes what regressed). `--strict` degrades gracefully: with no recorded baseline it passes and prints "nothing to compare." The hook wrapper maps a regression to the client's block contract only in blocking mode.

### Reuse the adapter registry

This is a capability *added to the existing adapters*, not a new subsystem. `AgentAdapter` gains an optional, paired pair of renders mirroring the install/uninstall design:

```python
def hook_actions(self, scope: Scope, *, project_root, blocking: bool) -> list[FileAction]: ...
```

returning `FileAction`s whose `render` writes the client's hook config and whose `unrender` strips it (so `archy uninstall --hooks` is the exact inverse, same as the MCP/permission writes). Adapters that cannot host a hook (Continue) return `[]`. opencode is best-effort: its hook is a TypeScript plugin file, which is a heavier artifact than a JSON/TOML stanza; ship it only if the plugin shape proves stable.

Per-adapter target:

- **Claude Code**: `Stop` hook in `settings.json` running the gate wrapper.
- **Cursor**: `afterFileEdit` (or `stop`) entry in `.cursor/hooks.json`; the wrapper reads Cursor's stdin JSON and, in blocking mode, emits `{"permission":"deny","agentMessage":...}`.
- **Codex CLI**: `post-edit` (blocking-capable) or `notify` (advisory) in `config.toml` `[hooks]`.
- **opencode**: a plugin under `.opencode/plugin/` subscribing to a turn/edit-complete event. Best-effort.
- **Continue**: excluded.

### A wrapper script, not an inline command

Each client invokes a small archy-owned wrapper (e.g. `archy hook gate`, a new hidden CLI entry) rather than an inline shell string, so: (a) the cross-platform and exit-code/JSON-contract logic lives in Python, not in five different shell escapings; (b) the per-client block contract is produced by archy from one place; (c) the wrapper can no-op cleanly when archy or a baseline is absent.

## Testing

Same five-layer strategy as the installer ([`SPEC_INSTALL_TESTING.md`](SPEC_INSTALL_TESTING.md)): unit (the `hook_actions` render/unrender per adapter), snapshot (emitted hook config per client x scope), filesystem integration + idempotency, and contract (parse the emitted hook file back with the client's expected schema). The gated E2E layer is the honest ceiling here: actually triggering a Cursor/Codex hook headlessly and asserting the gate ran is valuable but expensive, so it stays release-gated and best-effort, with Continue excluded (no hooks) exactly as in the install E2E matrix.

## Non-goals

- **Default-on hooks.** Opt-in only; the installer's default stays config-only.
- **Blocking by default.** Advisory first; blocking is an explicit upgrade.
- **A cross-agent hook abstraction.** The event models differ too much; each adapter owns its own emit. archy provides the uniform *gate command*, not a uniform hook format.
- **Continue hooks.** No lifecycle to hang one on.
- **Replacing CI gates.** Hooks are a local convenience; `archy check` / `archy cycles --strict` in CI remain the committed enforcement floor.

## Open questions

1. **Which event per client for "after a change settled"?** Claude `Stop` and Codex `notify` fire at turn end (good for a one-shot gate); Cursor `afterFileEdit` and Codex `post-edit` fire per edit (noisier but earlier). Pick per-adapter to match "settled," likely turn-end where available.
2. **Baseline lifecycle.** `archy score --strict` needs a recorded baseline to gate against. Should `--hooks` also install a `SessionStart`-style hook that records one (where supported), or rely on the agent/CI to seed `.archy/history.jsonl`? Leaning on the latter to avoid writing history on every session.
3. **Project vs user scope.** Mirror the installer's `--location global|local`. Project-scope hooks (committed) enforce for a whole team but are a bigger imposition; default to local.
