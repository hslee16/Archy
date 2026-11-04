"""Every claim in docs/SAFETY.md ("what archy does on your machine") is pinned here.

The page states what archy does and does not do: no network, `git` as the only
subprocess, which commands write nothing, and the exact files each writer
touches. A sentence on that page that no test fails on is a promise nobody
checks, so each section below names the claim it backs.

Method: build a throwaway git project, run the real commands against it with
HOME pointed at a second throwaway directory, and compare file trees before and
after. Writes OUTSIDE the project and HOME (for example under the system temp
dir) are not observed, so the page states its claim as "inside the project and
your home directory".
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import selectors
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main

SRC = Path(__file__).resolve().parent.parent / "src" / "archy"

# Modules whose import means "this code can open a network connection".
NETWORK_MODULES = frozenset(
    {
        "socket",
        "ssl",
        "http",
        "urllib",
        "requests",
        "httpx",
        "aiohttp",
        "ftplib",
        "smtplib",
        "poplib",
        "imaplib",
        "telnetlib",
        "xmlrpc",
        "websockets",
        "websocket",
    }
)

# The only modules allowed to run a subprocess, and the only program they run.
SUBPROCESS_MODULES = frozenset({"coupling.py", "gitlog.py", "hotspots.py", "history.py"})
GIT_SUBCOMMANDS = frozenset({"rev-parse", "log"})
# Flags/operands after the subcommand. A write flag such as --output= must not
# survive: docs/SAFETY.md says rev-parse and log only read history.
GIT_REV_PARSE_ARGS = frozenset(
    {
        "HEAD",
        "--abbrev-ref",
        "--show-toplevel",
        "--verify",
        "--quiet",
    }
)
GIT_LOG_ARGS = frozenset({"-M", "--name-status", "--format=", "--format=%H", "-1"})


def _snapshot(root: Path) -> dict[str, str]:
    """Relative path to content hash for every file under `root`, including `.git`.

    Read-only commands may run `git rev-parse` / `git log`, which only read
    history; neither writes under `.git`. Including the tree means a write such
    as `.git/leak` turns a before/after assert red (issue #696).
    """
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if not p.is_file():
            continue
        out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _changed(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A git project at `proj`, an empty HOME at `home`, cwd inside the project."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    proj = tmp_path / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "__init__.py").write_text("")
    (proj / "pkg" / "a.py").write_text("from pkg import b\n\n\ndef f():\n    return b.g()\n")
    (proj / "pkg" / "b.py").write_text("def g():\n    return 1\n")
    (proj / "archy.yaml").write_text(
        "layers:\n  low:\n    modules: ['pkg.b']\n  high:\n    modules: ['pkg.a']\n"
        "forbid:\n  - {from: low, to: high}\n"
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    for cmd in (["git", "init", "-q"], ["git", "add", "."], ["git", "commit", "-qm", "x"]):
        subprocess.run(cmd, cwd=proj, check=True, env=env)
    monkeypatch.chdir(proj)
    # `archy diff` needs a baseline to compare against; make it part of the fixture.
    assert CliRunner().invoke(main, ["snapshot", "."]).exit_code == 0
    return proj, home


# Commands the page lists as read-only. Each must exit 0 (a usage error would
# "write nothing" trivially, which proves nothing).
READ_ONLY_COMMANDS: list[list[str]] = [
    ["check", "."],
    ["cycles", "."],
    ["score", "."],
    ["graph", "."],
    ["dsm", "."],
    ["hotspots", "."],
    ["coupling", "."],
    ["duplicates", "."],
    ["conventions", "."],
    ["brief", "."],
    ["what-to-refactor-next", "."],
    ["trend", "."],
    ["diff", "."],
    ["render", "."],
    ["impact", ".", "--file", "pkg/b.py"],
    ["affected", ".", "pkg/b.py"],
]


# --- claim: archy makes no network connections ------------------------------------


def _imported_top_level_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_source_imports_no_network_module():
    offenders = {}
    scanned = 0
    for path in sorted(SRC.rglob("*.py")):
        scanned += 1
        found = _imported_top_level_names(ast.parse(path.read_text(encoding="utf-8")))
        bad = sorted(found & NETWORK_MODULES)
        if bad:
            offenders[path.relative_to(SRC).as_posix()] = bad
    assert scanned > 40, "the scan did not reach archy's source tree"
    assert offenders == {}


def test_runtime_attempts_no_connection(sandbox, monkeypatch):
    """Run the whole command matrix and every MCP tool with connect() made fatal."""
    attempts: list[str] = []

    def refuse(name):
        def _refuse(*args, **kwargs):
            attempts.append(name)
            raise OSError(f"network blocked by test: {name}")

        return _refuse

    for attr in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, attr, refuse(attr))
    monkeypatch.setattr(socket, "getaddrinfo", refuse("getaddrinfo"))
    monkeypatch.setattr(socket, "create_connection", refuse("create_connection"))

    proj, _home = sandbox
    ran = 0
    for cmd in READ_ONLY_COMMANDS:
        result = CliRunner().invoke(main, cmd)
        assert result.exit_code == 0, (cmd, result.output)
        ran += 1
    assert ran == len(READ_ONLY_COMMANDS)
    assert _call_every_mcp_tool(proj) >= 14
    assert attempts == []


# --- claim: `archy mcp` serves over stdio and opens no listener -------------------

# Runs the real `archy mcp` command with the interpreter running this test, never the `archy` on
# PATH, which can be a stale install (see tests/test_mcp_protocol.py).
_SERVE_ARGV = [sys.executable, "-c", "from archy.cli import main; main()", "mcp"]


@pytest.mark.skipif(shutil.which("lsof") is None, reason="lsof is needed to list open sockets")
def test_mcp_server_speaks_stdio_and_listens_on_no_port(sandbox):
    proj, _home = sandbox
    proc = subprocess.Popen(
        _SERVE_ARGV,
        cwd=proj,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "safety-test", "version": "0"},
            },
        }
        proc.stdin.write(json.dumps(init) + "\n")
        proc.stdin.flush()
        # An answer on stdout is the stdio transport; another transport never writes here.
        reply = json.loads(_readline_with_timeout(proc.stdout, seconds=20))
        assert reply["id"] == 1 and "serverInfo" in reply["result"]

        listing = subprocess.run(
            ["lsof", "-nP", "-a", "-p", str(proc.pid), "-i"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert "LISTEN" not in listing.stdout, listing.stdout
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _readline_with_timeout(stream, seconds: float) -> str:
    # `selectors` (kqueue/epoll), not `select.select`, which raises once any fd exceeds 1023 and
    # the rest of the suite leaves plenty open.
    with selectors.DefaultSelector() as sel:
        sel.register(stream, selectors.EVENT_READ)
        assert sel.select(seconds), f"no reply on stdout within {seconds}s"
    return stream.readline()


# --- claim: the only subprocess is read-only git ----------------------------------


def test_only_git_modules_use_subprocess():
    users: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if "subprocess" in _imported_top_level_names(tree):
            users.add(path.relative_to(SRC).as_posix())
        for node in ast.walk(tree):
            # os.system / os.popen / os.exec* / shell=True would each defeat the claim.
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert not (node.value.id == "os" and node.attr in {"system", "popen"}), path
                assert not (node.value.id == "os" and node.attr.startswith("exec")), path
                assert not (node.value.id == "subprocess" and node.attr == "Popen"), path
            if isinstance(node, ast.keyword) and node.arg == "shell":
                raise AssertionError(f"shell= used in {path}")
    assert users == SUBPROCESS_MODULES


def test_runtime_subprocesses_are_read_only_git(sandbox, monkeypatch):
    recorded: list[list[str]] = []
    real_run = subprocess.run

    def recording_run(args, *a, **kw):
        recorded.append([str(x) for x in args])
        return real_run(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", recording_run)
    for cmd in [*READ_ONLY_COMMANDS, ["score", ".", "--record"]]:
        result = CliRunner().invoke(main, cmd)
        assert result.exit_code == 0, (cmd, result.output)

    assert recorded, "no subprocess ran, so this test observed nothing"
    for argv in recorded:
        assert argv[0] == "git", argv
        assert argv[1] == "-C", argv
        assert argv[3] in GIT_SUBCOMMANDS, argv
        rest = argv[4:]
        if argv[3] == "rev-parse":
            bad = [
                a
                for a in rest
                if a not in GIT_REV_PARSE_ARGS
                and not a.startswith("--since=")
                and not a.endswith("^{commit}")
            ]
        else:
            bad = [
                a
                for a in rest
                if a not in GIT_LOG_ARGS and not a.startswith("--since=") and ".." not in a
            ]
        assert bad == [], argv
    assert {argv[3] for argv in recorded} == GIT_SUBCOMMANDS


# --- claim: the code archy analyses is parsed, never imported or executed ---------


def test_analysed_code_is_never_executed(sandbox):
    proj, _home = sandbox
    sentinel = proj.parent / "executed.txt"
    (proj / "pkg" / "boom.py").write_text(
        f"open({str(sentinel)!r}, 'w').write('ran')\n"
        "from pkg import b\n\n\ndef h():\n    return b.g()\n"
    )
    (proj / "pkg" / "test_boom.py").write_text(
        f"open({str(sentinel)!r}, 'w').write('ran')\n\n\ndef test_x():\n    assert True\n"
    )
    for cmd in READ_ONLY_COMMANDS:
        assert CliRunner().invoke(main, cmd).exit_code == 0, cmd
    assert _call_every_mcp_tool(proj) >= 14
    # The module really is in the graph, so "not executed" is not "not seen".
    graph = CliRunner().invoke(main, ["graph", ".", "--format", "json"])
    assert "pkg.boom" in graph.output
    assert not sentinel.exists()


# --- claim: these commands write nothing ------------------------------------------


@pytest.mark.parametrize("cmd", READ_ONLY_COMMANDS, ids=lambda c: c[0])
def test_read_only_command_writes_nothing(sandbox, cmd):
    proj, home = sandbox
    before_p, before_h = _snapshot(proj), _snapshot(home)
    result = CliRunner().invoke(main, cmd)
    assert result.exit_code == 0, result.output
    assert _changed(before_p, _snapshot(proj)) == []
    assert _changed(before_h, _snapshot(home)) == []


# --- claim: each writer touches exactly the files the page lists ------------------


def test_snapshot_writes_only_the_baseline(sandbox):
    proj, home = sandbox
    (proj / ".archy" / "baseline.json").unlink()  # the fixture made one; start without it
    before_p, before_h = _snapshot(proj), _snapshot(home)
    assert CliRunner().invoke(main, ["snapshot", "."]).exit_code == 0
    assert _changed(before_p, _snapshot(proj)) == [".archy/baseline.json"]
    assert _changed(before_h, _snapshot(home)) == []


def test_score_record_writes_only_the_history_file(sandbox):
    proj, home = sandbox
    before_p, before_h = _snapshot(proj), _snapshot(home)
    assert CliRunner().invoke(main, ["score", ".", "--record"]).exit_code == 0
    assert _changed(before_p, _snapshot(proj)) == [".archy/history.jsonl"]
    assert _changed(before_h, _snapshot(home)) == []


def test_index_sync_writes_only_the_cache(sandbox):
    proj, home = sandbox
    before_p, before_h = _snapshot(proj), _snapshot(home)
    assert CliRunner().invoke(main, ["index", "sync", "."]).exit_code == 0
    assert _changed(before_p, _snapshot(proj)) == [".archy/index.db"]
    assert _changed(before_h, _snapshot(home)) == []


def test_render_out_writes_only_the_named_file(sandbox):
    proj, home = sandbox
    before_p, before_h = _snapshot(proj), _snapshot(home)
    result = CliRunner().invoke(main, ["render", ".", "--out", "view.html"])
    assert result.exit_code == 0, result.output
    assert _changed(before_p, _snapshot(proj)) == ["view.html"]
    assert _changed(before_h, _snapshot(home)) == []


def _mcp_args(tool, proj: Path) -> dict:
    """Minimal arguments that make each archy MCP tool answer for the fixture project."""
    args: dict = {}
    if "path" in tool.inputSchema.get("properties", {}):
        args["path"] = str(proj)
    if tool.name in {"archy_impact", "archy_affected"}:
        args["files"] = ["pkg/b.py"]
    if tool.name == "archy_module_view":
        args["module"] = "pkg.a"
    if tool.name == "archy_simulate":
        # An empty delta is refused (#684), so propose a real one.
        args["add"] = [{"from": "pkg.b", "to": "pkg.a"}]
    return args


def _call_every_mcp_tool(proj: Path) -> int:
    """Call each MCP tool in-process on `proj`; return how many answered.

    A tool that raises fails the test: swallowing the error is how one tool
    (needing a `module` argument) went uncalled while the page said every tool was.
    `archy_snapshot` goes first so `archy_diff` has a baseline to compare against.
    """
    from archy.mcp import create_server

    server = create_server()
    tools = sorted(asyncio.run(server.list_tools()), key=lambda t: t.name != "archy_snapshot")
    for tool in tools:
        asyncio.run(server.call_tool(tool.name, _mcp_args(tool, proj)))
    time.sleep(0.3)  # let the debounced watcher settle before the caller diffs
    return len(tools)


def test_mcp_tools_write_only_cache_and_baseline(sandbox):
    """Read-only tools may populate the derived cache; only archy_snapshot writes the baseline."""
    from archy.mcp import create_server

    proj, home = sandbox
    (proj / ".archy" / "baseline.json").unlink()  # the fixture made one; start without it
    server = create_server()
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert len(tools) >= 14
    # archy_snapshot first, so archy_diff has a baseline and every tool can answer.
    tools = dict(sorted(tools.items(), key=lambda kv: kv[0] != "archy_snapshot"))

    # The page says every tool but archy_snapshot is annotated read-only.
    for name, tool in tools.items():
        assert tool.annotations is not None, name
        assert (tool.annotations.readOnlyHint is False) == (name == "archy_snapshot"), name

    written: set[str] = set()
    for name, tool in tools.items():
        before_p, before_h = _snapshot(proj), _snapshot(home)
        asyncio.run(server.call_tool(name, _mcp_args(tool, proj)))  # must answer, not refuse
        time.sleep(0.3)
        assert _changed(before_h, _snapshot(home)) == [], name
        changed = _changed(before_p, _snapshot(proj))
        allowed = {".archy/index.db"} | (
            {".archy/baseline.json"} if name == "archy_snapshot" else set()
        )
        assert set(changed) <= allowed, (name, changed)
        written.update(changed)

    # The two writes the page names really happen (a test that never sees them proves nothing).
    assert ".archy/baseline.json" in written
    assert ".archy/index.db" in written


GLOBAL_INSTALL_WRITES = [
    ".claude.json",
    ".claude/CLAUDE.md",
    ".claude/settings.json",
    ".codex/AGENTS.md",
    ".codex/config.toml",
    ".config/opencode/AGENTS.md",
    ".config/opencode/opencode.json",
    ".continue/mcpServers/archy.yaml",
    ".continue/rules/archy.md",
    ".cursor/mcp.json",
    ".cursor/rules/archy.mdc",
]

LOCAL_INSTALL_WRITES = [
    ".claude/settings.json",
    ".codex/config.toml",
    ".continue/mcpServers/archy.yaml",
    ".continue/rules/archy.md",
    ".cursor/mcp.json",
    ".cursor/rules/archy.mdc",
    ".mcp.json",
    "AGENTS.md",
    "CLAUDE.md",
    "opencode.json",
]


def test_install_print_config_writes_nothing(sandbox):
    proj, home = sandbox
    before_p, before_h = _snapshot(proj), _snapshot(home)
    result = CliRunner().invoke(main, ["install", "--print-config", "claude"])
    assert result.exit_code == 0, result.output
    assert _changed(before_p, _snapshot(proj)) == []
    assert _changed(before_h, _snapshot(home)) == []


def test_global_install_writes_exactly_the_listed_files_in_home(sandbox):
    proj, home = sandbox
    before_p = _snapshot(proj)
    result = CliRunner().invoke(main, ["install", "--target", "all", "-y"])
    assert result.exit_code == 0, result.output
    assert _changed({}, _snapshot(home)) == sorted(GLOBAL_INSTALL_WRITES)
    assert _changed(before_p, _snapshot(proj)) == []


def test_local_install_writes_exactly_the_listed_files_in_the_project(sandbox):
    proj, home = sandbox
    before_p = _snapshot(proj)
    result = CliRunner().invoke(main, ["install", "--target", "all", "--location", "local", "-y"])
    assert result.exit_code == 0, result.output
    assert _changed(before_p, _snapshot(proj)) == sorted(LOCAL_INSTALL_WRITES)
    assert _changed({}, _snapshot(home)) == []


def test_uninstall_removes_what_install_added(sandbox):
    _proj, home = sandbox
    assert CliRunner().invoke(main, ["install", "--target", "all", "-y"]).exit_code == 0
    assert _snapshot(home) != {}
    result = CliRunner().invoke(main, ["uninstall", "--target", "all", "-y"])
    assert result.exit_code == 0, result.output
    # Files archy created are gone or no longer mention archy; none is left holding its stanza.
    for rel in GLOBAL_INSTALL_WRITES:
        p = home / rel
        if p.exists():
            assert "archy" not in p.read_text(encoding="utf-8").lower(), rel
