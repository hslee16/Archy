"""`server.json` must not drift from the package version.

`server.json` is what the official modelcontextprotocol registry consumes. It
is not imported by any code, so nothing exercises it and nothing fails when it
goes stale. It drifted for 23 releases before anyone looked: `pyproject.toml`
said 0.42.0, `server.json` said 0.19.0, and the live registry entry said
0.13.3, published on launch day and never refreshed.

A checklist line would not have caught that, because the release flow already
had one and it was skipped every time. A test fails the build instead.

Note the version appears TWICE in `server.json`: once at the top level and
once on the pypi package entry. A release that updates only one is still
wrong, so both are asserted separately.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 3.11+ ships `tomllib`; the `tomli` backport covers archy's 3.10 floor,
# matching the pattern in src/archy/install/merge.py.
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import archy

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_JSON = REPO_ROOT / "server.json"
PYPROJECT = REPO_ROOT / "pyproject.toml"

_HINT = (
    "Update server.json (BOTH the top-level `version` and "
    "`packages[0].version`) as part of the release, then republish the "
    "registry entry. See issue #340."
)


def _pyproject_version() -> str:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]


def _server_json() -> dict:
    return json.loads(SERVER_JSON.read_text(encoding="utf-8"))


def test_server_json_top_level_version_matches_pyproject():
    assert _server_json()["version"] == _pyproject_version(), _HINT


def test_server_json_package_version_matches_pyproject():
    packages = _server_json()["packages"]
    assert len(packages) == 1, "expected exactly one published package entry"
    assert packages[0]["version"] == _pyproject_version(), _HINT


def test_package_dunder_version_matches_pyproject():
    # The third copy of the same number. Cheap to assert while we are here.
    assert archy.__version__ == _pyproject_version()


def test_server_json_identity_matches_the_registry_ownership_marker():
    # The registry authenticates ownership by matching this name against the
    # `<!-- mcp-name: ... -->` comment in README.md. If either side is edited
    # alone, republishing silently fails the ownership check.
    name = _server_json()["name"]
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert f"<!-- mcp-name: {name} -->" in readme, (
        f"server.json name {name!r} has no matching mcp-name marker in README.md"
    )
