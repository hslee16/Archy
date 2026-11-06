"""An `archy.yaml` key the loader never reads is an error, not a silent no-op (#525).

`forbidden:` for `forbid:` printed "No layer violations" and exited 0: a rule that
cannot fire looks exactly like a clean codebase. Every level is checked, each error
names the bad key (with a suggestion when one is close), and `exclude:` entries
that can never match a directory name are refused too.

The positive test matters as much as the typo tests: the issue's own list of
allowed keys was stale (it omitted `exclude_type_checking_imports` and `within`),
and a validator built from it would have rejected valid configs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.layers import LayerConfigError, load_config
from archy.mcp import create_server
from archy.mcp_compat import ToolError
from archy.mcp_runs import run_check
from tests.mcp_helpers import call_tool

REPO = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "archy.yaml"
    path.write_text(text)
    return path


LAYERS = "layers:\n  core: {modules: [myapp.core.**]}\n  cli: {modules: [myapp.cli.**]}\n"


@pytest.mark.parametrize(
    ("text", "expected", "absent"),
    [
        pytest.param(
            LAYERS + "forbidden:\n  - {from: core, to: cli}\n",
            ["`forbidden`", "did you mean `forbid`?", "the top level"],
            [],
            id="forbidden-for-forbid",
        ),
        pytest.param(
            "layers:\n  core: {module: [myapp.core.**]}\nforbid: []\n",
            ["layer 'core'", "`module`", "did you mean `modules`?"],
            [],
            id="module-for-modules",
        ),
        pytest.param(
            LAYERS + "forbid:\n  - {from: core, to: cli, reason: core must not know the CLI}\n",
            ["forbid entry 0", "`reason`", "Allowed keys: `from`, `to`"],
            ["did you mean"],  # no close match: a misunderstanding, not a slip
            id="reason-on-a-forbid-entry",
        ),
        pytest.param(
            "layers: {}\nsdp: {enable: true}\n",
            ["`sdp`", "`enable`", "did you mean `enabled`?"],
            [],
            id="sdp-enable-for-enabled",
        ),
        pytest.param(
            "required:\n  - {source: 'myapp.**', must-reach: 'myapp.core', reason: x}\n",
            ["required entry 0", "`must-reach`", "did you mean `must_reach`?"],
            [],
            id="required-key-typo",
        ),
        pytest.param(
            "layers: {}\nforbidden: []\nrequird: []\n",
            ["unknown keys", "`forbidden` (did you mean `forbid`?)", "`requird`"],
            [],
            id="every-unknown-key-is-named",
        ),
    ],
)
def test_an_unknown_key_is_refused_naming_the_key(
    tmp_path: Path, text: str, expected: list[str], absent: list[str]
):
    with pytest.raises(LayerConfigError) as caught:
        load_config(_config(tmp_path, text))
    message = str(caught.value)
    for fragment in expected:
        assert fragment in message, message
    for fragment in absent:
        assert fragment not in message, message


@pytest.mark.parametrize("entry", ["myapp/gen", "myapp\\\\gen", "*.out", "gen?", "gen[0-9]"])
def test_an_exclude_entry_that_can_never_match_a_directory_name_is_refused(
    tmp_path: Path, entry: str
):
    with pytest.raises(LayerConfigError) as caught:
        load_config(_config(tmp_path, f"layers: {{}}\nexclude: ['{entry}']\n"))
    message = str(caught.value)
    assert repr(entry) in message and "whole directory names only" in message, message


def test_every_documented_key_at_every_level_still_loads(tmp_path: Path):
    """Each value is asserted back, so a key that loads by being ignored cannot pass."""
    config = load_config(
        _config(
            tmp_path,
            "layers:\n"
            "  core: {modules: [myapp.core.**]}\n"
            "  cli: {modules: [myapp.cli.**]}\n"
            "forbid:\n"
            "  - {from: core, to: cli}\n"
            "required:\n"
            "  - {source: '@entrypoints', must_reach: 'myapp.core.**', reason: why, "
            "within: 'myapp.cli.**'}\n"
            "exclude: [generated]\n"
            "roots: [myapp]\n"
            "sdp: {enabled: true, tolerance: 0.1, mode: warn}\n"
            "max_modules: 500\n"
            "min_layers_present: 2\n"
            "exclude_type_checking_imports: true\n",
        )
    )
    assert [layer.name for layer in config.layers] == ["core", "cli"]
    assert [(r.from_layer, r.to_layer) for r in config.forbid] == [("core", "cli")]
    [rule] = config.required
    assert (rule.reason, rule.within) == ("why", "myapp.cli.**")
    assert config.exclude == ("generated",)
    assert config.roots == ("myapp",)
    assert (config.sdp.enabled, config.sdp.tolerance, config.sdp.mode) == (True, 0.1, "warn")
    assert config.max_modules == 500
    assert config.min_layers_present == 2
    assert config.exclude_type_checking_imports is True


def _tracked_configs() -> list[Path]:
    found = [REPO / "archy.yaml", REPO / "bench" / "fixtures" / "conduit_clean" / "archy.yaml"]
    found += sorted((REPO / "bench" / "q1b_layers").glob("*.yaml"))
    return found


@pytest.mark.parametrize("path", _tracked_configs(), ids=lambda p: str(p.relative_to(REPO)))
def test_every_config_tracked_in_this_repo_still_loads(path: Path):
    assert path.exists(), f"{path} moved; update the list"
    load_config(path)


@pytest.fixture
def typo_project(tmp_path: Path) -> Path:
    """`domain` imports `infra`; the config forbids it, but spells the key `forbidden`."""
    pkg = tmp_path / "myapp"
    for sub in ("", "domain", "infra"):
        (pkg / sub).mkdir(parents=True, exist_ok=True)
        (pkg / sub / "__init__.py").write_text("")
    (pkg / "domain" / "svc.py").write_text("from myapp.infra import db\n")
    (pkg / "infra" / "db.py").write_text("")
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        "  domain: {modules: [myapp.domain.**]}\n"
        "  infra: {modules: [myapp.infra.**]}\n"
        "forbidden:\n"
        "  - {from: domain, to: infra}\n"
    )
    return tmp_path


def test_check_fails_loudly_on_the_typo_and_finds_the_violation_once_fixed(typo_project: Path):
    typo = CliRunner().invoke(main, ["check", str(typo_project)])
    assert typo.exit_code == 1, typo.output
    assert "Error:" in typo.stderr and "did you mean `forbid`?" in typo.stderr
    assert "No layer violations" not in typo.output

    # Control: the same project with the key spelled correctly really has a violation,
    # so the failure above is the typo and not something else about the fixture.
    config = typo_project / "archy.yaml"
    config.write_text(config.read_text().replace("forbidden:", "forbid:"))
    fixed = CliRunner().invoke(main, ["check", str(typo_project), "--format", "json"])
    assert fixed.exit_code == 1, fixed.output
    assert len(json.loads(fixed.stdout)["violations"]) == 1


def test_mcp_check_raises_a_tool_error_carrying_the_reason(typo_project: Path):
    """A tool error (`isError`), not a `CheckErrorPayload`, which only means no config."""
    with pytest.raises(LayerConfigError, match="did you mean `forbid`"):
        run_check(typo_project, config_path=None)
    with pytest.raises(ToolError) as caught:
        call_tool(create_server(), "archy_check", {"path": str(typo_project)})
    assert "did you mean `forbid`?" in str(caught.value)
