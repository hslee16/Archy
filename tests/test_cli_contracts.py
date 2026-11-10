"""The `contracts` and `brief` commands and the transitive-contract handoff."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from archy.cli import (
    main,
)
from archy.contracts import ContractsConfigError, ContractsNotAvailable
from tests.cli_helpers import (
    _SHIPPING_LAYERS_NO_FORBID,
    _invoke_json,
    _make_empty_package_project,
    _make_uncovered_forbid_project,
    _stub_run_contracts_raising,
    _write_transitive_violation_project,
)


def test_check_and_contracts_still_report_malformed_yaml(tmp_path: Path):
    """#524: check/contracts keep their existing clean Error path."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (tmp_path / "archy.yaml").write_text("layers: [\n")
    for cmd in (["check", str(tmp_path)], ["contracts", str(tmp_path)]):
        result = CliRunner().invoke(main, cmd)
        assert result.exit_code == 1, (cmd, result.output)
        assert "Traceback" not in result.output
        assert "archy.yaml" in result.output


def test_contracts_exits_one_on_a_forbidden_transitive_path(tmp_path: Path):
    """#435: this exact project reported `OK` at exit 0. `contracts` is the
    surface `check` hands users to when its own coverage is too thin, so a
    false pass here leaves them more confident and no better protected."""
    root = _write_transitive_violation_project(tmp_path, store_pattern="top.store.**")

    result = CliRunner().invoke(main, ["contracts", str(root)])

    assert result.exit_code == 1
    assert "0 kept, 1 broken" in result.output
    # The chain, not just the verdict: it is what the user acts on.
    assert "top.store -> top.service -> top.api" in result.output


def test_contracts_does_not_render_an_unfalsifiable_contract_as_ok(tmp_path: Path):
    """A contract whose pattern matches nothing holds whatever the code does.
    `OK` at exit 0 is indistinguishable from real protection (#435)."""
    root = _write_transitive_violation_project(tmp_path, store_pattern="top.*.handlers")

    result = CliRunner().invoke(main, ["contracts", str(root)])

    assert result.exit_code == 1
    assert "1 unverifiable" in result.output
    assert "could not have failed" in result.output
    assert "top.*.handlers" in result.output
    # The marker has to differ from the one a real pass gets, or the line
    # still reads as protection.
    assert "OK store" not in result.output


def test_contracts_json_says_verified_separately_from_all_kept(tmp_path: Path):
    """A machine reader needs the two facts apart: import-linter calls an
    unfalsifiable contract kept, so `all_kept` alone cannot express it."""
    root = _write_transitive_violation_project(tmp_path, store_pattern="top.*.handlers")

    result = CliRunner().invoke(main, ["contracts", str(root), "--format", "json"])

    payload = json.loads(result.output)
    assert payload["all_kept"] is True
    assert payload["verified"] is False
    assert payload["unverifiable"] == 1
    assert payload["contracts"][0]["matched_nothing"] is True
    assert payload["contracts"][0]["unmatched_expressions"] == ["top.*.handlers"]


def test_check_contracts_does_not_claim_a_transitive_check_it_did_not_do(tmp_path: Path):
    """#457. `transitive_checked` answers "were the forbid rules evaluated
    transitively". A contract whose patterns matched nothing was not evaluated,
    so claiming otherwise is the #435 shape one surface further out."""
    root = _write_transitive_violation_project(tmp_path, store_pattern="top.*.handlers")

    result = CliRunner().invoke(main, ["check", str(root), "--contracts", "--format", "json"])

    payload = json.loads(result.output)
    # The fixture has to reach the branch: contracts ran and produced a result.
    assert payload["contracts"]["unverifiable"] == 1
    assert payload["transitive_checked"] is False
    # A verdict without a reason is indistinguishable from a bug in archy.
    assert "could not have failed" in payload["transitive_unverified_reason"]


def test_check_contracts_still_claims_the_check_when_every_contract_was_real(tmp_path: Path):
    """The other side, or `transitive_checked` could just be hardcoded False.
    Same project, a pattern that matches, so the contract is really evaluated."""
    root = _write_transitive_violation_project(tmp_path, store_pattern="top.store.**")

    result = CliRunner().invoke(main, ["check", str(root), "--contracts", "--format", "json"])

    payload = json.loads(result.output)
    assert payload["contracts"]["unverifiable"] == 0
    assert payload["transitive_checked"] is True
    assert payload["transitive_unverified_reason"] is None


def test_contracts_missing_config_exits_one(tmp_path: Path):
    # Regression for #170 (F8): missing contracts config exits 1 (like
    # check/score gate failures), not 2, and prints a clean message rather
    # than a traceback.
    _make_empty_package_project(tmp_path)
    result = CliRunner().invoke(main, ["contracts", str(tmp_path)])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_check_handoff_is_silent_once_contracts_was_asked_for(tmp_path: Path):
    root = _make_uncovered_forbid_project(tmp_path)
    result = CliRunner().invoke(main, ["check", str(root), "--contracts"])
    assert "evaluates them transitively" not in result.output


def test_check_handoff_is_silent_without_forbid_rules(tmp_path: Path):
    """Nothing to verify means nothing to hand off. A line printed on every run
    is one a reader learns to skip."""
    root = _make_uncovered_forbid_project(tmp_path)
    (root / "archy.yaml").write_text(_SHIPPING_LAYERS_NO_FORBID)
    result = CliRunner().invoke(main, ["check", str(root)])
    assert "--contracts" not in result.output


def test_check_contracts_does_not_change_the_exit_code(tmp_path: Path):
    """`--contracts` REPORTS. A flag that can turn a passing check into a failing
    one would change what a green check has always meant, and would make the
    flag unsafe to leave on in CI."""
    root = _make_uncovered_forbid_project(tmp_path)
    plain = CliRunner().invoke(main, ["check", str(root)])
    withc = CliRunner().invoke(main, ["check", str(root), "--contracts"])
    assert withc.exit_code == plain.exit_code


def test_brief_answers_the_four_questions_and_stays_small(tmp_path: Path):
    """`brief` exists because of an economic argument: reading is ~66x cheaper
    than writing on an inference box, so a briefing only pays if it is small
    enough to inject and complete enough to prevent a derivation."""
    root = _make_uncovered_forbid_project(tmp_path)
    result = CliRunner().invoke(main, ["brief", str(root)])
    assert result.exit_code == 0
    for heading in (
        "what kind of thing",
        "what must change WITH it",
        "does a new finding gate",
        "cannot see",
    ):
        assert heading in result.output
    assert "layer coverage" in result.output
    assert len(result.output) < 20_000


def test_brief_is_advisory_and_never_gates(tmp_path: Path):
    root = _make_uncovered_forbid_project(tmp_path)
    assert CliRunner().invoke(main, ["brief", str(root)]).exit_code == 0


def test_check_contracts_says_why_it_has_no_verdict_on_every_surface(tmp_path: Path, monkeypatch):
    """A verdict without a reason is not actionable. When import-linter is
    absent, `contracts` missing from the JSON entirely is indistinguishable from
    a bug in archy, and a reason on stderr is carried by neither structured
    stream. The MCP surface has always reported `available`/`error` here."""
    _stub_run_contracts_raising(
        monkeypatch, ContractsNotAvailable("import-linter is not installed")
    )
    root = _make_uncovered_forbid_project(tmp_path)

    payload = _invoke_json(["check", str(root), "--contracts", "--format", "json"])
    assert payload["contracts"]["available"] is False
    assert "import-linter" in payload["contracts"]["error"]

    text = CliRunner().invoke(main, ["check", str(root), "--contracts"]).output
    assert "# contracts: no verdict (import-linter is not installed)" in text


def test_brief_contracts_says_why_it_has_no_verdict(tmp_path: Path, monkeypatch):
    """An unreadable contracts config is `available=True` with a reason, the way
    `mcp_runs.run_contracts` distinguishes it from a missing dependency."""
    _stub_run_contracts_raising(monkeypatch, ContractsConfigError("no contracts config found"))
    root = _make_uncovered_forbid_project(tmp_path)

    payload = _invoke_json(["brief", str(root), "--contracts", "--format", "json"])
    assert payload["contracts"]["available"] is True
    assert "no contracts config" in payload["contracts"]["error"]

    text = CliRunner().invoke(main, ["brief", str(root), "--contracts"]).output
    assert "# contracts: no verdict (no contracts config found)" in text


def test_check_json_keeps_a_reason_when_contracts_could_not_run(tmp_path: Path, monkeypatch):
    """The failure this PR exists to close, one level down. Requesting
    `--contracts` and getting no verdict leaves the forbid rules exactly as
    unverified as never asking, so dropping the reason there would be the same
    silent clean pass in a different disguise. It must not name `--contracts`
    back at a caller who just passed it and watched it fail."""
    _stub_run_contracts_raising(monkeypatch, ContractsConfigError("no contracts config found"))
    root = _make_uncovered_forbid_project(tmp_path)

    payload = _invoke_json(["check", str(root), "--contracts", "--format", "json"])

    assert payload["transitive_checked"] is False
    reason = payload["transitive_unverified_reason"]
    assert "no contracts config found" in reason
    assert "produced no transitive verdict" in reason


def test_brief_json_says_whether_it_looked_transitively(tmp_path: Path):
    """The fourth surface. brief's text has rendered this handoff since it
    shipped and its JSON had not, which left the gap #343 closed on `check`
    open one command over, on the output a harness parses rather than reads."""
    root = _make_uncovered_forbid_project(tmp_path)

    payload = _invoke_json(["brief", str(root), "--format", "json"])

    assert payload["transitive_checked"] is False
    assert "forbid rule" in payload["transitive_unverified_reason"]


def test_brief_json_reason_is_absent_without_an_archy_yaml(tmp_path: Path):
    """No config means no rule to leave unverified, and brief still reports.
    A reason here would be asserting a gap in a governance that was never
    declared."""
    root = _make_uncovered_forbid_project(tmp_path)
    (root / "archy.yaml").unlink()

    payload = _invoke_json(["brief", str(root), "--format", "json"])

    assert payload["transitive_checked"] is False
    assert payload["transitive_unverified_reason"] is None
