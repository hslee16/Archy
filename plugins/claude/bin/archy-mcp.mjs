#!/usr/bin/env node
// archy MCP launcher for the Claude Code plugin (issue #150).
//
// The manifest invokes this as `node ${CLAUDE_PLUGIN_ROOT}/bin/archy-mcp.mjs`.
// `node` is the one launcher Claude Code guarantees on PATH on every platform,
// which lets us drop the previous HARD `uv` requirement: a missing `uv` used to
// make the whole tool surface silently fail to start. This script instead tries
// every reasonable way to run `archy mcp` and, if none exist, prints an
// actionable error telling the user exactly what to install.
//
// Resolution order (first hit wins):
//   1. `archy` on PATH      -- any install method (pipx / uv tool / pip).
//   2. `uvx`  on PATH       -- fetches the pinned archy on demand, no install.
//   3. `python -m archy`    -- archy pip-installed but its console script not on
//                              PATH (e.g. a venv whose Scripts/ dir is unlinked).
//
// It is a pure process shim: it execs the resolved command with stdio inherited
// so the MCP stdio protocol passes straight through, and forwards exit/signal.

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { delimiter, join } from "node:path";

// Keep in sync with plugins/claude/README.md and tests/test_plugin.py. The lower
// bound guarantees the full current tool set; the `<1.0` cap stops a breaking
// 1.0 from being auto-pulled into the stanza.
const SPEC = "archy>=0.31,<1.0";

const isWindows = process.platform === "win32";
// On Windows a bare command name has no extension; CreateProcess resolves `.exe`
// but PATH scanning here must append each PATHEXT entry to find the file.
const pathExts = isWindows ? (process.env.PATHEXT || ".EXE;.CMD;.BAT;.COM").split(";") : [""];

function which(cmd) {
  for (const dir of (process.env.PATH || "").split(delimiter).filter(Boolean)) {
    for (const ext of pathExts) {
      const candidate = join(dir, cmd + ext);
      if (existsSync(candidate)) return candidate;
    }
  }
  return null;
}

function resolveLauncher() {
  let p;
  if ((p = which("archy"))) return { path: p, args: ["mcp"] };
  if ((p = which("uvx"))) return { path: p, args: [SPEC, "mcp"] };
  for (const py of ["python3", "python"]) {
    if ((p = which(py))) return { path: p, args: ["-m", "archy", "mcp"] };
  }
  return null;
}

const resolved = resolveLauncher();
if (!resolved) {
  process.stderr.write(
    "archy MCP server could not start: none of `archy`, `uvx`, or `python` were " +
      "found on PATH.\nInstall any one of these, then restart Claude Code:\n" +
      "  - uv (bootstraps its own Python): curl -LsSf https://astral.sh/uv/install.sh | sh\n" +
      "  - archy directly:                 pipx install archy   (or: uv tool install archy)\n" +
      "More: https://github.com/hslee16/archy/blob/main/plugins/claude/README.md\n",
  );
  process.exit(1);
}

// `.cmd`/`.bat` shims cannot be exec'd directly by spawn on Windows; they need
// cmd.exe. Real `.exe` launchers (uv ships uvx.exe, pip ships archy.exe) and all
// POSIX binaries spawn directly.
const useCmd = isWindows && /\.(cmd|bat)$/i.test(resolved.path);
const command = useCmd ? "cmd" : resolved.path;
const args = useCmd ? ["/c", resolved.path, ...resolved.args] : resolved.args;

const child = spawn(command, args, { stdio: "inherit" });
child.on("error", (err) => {
  process.stderr.write(`archy MCP server failed to launch ${resolved.path}: ${err.message}\n`);
  process.exit(1);
});
child.on("exit", (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 0);
});
