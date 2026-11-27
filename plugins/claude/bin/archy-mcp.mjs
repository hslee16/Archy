#!/usr/bin/env node
// archy MCP launcher for the Claude Code plugin (issue #150).
//
// The manifest invokes this as `node ${CLAUDE_PLUGIN_ROOT}/bin/archy-mcp.mjs`.
// `node` is the one launcher Claude Code guarantees on PATH on every platform,
// which lets us drop the previous HARD `uv` requirement: a missing `uv` used to
// make the whole tool surface silently fail to start. This script instead tries
// every reasonable way to run `archy mcp` and, if none start, prints an
// actionable error telling the user exactly what to install.
//
// Resolution order (first that actually starts wins):
//   1. `archy` on PATH      -- any install method (pipx / uv tool / pip).
//   2. `uvx`  on PATH       -- fetches the pinned archy on demand, no install.
//   3. `python3` / `python`  -- one that can import archy: pip-installed but its
//                              console script not on PATH (e.g. a venv whose
//                              Scripts/ dir is unlinked).
//
// It is a pure stdio shim: it execs the resolved command with stdio inherited so
// the MCP stdio protocol passes straight through (the resolver only ever writes
// to stderr, never stdout, so the JSON-RPC stream stays clean), forwards
// termination signals to the child so a Claude Code restart cannot orphan it,
// and propagates the child's exit status.

import { spawn } from "node:child_process";
import { statSync } from "node:fs";
import { delimiter, join } from "node:path";

// Keep in sync with plugins/claude/README.md and tests/test_plugin.py. The lower
// bound guarantees the full current tool set; the `<1.0` cap stops a breaking
// 1.0 from being auto-pulled into the stanza.
const SPEC = "archy>=0.51,<1.0";

// `-c` and `-m` both put the working directory, here the user's project, first on
// sys.path, so a project with its own `archy/` folder would be executed; the
// "" entry is dropped for that. `-E` covers the other route, a PYTHONPATH such
// as "." that Python resolves into the project. No double quotes: see spawnArgs.
const PY_IMPORT = "import sys; sys.path[:] = [p for p in sys.path if p]; from archy.cli import main";
const PY_RUN = `${PY_IMPORT}; main()`;

const isWindows = process.platform === "win32";
// Only extensions we can actually launch: .exe/.com directly, .cmd/.bat via
// cmd.exe. Deliberately NOT the full PATHEXT -- a .ps1/.vbs on PATH is not
// directly spawnable and would only waste a candidate slot.
const pathExts = isWindows ? [".EXE", ".COM", ".CMD", ".BAT"] : [""];

function isLaunchable(file) {
  let st;
  try {
    st = statSync(file);
  } catch {
    return false;
  }
  // Must be a real file (not a directory that merely shares the tool's name) and
  // non-empty: Windows Store "App Execution Alias" shims for python are 0-byte
  // reparse points that hang instead of running, so size 0 is excluded. On POSIX
  // the executable bit must be set or spawn would fail with EACCES.
  if (!st.isFile() || st.size === 0) return false;
  if (!isWindows && (st.mode & 0o111) === 0) return false;
  return true;
}

function which(cmd) {
  for (const dir of (process.env.PATH || "").split(delimiter).filter(Boolean)) {
    for (const ext of pathExts) {
      const candidate = join(dir, cmd + ext);
      if (isLaunchable(candidate)) return candidate;
    }
  }
  return null;
}

function candidates() {
  const out = [];
  let p;
  if ((p = which("archy"))) out.push({ path: p, args: ["mcp"] });
  if ((p = which("uvx"))) out.push({ path: p, args: [SPEC, "mcp"] });
  for (const py of ["python3", "python"]) {
    if ((p = which(py))) out.push({ path: p, args: ["-E", "-c", PY_RUN, "mcp"], probe: ["-E", "-c", PY_IMPORT] });
  }
  return out;
}

function spawnArgs(path, args) {
  // .cmd/.bat cannot be exec'd directly by CreateProcess; route them through
  // cmd.exe. Double-quote every token so spaces and the spec's `<`/`>` (cmd
  // redirection operators) stay literal, wrap per `cmd /s /c "..."` rules, and
  // pass verbatim so Node does not re-quote. .exe/.com and POSIX binaries spawn
  // directly, where the spec is a clean argv element needing no escaping.
  if (isWindows && /\.(cmd|bat)$/i.test(path)) {
    const line = [path, ...args].map((t) => `"${t}"`).join(" ");
    return { command: "cmd", argv: ["/d", "/s", "/c", `"${line}"`], verbatim: true };
  }
  return { command: path, argv: args, verbatim: false };
}

const FAIL_MESSAGE =
  "archy MCP server could not start: found no `archy` or `uvx` on PATH, and no " +
  "Python that has archy installed.\nInstall any one of these, then restart Claude Code:\n" +
  "  - uv (bootstraps its own Python): curl -LsSf https://astral.sh/uv/install.sh | sh\n" +
  "  - archy directly:                 pipx install archy   (or: uv tool install archy)\n" +
  "More: https://github.com/hslee16/archy/blob/main/plugins/claude/README.md\n";

const SIGNALS = ["SIGINT", "SIGTERM", "SIGHUP"];
const list = candidates();
let active = null;
let probing = null;
let terminating = false;

// Re-raise `signal` on ourselves with the DEFAULT action (terminate by signal),
// so the shim's exit status reflects how it died. The handlers must be removed
// first or the re-raise is swallowed by them and the process exits 0 instead.
function dieBySignal(signal) {
  for (const s of SIGNALS) process.removeAllListeners(s);
  process.kill(process.pid, signal);
}

// Forward termination to the child so a Claude Code stop/restart never orphans
// the real MCP process (which holds the live stdio pipes). The previous shim
// killed only itself, leaking a stranded archy/uvx/python on every restart.
function onSignal(signal) {
  terminating = true;
  if (probing) {
    // Nothing to shut down gracefully, and a hung interpreter may ignore `signal`.
    probing.kill("SIGKILL");
    dieBySignal(signal);
  } else if (active && active.exitCode === null && active.signalCode === null) {
    active.kill(signal); // child's `exit` handler then propagates the signal up
  } else {
    dieBySignal(signal); // no live child to carry it: terminate ourselves now
  }
}
for (const sig of SIGNALS) process.on(sig, () => onSignal(sig));

function start(i) {
  // A termination signal that arrived mid-resolution must stop us, not trigger
  // the spawn of yet another child.
  if (terminating) return process.exit(0);
  if (i >= list.length) {
    process.stderr.write(FAIL_MESSAGE);
    return process.exit(1);
  }
  if (!list[i].probe) return launch(i);
  // Nearly every machine has some python3, so its presence says nothing: running
  // it without archy would exit with "No module named archy" and hide the hint.
  // Asynchronous, so a termination signal is still handled while it runs.
  const { command, argv, verbatim } = spawnArgs(list[i].path, list[i].probe);
  probing = spawn(command, argv, {
    stdio: "ignore",
    timeout: 15000,
    killSignal: "SIGKILL",
    windowsVerbatimArguments: verbatim,
  });
  const next = (passed) => {
    probing = null;
    if (passed) launch(i);
    else start(i + 1);
  };
  probing.on("error", () => next(false));
  probing.on("exit", (code) => next(code === 0));
}

function launch(i) {
  if (terminating) return process.exit(0);
  const { command, argv, verbatim } = spawnArgs(list[i].path, list[i].args);
  const child = spawn(command, argv, { stdio: "inherit", windowsVerbatimArguments: verbatim });
  active = child;
  let started = false;
  child.on("spawn", () => {
    started = true;
  });
  child.on("error", () => {
    // Failed to even start this candidate (vanished, EACCES, not a real exe).
    // Nothing has touched the stdio stream yet, so fall through to the next.
    if (started) {
      process.stderr.write(FAIL_MESSAGE);
      process.exit(1);
    } else {
      start(i + 1);
    }
  });
  child.on("exit", (code, signal) => {
    if (signal) dieBySignal(signal);
    else process.exit(code ?? 0);
  });
}

start(0);
