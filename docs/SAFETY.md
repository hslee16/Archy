# What archy does on your machine

archy reads your Python source and, if you ask, edits your AI agents' config
files. This page lists what it touches. **Every claim below is pinned by a test
in [`tests/test_safety_claims.py`](../tests/test_safety_claims.py)**; a sentence
without a test is marked as such. Run `uv run pytest tests/test_safety_claims.py`
to check them against the copy you have.

For vulnerability reporting and scope, see [`SECURITY.md`](../SECURITY.md).

## In one table

| | |
|---|---|
| Network | None. archy's own code imports no networking module, and the test suite makes `connect()` fatal while every read-only command and every MCP tool runs. |
| Subprocesses | `git` only, and only `git -C <project> rev-parse` and `git -C <project> log`. Both read history; neither changes it. |
| Your analysed code | Parsed with tree-sitter, never imported or executed. |
| Most commands | Write nothing at all (list below). |
| The commands that write | Each writes a fixed, named set of files (table below). |

## Network

- `src/archy` imports none of `socket`, `ssl`, `http`, `urllib`, `requests`,
  `httpx`, `aiohttp`, `ftplib`, `smtplib`, `poplib`, `imaplib`, `telnetlib`,
  `xmlrpc`, `websockets`, `websocket`.
  (`test_source_imports_no_network_module`)
- A second test catches what an import scan cannot (a call built with
  `__import__`, or a dependency): with `socket.connect`, `connect_ex`,
  `sendto`, `getaddrinfo` and `create_connection` all made to fail, the
  read-only commands run and every MCP tool is called, and nothing attempts a
  connection. (`test_runtime_attempts_no_connection`)
- The MCP server (`archy mcp`) serves over stdio and opens no listener: the real
  command answers an `initialize` request on stdout, and `lsof` shows no listening
  socket for its process. (`test_mcp_server_speaks_stdio_and_listens_on_no_port`,
  skipped where `lsof` is missing)

Installing archy is a different matter: `pip`, `uv` or `uvx` download it from
PyPI. That is your package manager, not archy.

## Subprocesses

Only these modules run a subprocess: `coupling.py`, `gitlog.py`, `hotspots.py`
and `history.py`. None uses a shell, `os.system`, `os.popen` or `Popen`.
(`test_only_git_modules_use_subprocess`)

Running the command matrix records every subprocess call: each is
`git -C <project> rev-parse ...` or `git -C <project> log ...`, and both
subcommands are actually exercised.
(`test_runtime_subprocesses_are_read_only_git`)

`archy contracts` and `archy check --contracts` run import-linter inside the
same Python process, not as a subprocess.

## Your analysed code is not run

archy parses source text. A module whose top level writes a file is parsed,
appears in the graph, and its side effect never happens, across the read-only
commands and all MCP tools. (`test_analysed_code_is_never_executed`)

## Commands that write nothing

These leave every file in the project and in your home directory unchanged, and
each exits 0 (a usage error would "write nothing" for free):

`check`, `cycles`, `score`, `graph`, `dsm`, `hotspots`, `coupling`,
`duplicates`, `conventions`, `brief`, `what-to-refactor-next`, `trend`, `diff`,
`render` (it prints HTML to stdout unless you pass `--out`), `impact`,
`affected`. (`test_read_only_command_writes_nothing`)

`install --print-config <agent>` also writes nothing.
(`test_install_print_config_writes_nothing`)

## Commands that write

| Command | What it writes | Test |
|---|---|---|
| `archy snapshot` | `.archy/baseline.json` in the project | `test_snapshot_writes_only_the_baseline` |
| `archy score --record` | appends to `.archy/history.jsonl` | `test_score_record_writes_only_the_history_file` |
| `archy index sync` | `.archy/index.db`, a parse cache; `archy index clear` deletes it | `test_index_sync_writes_only_the_cache` |
| `archy render --out FILE` | `FILE` only | `test_render_out_writes_only_the_named_file` |
| `archy install --target all` (global, the default) | the 11 files listed below, in your home directory | `test_global_install_writes_exactly_the_listed_files_in_home` |
| `archy install --target all --location local` | the 10 files listed below, in the project | `test_local_install_writes_exactly_the_listed_files_in_the_project` |
| `archy uninstall` | removes archy's entries from those files | `test_uninstall_removes_what_install_added` |
| `archy mcp` | see next section | `test_mcp_tools_write_only_cache_and_baseline` |

Install, global (paths relative to your home directory):

```
.claude.json                       .codex/AGENTS.md
.claude/CLAUDE.md                  .codex/config.toml
.claude/settings.json              .config/opencode/AGENTS.md
.cursor/mcp.json                   .config/opencode/opencode.json
.cursor/rules/archy.mdc            .continue/mcpServers/archy.yaml
                                   .continue/rules/archy.md
```

Install, local (paths relative to the project):

```
.mcp.json   CLAUDE.md   .claude/settings.json   AGENTS.md   opencode.json
.codex/config.toml   .cursor/mcp.json   .cursor/rules/archy.mdc
.continue/mcpServers/archy.yaml   .continue/rules/archy.md
```

These lists are what a **clean** home directory or project gets. On an existing
setup, install edits those files in place. By default `archy install` only
configures the agents it detects; `--target all` is what the test uses. The
Windows locations (`%APPDATA%`, `%LOCALAPPDATA%`) are not covered by a test.

## The MCP server

Every MCP tool except `archy_snapshot` is annotated read-only. While serving,
they write at most one thing, `.archy/index.db`, the same derived cache as above:
the server keeps it warm and refreshes it when your files change.
`archy_snapshot` additionally writes `.archy/baseline.json` and is the only tool
not annotated read-only. No tool writes anywhere else in the project or your home
directory. Deleting `.archy/` is always safe.

## What this page does not claim

Known gaps in the tests behind the claims above, each tracked: the network check does not see DNS lookups, raw `_socket` use or
`bind`/`listen` ([#693](https://github.com/hslee16/archy/issues/693)); the
subprocess scan can be bypassed with `os.spawn*`, asyncio subprocesses or aliased
imports ([#694](https://github.com/hslee16/archy/issues/694)); the git check does
not look at flags after the subcommand
([#695](https://github.com/hslee16/archy/issues/695)); and
uninstall is only weakly pinned, with no test on an existing config
([#697](https://github.com/hslee16/archy/issues/697)). Also, the MCP entry
install writes runs `uvx archy mcp`, so your agent's launcher fetches archy from
PyPI when it starts the server
([#698](https://github.com/hslee16/archy/issues/698) tracks saying so in the
network section above).

Other limits:

- **Writes outside the project and your home directory** (for example the system
  temp directory) are not observed by the tests.
- **`archy conventions --emit-headers --write`** rewrites the derived header
  block inside your source files. It is opt-in and has no test on this page;
  `--check` is the read-only form.
- **`archy simulate` and `archy contracts`** are not in the read-only list
  above, because the test matrix does not run them.
- **Third-party dependencies** (`networkx`, `pydantic`, `click`, `tree-sitter`,
  `mcp`, `watchdog`, and `import-linter` with the `contracts` extra) run
  inside archy's process. The runtime test shows none opens a connection on the
  paths it exercises; it does not audit their code.
- **Release checksums.** PyPI publishes a SHA-256 for the wheel and the sdist on
  each file's page. archy publishes no separate checksum file.
