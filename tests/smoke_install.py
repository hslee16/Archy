"""Cross-platform end-to-end smoke test for a freshly installed `archy`.

Run after `pip install` has placed `archy` on PATH. Creates fixture projects in
a temp directory and exercises the installed console script and the installed
MCP server end to end.

This runs against a BUILT WHEEL in a clean venv, which is the only place some
failures are visible: the unit suite runs from source against a dev environment
resolved from `uv.lock`, so it proves archy works on the exact dependency
versions a user installing from PyPI probably does not get.

That gap shipped a broken release. archy declared `mcp>=1.28.1` with no ceiling;
mcp 2.0.0 removed `mcp.server.fastmcp`, and every fresh install got a server
that died on import (#391). CI was green on all counts, and so was this file,
because it only exercised the CLI -- and the CLI is entirely unaffected by that
break. `score` and `cycles` work perfectly on an install whose MCP server cannot
start, which is why the checks below now cover the agent-facing surface too.

Used by `.github/workflows/smoke.yml`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Generous because this spawns a server process and completes an MCP handshake
# on three OSes, including a cold Windows runner.
_HANDSHAKE_TIMEOUT_S = 90


def _make_fixture(root: Path) -> Path:
    pkg = root / "sample"
    (pkg / "foo").mkdir(parents=True)
    (pkg / "bar").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "foo" / "__init__.py").write_text("from sample.bar import greet\n")
    (pkg / "bar" / "__init__.py").write_text('def greet():\n    return "hi"\n')
    return pkg


def _make_required_reach_fixture(root: Path, *, bootstrap: str) -> Path:
    """A project with a `required:` rule, so `check`'s exit code means something.

    `bootstrap` is the body of `app/commands/__init__.py`: empty leaves the rule
    unsatisfied, importing the registry satisfies it for every command module.
    """
    proj = root / "reach"
    app = proj / "app"
    (app / "commands").mkdir(parents=True)
    (app / "core").mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "core" / "__init__.py").write_text("")
    (app / "core" / "model_registry.py").write_text("REGISTRY = {}\n")
    (app / "commands" / "__init__.py").write_text(bootstrap)
    (app / "commands" / "setup_user.py").write_text("x = 1\n")
    (proj / "archy.yaml").write_text(
        "layers: {}\nforbid: []\n"
        "required:\n  - source: 'app.commands.*'\n    must_reach: app.core.model_registry\n"
        "    reason: standalone entrypoints need the full mapper registry\n"
    )
    return proj


def _run(*args: str, cwd: Path, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(args),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        # A server that starts but never answers would otherwise hang the job
        # until the runner's own timeout, turning a clear failure into a
        # mysterious one. Report it as a failed run instead.
        # `TimeoutExpired.stdout` is typed `bytes | str` even under text=True.
        partial = (
            exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
        )
        return subprocess.CompletedProcess(
            list(args), 124, partial or "", f"timed out after {timeout}s"
        )


def _fail(label: str, proc: subprocess.CompletedProcess[str]) -> int:
    print(f"FAIL: {label} exit={proc.returncode}", file=sys.stderr)
    print(proc.stdout, proc.stderr, file=sys.stderr)
    return 1


# Completes a real MCP handshake against the INSTALLED server and prints the
# tool count. Run in a subprocess with the same interpreter that imported the
# installed archy, so it exercises the shipped code rather than the checkout.
_HANDSHAKE = """
import asyncio, json, sys
from mcp import ClientSession, StdioServerParameters
import mcp.client.stdio as cs

async def main():
    params = StdioServerParameters(command=sys.argv[1], args=["mcp"])
    async with (
        cs.stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = (await session.list_tools()).model_dump(by_alias=True)["tools"]
        print(json.dumps({"count": len(tools), "names": sorted(t["name"] for t in tools)}))

asyncio.run(main())
"""


def _check_mcp_surface(archy_path: str, root: Path) -> int:
    """The surface #391 broke, and the one the CLI checks cannot see."""
    imports = _run(
        sys.executable,
        "-c",
        "from archy.mcp import create_server; create_server(); print('ok')",
        cwd=root,
    )
    if imports.returncode != 0:
        print(
            "FAIL: the installed MCP server does not import. This is the #391 shape: "
            "an incompatible `mcp` SDK resolved at install time. The CLI checks above "
            "pass regardless, so treat this as a release blocker.",
            file=sys.stderr,
        )
        return _fail("`create_server()`", imports)
    print("OK   MCP server imports and constructs")

    # Importing is necessary but not sufficient: it proves the module loads, not
    # that the server answers. A handshake is what an agent actually does first.
    handshake = _run(
        sys.executable, "-c", _HANDSHAKE, archy_path, cwd=root, timeout=_HANDSHAKE_TIMEOUT_S
    )
    if handshake.returncode != 0:
        return _fail("MCP stdio handshake", handshake)
    try:
        payload = json.loads(handshake.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print(f"FAIL: handshake produced no JSON:\n{handshake.stdout}", file=sys.stderr)
        return 1
    if payload["count"] < 1 or "archy_check" not in payload["names"]:
        print(f"FAIL: handshake listed no usable tools: {payload}", file=sys.stderr)
        return 1
    print(f"OK   MCP stdio handshake listed {payload['count']} tools")
    return 0


def _check_gate_exit_codes(archy_path: str, root: Path) -> int:
    """`archy check` must actually FAIL on a violation, from the wheel.

    Asserting only the clean case would pass on a build where the gate never
    fires, which is indistinguishable from a codebase with nothing wrong.
    """
    violating = _make_required_reach_fixture(root, bootstrap="")
    proc = _run(archy_path, "check", str(violating), cwd=root)
    if proc.returncode != 1:
        print(
            f"FAIL: `archy check` on a violation exited {proc.returncode}, want 1",
            file=sys.stderr,
        )
        print(proc.stdout, proc.stderr, file=sys.stderr)
        return 1
    if "required-reach violation" not in proc.stdout:
        print(f"FAIL: violation not reported:\n{proc.stdout}", file=sys.stderr)
        return 1
    print("OK   archy check exits 1 and names the violation")

    satisfied = _make_required_reach_fixture(
        root / "fixed", bootstrap="from app.core import model_registry\n"
    )
    proc = _run(archy_path, "check", str(satisfied), cwd=root)
    if proc.returncode != 0:
        return _fail("`archy check` on a satisfied rule", proc)
    print("OK   archy check exits 0 once the rule is satisfied")
    return 0


def main() -> int:
    archy_path = shutil.which("archy")
    if archy_path is None:
        print("FAIL: `archy` not on PATH after install", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pkg = _make_fixture(root)

        version_proc = _run(archy_path, "--version", cwd=root)
        if version_proc.returncode != 0:
            return _fail("`archy --version`", version_proc)

        # Guard against testing the WRONG archy. `shutil.which` returns whatever
        # PATH offers, and a machine with a global tool install can shadow the
        # venv: a protocol test written during #391 silently ran against a
        # year-old archy from PATH and passed 5 of 6 assertions against it.
        installed = _run(sys.executable, "-c", "import archy; print(archy.__version__)", cwd=root)
        if installed.returncode != 0:
            return _fail("importing the installed archy", installed)
        expected = installed.stdout.strip()
        if expected not in version_proc.stdout:
            print(
                f"FAIL: PATH `archy` reports {version_proc.stdout.strip()!r} but the "
                f"importable package is {expected!r}. The smoke test would be checking "
                "a different build than the one just installed.",
                file=sys.stderr,
            )
            return 1
        print(f"OK   archy on PATH: {archy_path}")
        print(f"OK   {version_proc.stdout.strip()} matches the installed package")

        score_proc = _run(archy_path, "score", str(pkg), cwd=root)
        if score_proc.returncode != 0:
            return _fail("`archy score`", score_proc)
        if "archy score:" not in score_proc.stdout:
            print("FAIL: `archy score` output missing 'archy score:' line", file=sys.stderr)
            print(score_proc.stdout, file=sys.stderr)
            return 1
        print("OK   archy score completed with recognisable output")

        cycles_proc = _run(archy_path, "cycles", str(pkg), cwd=root)
        if cycles_proc.returncode != 0:
            return _fail("`archy cycles`", cycles_proc)
        print("OK   archy cycles completed")

        if (rc := _check_gate_exit_codes(archy_path, root)) != 0:
            return rc
        if (rc := _check_mcp_surface(archy_path, root)) != 0:
            return rc

    print("SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
