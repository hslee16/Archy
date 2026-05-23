# Spec: testing strategy for the agent-detecting installer

Companion to [`SPEC_INDEX_AND_INSTALL.md`](SPEC_INDEX_AND_INSTALL.md) Part 4. That spec defines *what* the installer does; this one defines *how we know it works* across Linux, macOS, and Windows for five agent clients (Claude Code, Cursor, Codex CLI, opencode, Continue).

The install path is the kind of code that silently breaks on the platform the maintainer does not develop on. The strategy below is layered so every class of failure has at least one layer cheap enough to run on every PR.

## Prior art and what we learned from it

- **mcpm** (`pathintegral-institute/mcpm.sh`) is the closest existing project. Useful patterns: per-adapter test files (`tests/test_clients/test_opencode.py`, `test_windsurf.py`, etc.), `click.testing.CliRunner` for command invocation, `monkeypatch` over the registry surface. Antipattern to avoid: their CI runs only on `ubuntu-latest` with no matrix, and detection is mocked end-to-end, so they have zero real cross-OS coverage. Archy ships the matrix from day one.
- **chezmoi** runs the *real* setup on macOS + Ubuntu on a schedule, treats idempotency as a contract, and implements dry-run as a `DryRunSystem` wrapper that allows reads but discards writes. Archy adopts the dry-run shape via `--print-config <id>` (already in the install spec) and the idempotency contract as a CI assertion.
- **nektos/act** runs GitHub Actions locally but is Linux-only. Useful for fast local iteration on the ubuntu job; not a substitute for the matrix.

## The five-layer strategy

Ordered cheapest to most expensive. Each layer catches a class of failure the layers above it cannot.

| # | Layer | Tool | Runs on | What it catches |
|---|---|---|---|---|
| 1 | Unit tests | `pytest` + `monkeypatch` + `tmp_path` | one Linux runner, every PR | Detection logic for all three OSes via `sys.platform` / `Path.exists` / `shutil.which` mocks; adapter Protocol compliance; per-adapter test file mirroring mcpm's shape |
| 2 | Snapshot tests | `syrupy` ([repo](https://github.com/syrupy-project/syrupy)) | one Linux runner, every PR | Emitted-config drift. For each `(adapter, OS, scope)`, snapshot the bytes `write_mcp_config` would emit. Writers are pure functions of `(scope, paths)`, so all three OSes can be snapshotted from Linux by parameterizing the path roots. |
| 3 | Filesystem integration | `pytest` + `tmp_path` | GHA matrix `ubuntu-latest` / `macos-latest` / `windows-latest`, every PR | Atomic-write behavior, real `os.replace`, Windows file-locking (with a fixture that holds a handle), real path resolution. `fail-fast: false`. |
| 4 | Contract tests | `pytest` + each client's expected schema | matrix, every PR | After `write_mcp_config`, parse the file back using the client's expected shape (Cursor `mcp.json`, Codex TOML, opencode JSON, Claude `~/.claude.json`). Catches "writer emits something the client rejects" without running the client. |
| 5 | E2E smoke | real CLIs in a gated workflow | matrix, on release tag or `workflow_dispatch` only | Install Claude Code SDK + Cursor CLI in CI, run `archy install`, invoke the agent with a trivial MCP-calling prompt, assert archy is reached. Skipped on PRs (cost + API-key secrets). Continue is excluded (VS Code extension, no headless story). |

## Design conventions that make layers 1 through 4 work

- **Dry-run is non-negotiable.** The install spec already requires `--print-config <id>`. The implementation should follow chezmoi: a `WriteSystem` Protocol with `RealWriteSystem` and `DryRunWriteSystem` impls. Every test in layers 1 and 2 uses `DryRunWriteSystem`; layers 3 through 5 use `RealWriteSystem`. This keeps the slow tests rare.
- **Idempotency as a CI assertion.** For each adapter, run `install` twice on a tmp dir and assert the second run produces no file diffs and no exceptions. One property per adapter, asserted in layer 3.
- **Single path-resolution helper.** All `os.environ` lookups (`APPDATA`, `LOCALAPPDATA`, `USERPROFILE`) and `Path.home()` calls go through one module that is the only place unit tests monkeypatch for cross-OS simulation. Prevents per-adapter resolution drift.
- **No hand-coded JSON or TOML strings in tests.** Always parse-roundtrip. Strings drift; structures do not.
- **Per-adapter test files.** `tests/install/test_claude.py`, `test_cursor.py`, `test_codex.py`, `test_opencode.py`, `test_continue.py`. Adapter registry pattern in the source, mirror in the tests.

## Why E2E is gated, not scheduled

The original draft of this strategy had a weekly cron for layer 5. We reconsidered:

- archy is not a service; it runs when a user types `archy install`. Finding out about upstream breakage six days before a user does is not load-bearing.
- Unwatched cron alerts become noise. Weekly failures that nobody investigates are a failure mode in themselves.
- Layer 4 already covers the static schema-drift case via vendored schemas.
- E2E costs API tokens (two clients times three OSes per run) and maintenance attention (every red cron is an investigation).

The forcing function that actually matters is **shipping a release**. Layer 5 runs on release tags so we know the install path works for the version we are about to publish, and on `workflow_dispatch` for ad-hoc verification when something feels off. No cron.

## CI shape

```yaml
# .github/workflows/test.yml (PR-blocking)
on: [pull_request, push]
jobs:
  fast:                     # layers 1 and 2, ~30s
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --group dev
      - run: uv run pytest tests/install/unit tests/install/snapshot

  cross-os:                 # layers 3 and 4, ~3min
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --group dev
      - run: uv run pytest tests/install/integration tests/install/contract
```

```yaml
# .github/workflows/install-e2e.yml (layer 5, gated)
on:
  push:
    tags: ['v*']
  workflow_dispatch:
jobs:
  e2e:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        client: [claude-code, cursor]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      # install the client CLI per matrix.client
      # run `archy install --target ${{ matrix.client }} --yes`
      # invoke the client headlessly with a trivial MCP prompt
      # assert archy was reached
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      CURSOR_API_KEY: ${{ secrets.CURSOR_API_KEY }}
```

Codex CLI and opencode are added to layer 5 once their headless modes are stable enough to script against; Continue stays out (VS Code extension, no headless story).

## Coverage matrix

What every adapter must have at every layer before its PR can merge.

| Layer | claude | cursor | codex | opencode | continue |
|---|---|---|---|---|---|
| 1 unit (detect, paths, dry-run write) | required | required | required | required | required |
| 2 snapshot (write per OS x scope) | required | required | required | required | required |
| 3 integration (real fs, all 3 OSes) | required | required | required | required | required |
| 4 contract (parse-roundtrip) | required | required | required | required | required |
| 5 E2E (real client, headless) | required | required | best-effort | best-effort | excluded |

### Uninstall coverage

`archy uninstall` is the inverse of install and is covered by the same layers, not a parallel suite, because it reuses the install plan (each `FileAction` carries paired `render`/`unrender`). The load-bearing assertions:

- **Unit:** each `strip_*` render is idempotent, removes only archy's keys/patterns, and preserves unrelated structure; `remove_instructions` returns `None` only when the archy block was the file's sole content; `apply_uninstall` skips absent files, strips shared ones, deletes owned ones.
- **Integration (matrix):** install -> uninstall round-trip leaves no file mentioning archy on a previously-clean machine; a second uninstall is a byte-for-byte no-op (idempotency); a pre-existing user `~/.claude.json` and `CLAUDE.md` survive with only archy's parts removed.
- **Contract:** every stripped shared config still parses with the client's parser and no longer contains the archy server; Continue's owned files resolve to deletion, not a parse target.

## What we explicitly do not do

- **Cron-scheduled E2E.** See "Why E2E is gated" above.
- **nektos/act for cross-OS.** Linux-only; misleads more than it helps.
- **BuildJet / depot.dev / Cirrus.** Premature optimization. GHA Windows runners are slow but free; revisit only if the `cross-os` job exceeds about five minutes steady state.
- **Docker-based Linux distro variants.** archy is Python; the interpreter abstracts distro. Add only if a real distro-specific bug surfaces.
- **End-to-end Continue.** VS Code extension, no headless mode worth the cost. Layer 4 contract test on `~/.continue/mcpServers/mcp.json` is the right ceiling.
- **Snapshot-testing end-to-end flows.** Snapshots are for writer outputs only; behavior is asserted.

## Cost estimate

- Layers 1 and 2: about 30 seconds per PR. Free GHA minutes.
- Layers 3 and 4: about three minutes per PR across the three-OS matrix. Free.
- Layer 5: about five minutes per release tag. Free GHA minutes; cost is the API tokens consumed by the headless agent runs (small, trivial prompt).
- Engineering cost to set up: roughly one focused day for layers 1 through 4, half a day for layer 5.

## References

- [`SPEC_INDEX_AND_INSTALL.md`](SPEC_INDEX_AND_INSTALL.md) Part 4 (the install registry being tested)
- [syrupy](https://github.com/syrupy-project/syrupy)
- [pyfakefs](https://github.com/pytest-dev/pyfakefs) (alternative to `tmp_path` if performance becomes an issue; not adopted at launch)
- [chezmoi testing](https://www.chezmoi.io/developer-guide/testing/)
- [chezmoi dry-run architecture](https://www.chezmoi.io/developer-guide/architecture/)
- [Cursor CLI headless docs](https://cursor.com/docs/cli/headless)
- [Claude Code headless mode](https://code.claude.com/docs/en/headless)
- [Codex MCP config](https://developers.openai.com/codex/mcp)
- [opencode MCP servers](https://opencode.ai/docs/mcp-servers/)
- [Continue MCP config](https://docs.continue.dev/customize/deep-dives/mcp)
