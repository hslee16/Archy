# Spec: persistent index, file watcher, `archy affected`, and install DX

Inspired by research into [colbymchenry/codegraph](https://github.com/colbymchenry/codegraph), which proved two things on real benchmarks:

1. A persistent SQLite-backed graph plus a native FS watcher makes agent-facing tools fast enough that agents actually call them on every change.
2. A one-shot installer (`npx ...`) that auto-detects the agent, writes MCP config, drops a rules/instructions file, and seeds a permission allowlist removes the biggest adoption drop-off.

Archy and CodeGraph play different roles. CodeGraph is the *librarian* (symbol-level lookup for Explore agents). Archy is the *judge* (risk, complexity, cycles, DSM, hotspots, diff). The features in this spec import CodeGraph's *plumbing* without diluting archy's role: archy is not adding tree-sitter, symbol search, or a code-dump tool.

---

## Outcomes

- **Sub-second steady-state MCP calls** on pytorch-scale repos (2,252 modules). Today every call re-parses; after this work, steady-state calls are SQL reads.
- **`archy_record_baseline` and `archy_trend` become cheap enough to densify.** Today users record baselines sparingly because it is slow; after this work, recording is near-free, so trend data becomes useful.
- **`archy affected` gives archy a first-class CI/pre-commit story.** Pairs with the risk-weighted `archy_diff` shipped in v0.24.0: `affected` finds the blast radius, `diff` scores it.
- **Install drops to one command** across Claude Code, Cursor, Codex, opencode, Continue, and anything else that speaks MCP.
- **Churn data falls out for free** once the index is hash-keyed and persistent, unlocking a churn × complexity hotspot signal for `archy_hotspots` and `archy_high_risk_modules` in a follow-up.

Honest framing on token savings: archy already returns verdicts, not code, so per-call token cost is small. The win is not "90% fewer tool calls" (that is CodeGraph's pitch and archy does not compete there). The win is that archy stays cheap enough that agents reach for it on every change.

---

## Part 1: persistent index

### Storage

SQLite via the stdlib `sqlite3` module. No new runtime dependency, no `better-sqlite3`/WASM-fallback story to maintain. Database lives at `.archy/index.db` (sibling to baselines).

Schema sketch:

```
files(path PRIMARY KEY, mtime, size, sha256, last_parsed_at)
modules(qualname PRIMARY KEY, path, is_package, external, last_seen_at)
edges(src_qualname, dst_qualname, lines, is_relative, PRIMARY KEY (src, dst))
metrics(qualname, complexity, instability, ... , last_computed_at)
churn(qualname, change_count, last_changed_at)   -- populated lazily from git or watcher
schema_version(version)
```

The shapes mirror what `_graph_to_dict` and the scoring pipeline already emit. The DB is a cache, not a source of truth: deleting `.archy/` must always be safe.

### Incremental sync

`archy index sync` (and the in-process equivalent the MCP server calls before every tool) does:

1. Walk source roots, hash each file (cheap: stat → compare mtime/size → only sha256 if those changed).
2. For changed files: re-parse, diff modules/edges against DB, upsert.
3. For deleted files: tombstone their modules and any edges touching them.
4. Recompute metrics for the affected closure (parents-of-changed in the dep graph).

Worst case (cold cache, full repo) matches today's cost. Warm case (no changes) is one stat-walk plus a few SQL reads.

### Invalidation correctness

The single subtle failure mode is metric drift: a module's complexity score depends only on itself, but instability depends on its neighborhood. The recompute closure must include any module whose edges changed, not just modules whose source changed. Tests must cover: rename, delete-then-readd, import-target-rename, and circular-recompute.

### Backwards compatibility

`archy_record_baseline` keeps emitting the same on-disk JSON it does today. The index is purely a cache layer underneath. A user who blows away `.archy/index.db` loses no data, just speed.

---

## Part 2: file watcher

`watchdog` (already cross-platform, uses FSEvents on macOS, inotify on Linux, ReadDirectoryChangesW on Windows; the same OS primitives CodeGraph uses through `chokidar`).

- Started by the MCP server only when serving (`archy mcp`). The CLI does not need it.
- Debounces with a 2-second quiet window via `threading.Timer` reset on each event.
- Filters to source files matching configured `languages`/`exclude` (initially just Python, matching archy's current scope).
- On debounce fire: runs the same incremental sync as Part 1.
- Surfaces a `last_synced_at` field on `archy_status` so agents can sanity-check freshness.

Out of scope: watching for git operations, branch switches, or remote refs. A user who switches branches can wait one sync cycle.

---

## Part 3: `archy affected`

New CLI subcommand and matching MCP tool. Reuses the existing impact graph; no new analysis.

```bash
archy affected src/foo.py src/bar.py            # explicit files
git diff --name-only | archy affected --stdin   # piped
archy affected src/foo.py --filter "tests/**"   # custom test glob
archy affected --depth 3 --json                 # tune & emit JSON
```

Flags mirror CodeGraph's `affected` for muscle-memory portability:

| Flag | Description | Default |
|---|---|---|
| `--stdin` | Read file list from stdin | off |
| `-d, --depth <n>` | Max reverse-dep traversal depth | 5 |
| `-f, --filter <glob>` | Glob identifying test files | auto-detect `test_*.py`, `*_test.py`, `tests/**` |
| `-j, --json` | JSON output | off |
| `-q, --quiet` | Paths only (one per line, no headers) | off |

Output by default groups the impacted set into "tests to run" and "modules touched downstream." `--json` emits both as arrays.

The MCP equivalent (`archy_affected`) takes a list of paths and returns the same shape. Same risk-weighting hook as `archy_diff` could layer on later: "of the N affected tests, here are the M whose modules carry highest risk."

---

## Part 4: install DX

This is the question worth thinking through carefully. The user asked specifically whether this should use Claude plugins and how to generalize across providers.

### Distribution

Archy is a Python tool, so the natural `npx`-equivalent is `uvx`:

```bash
uvx archy install        # one-shot, no global install needed
```

Alternatively `pipx run archy install` for users without `uv`. Both go in the README.

### Three install surfaces, ranked by DX

**(a) Claude Code plugin (best DX, Claude Code only)**

Claude Code plugins package an MCP server, slash commands, agents, hooks, and CLAUDE.md snippets into a single installable unit. A user runs `/plugin install archy` and everything is wired up: MCP server registered, `permissions.allow` seeded, the agent-facing instruction snippet appended to CLAUDE.md.

Recommendation: yes, publish a Claude plugin. It is the lowest-friction path for the largest segment of archy's users. The plugin manifest points at `uvx archy mcp` so there is no separate binary to distribute. The plugin repo can live under the `archy/` GitHub org as `archy-claude-plugin`, or inline in this repo under `plugins/claude/`.

**(b) `archy install` interactive installer (covers everything else)**

For Cursor, Codex CLI, opencode, Continue, Zed, and any future MCP client, we need a generalized installer. Mirror CodeGraph's UX:

```
$ uvx archy install
? Which agents would you like to configure?
  [x] Claude Code      (detected at ~/.claude.json)
  [x] Cursor           (detected at ~/.cursor/)
  [ ] Codex CLI        (not detected)
  [ ] opencode         (not detected)
? Configure for all projects or just this one? (global / local)
? Add archy to permission allowlists where supported? (Y/n)
```

The installer:

1. Auto-detects installed agents using a layered probe (see "Detection" below). Cross-platform across Linux, macOS, and Windows, matching archy's existing OS support matrix.
2. Writes the MCP server config in each target's expected format (Claude `~/.claude.json`, Cursor `~/.cursor/mcp.json`, Codex `~/.codex/config.toml`, opencode `opencode.json`, etc.); per-OS path table in "Detection" below.
3. Writes the rules/instructions file (`CLAUDE.md` snippet, `.cursor/rules/archy.mdc`, `~/.codex/AGENTS.md`).
4. Seeds permission allowlist (Claude only, today; others as they ship the feature).
5. Supports non-interactive mode for CI/scripting: `--yes`, `--target=cursor,claude`, `--location=local|global`, `--print-config <id>`.

The installer is the same binary as everything else, just a different subcommand. Code-wise it is a registry of "agent adapters," each adapter knowing how to detect, where to write, and what content to emit.

### Detection

Each adapter's `detect()` is a layered probe across three signals; any hit returns true. This catches "agent installed but never launched" (CLI on PATH but no config dir yet) and "agent launched but not on PATH" (Electron desktop apps that don't register a CLI), in addition to the baseline "agent has been run before" (config dir exists).

```python
def detect(self) -> bool:
    if shutil.which(self.cli_name):                       # CLI on PATH (all OSes)
        return True
    if any(p.exists() for p in self.config_paths()):      # platform-aware config dirs
        return True
    if sys.platform == "darwin" and any(p.exists() for p in self.mac_app_bundles):
        return True
    if sys.platform == "win32" and any(p.exists() for p in self.windows_install_dirs):
        return True
    return False
```

Resolve all paths via `pathlib.Path` and `os.environ` lookups (`APPDATA`, `LOCALAPPDATA`, `USERPROFILE`) or `Path.home()`. No hardcoded separators.

Per-adapter path table (config paths only; CLI names follow `claude` / `cursor` / `codex` / `opencode` / `continue`):

| Client | Linux / macOS | Windows |
|---|---|---|
| Claude Code | `~/.claude.json` | `%USERPROFILE%\.claude.json` |
| Cursor | `~/.cursor/mcp.json` | `%USERPROFILE%\.cursor\mcp.json` |
| Codex CLI | `~/.codex/config.toml` | `%USERPROFILE%\.codex\config.toml` |
| opencode | `~/.config/opencode/opencode.json` (+ project-local `opencode.json`) | `%APPDATA%\opencode\opencode.json` (+ project-local) |
| Continue | `~/.continue/` | `%USERPROFILE%\.continue\` |

Secondary probes (used only when CLI and config probes both miss):

- **macOS app bundles**: `/Applications/Claude.app`, `/Applications/Cursor.app`, plus `~/Applications/...`.
- **Windows per-user install dirs**: `%LOCALAPPDATA%\Programs\cursor\Cursor.exe`, `%LOCALAPPDATA%\AnthropicClaude\` (or `%LOCALAPPDATA%\Programs\Claude\`), `%LOCALAPPDATA%\Programs\Microsoft VS Code\` (for the Continue-via-VS-Code case). Registry walks of `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall` via stdlib `winreg` are available as a fallback if a specific adapter needs them, gated behind `sys.platform == "win32"`; not used at launch.

### Cross-platform write-side considerations

- **Atomic writes.** Use write-temp-then-`os.replace` for every config write. On Windows, `os.replace` can fail with `PermissionError` if the target file is held open by a running agent client (Electron apps hold exclusive handles); the installer surfaces a clear "please close $CLIENT and re-run" error rather than retrying indefinitely.
- **Paths in emitted configs.** Emit OS-native paths (forward slashes on Unix, backslashes on Windows) and let each client normalize; do not hand-rewrite separators. JSON files written via `json.dump` are safe; TOML via `tomllib`/`tomli-w` is safe.
- **Line endings.** Open config files in text mode and let Python pick the platform default. Do not hand-format with explicit `\n`.
- **No `chmod` calls on Windows.** Gate any executable-bit manipulation behind `sys.platform != "win32"`.
- **Permission allowlist seeding** (Claude Code) uses the same homedir `~/.claude.json` on Windows, so no OS-specific branching for that adapter beyond path resolution.

### CI coverage

The install code is the kind that silently breaks on the platform you don't develop on. The CI matrix runs at least `archy install --print-config <id>` (dry-run mode) on `ubuntu-latest`, `macos-latest`, and `windows-latest` for each adapter, with emitted-config snapshots validated per OS. Detection logic is unit-tested by monkeypatching `Path.exists` / `shutil.which` / `sys.platform`.

Full testing strategy (five layers: unit, snapshot, filesystem integration, contract, gated E2E) lives in [`SPEC_INSTALL_TESTING.md`](SPEC_INSTALL_TESTING.md).

**(c) Manual MCP config (always available, for power users and unknown clients)**

The README keeps the current manual snippet for users on tools the installer does not know about. Anything that speaks MCP can wire archy in by hand:

```json
{
  "mcpServers": {
    "archy": { "command": "uvx", "args": ["archy", "mcp"] }
  }
}
```

### Why a registry of adapters beats one-installer-per-agent

CodeGraph took the registry approach (their installer ships adapters for Claude/Cursor/Codex/opencode) and it scales: each new agent is a small adapter, not a new binary. Archy should do the same. The adapter interface is roughly:

```python
class AgentAdapter(Protocol):
    id: str                       # "claude", "cursor", ...
    name: str
    cli_name: str                 # binary name probed via shutil.which
    def config_paths(self) -> list[Path]: ...       # platform-aware (Linux/macOS/Windows)
    def mac_app_bundles(self) -> list[Path]: ...    # macOS fallback, may be empty
    def windows_install_dirs(self) -> list[Path]: ...  # Windows fallback, may be empty
    def detect(self) -> bool: ...
    def write_mcp_config(self, scope: Scope) -> None: ...
    def write_instructions(self, scope: Scope) -> None: ...
    def seed_permissions(self, scope: Scope) -> None: ...  # optional
```

Five adapters at launch: Claude Code, Cursor, Codex CLI, opencode, Continue. Add more as MCP spreads.

### Plugin vs installer: do we need both?

Yes. The Claude plugin is strictly better DX *for Claude Code users*, and Claude Code is the largest single audience. The installer covers everyone else and remains the fallback when a user is on multiple agents. Maintenance cost is low because the plugin manifest is ~20 lines and points at the same `uvx archy mcp` entry point the installer configures.

The installer should *detect* when a Claude plugin is already installed and skip re-writing the Claude config to avoid double-registration.

### What we explicitly do not do

- Ship a Node-based installer just because CodeGraph does. `uvx` is the Python-native equivalent and avoids dragging in a JS toolchain.
- Build a hosted "archy cloud" account flow. Everything stays local, matching archy's current posture.
- Auto-update or phone-home. The installer writes config files and exits.

---

## Sequencing

Two natural phases. Phase 1 ships standalone value; Phase 2 depends on Phase 1's index.

**Phase 1: install DX + `archy affected`**

1. `archy install` registry with five adapters (Claude, Cursor, Codex, opencode, Continue).
2. Claude Code plugin manifest at `plugins/claude/`.
3. `archy affected` CLI + `archy_affected` MCP tool. Pure Python, no new deps, reuses existing impact graph.
4. README: benchmark-style table modeled on CodeGraph's, showing archy's wins ("X% of diffs flagged as high-risk that traditional review missed" or similar; we have `bench/` to source numbers).

This phase is mostly CLI plumbing and packaging. Days, not weeks.

**Phase 2: persistent index + watcher**

5. SQLite cache layer behind the existing parser. Cold-path unchanged, warm-path fast.
6. `watchdog`-driven debounced sync inside `archy mcp`.
7. `archy_status` surfaces `last_synced_at` and cache stats.
8. Churn column populated lazily from git log; surface in `archy_hotspots` as a follow-up.

This phase is the bigger lift. The hard part is invalidation correctness; budget a full test pass dedicated to it.

---

## Open questions

1. **Plugin distribution.** ~~Inline or separate repo?~~ **Resolved: inline at `plugins/claude/`.** Manifest is versioned with the code it points at, single PR for changes, no version skew. If/when a public registry repo becomes necessary, a small CI job will mirror `plugins/claude/` on tag.
2. **Index scope.** ~~Should the cache also memoize `archy_score` / `archy_diff` outputs?~~ **Resolved: no.** Cache parse + edges + per-module metrics only. Score and diff stay as in-memory computations over the cached graph: cheap once parse is cached, and their invalidation rules (whole-graph for score, two graph states for diff) are too easy to get wrong. Revisit only if benchmarks show score/diff dominating warm-path latency.
3. **`archy affected` and external modules.** ~~Should it respect `internal_only` config, or add a flag?~~ **Resolved: internal-only at launch, document the limitation.** Matches `archy_impact`'s existing behavior, keeps CI runs deterministic across machines, simplest mental model. Users who vendor third-party code and need it traced can file an issue; revisit with an opt-in flag if real demand surfaces.
4. **Watcher behavior during git operations.** ~~Pause on git ops? Coarser debounce?~~ **Resolved: rely on the 2-second debounce; add a stress test before declaring done.** Stress test: swap between two divergent branches on a pytorch-scale repo in a loop, assert the watcher recovers within bounded time and the index converges to the correct state. Avoid teaching archy about `.git/` internals unless this test fails; iterate only if real-world reports show debounce is insufficient.
