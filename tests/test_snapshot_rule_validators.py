"""A hand-edited or older snapshot with a malformed rule is refused, with why (#706).

`archy diff --baseline` reads these files. Without a validator the bad value is
accepted and the rules it should have recorded are silently something else.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.diff import read_snapshot

MALFORMED = {
    "forbid-pair": ("forbid", [["a"]], "forbid pair must be two strings, got ['a']"),
    "forbid-types": ("forbid", [["a", 1]], "forbid pair must be two strings, got ['a', 1]"),
    # Too long, which a `< 2` check would truncate to the first two without a word.
    "forbid-extra": (
        "forbid",
        [["a", "b", "c"]],
        "forbid pair must be two strings, got ['a', 'b', 'c']",
    ),
    "layer-extra": (
        "layers",
        [["web", [], "x"]],
        "layer entry must be [name, patterns], got ['web', [], 'x']",
    ),
    "required-source-type": (
        "required",
        [{"source": 1, "must_reach": "b"}],
        "required entry needs string source and must_reach, got {'source': 1, 'must_reach': 'b'}",
    ),
    "required-reach-type": (
        "required",
        [{"source": "a", "must_reach": 2}],
        "required entry needs string source and must_reach, got {'source': 'a', 'must_reach': 2}",
    ),
    "layer-shape": ("layers", [["only"]], "layer entry must be [name, patterns], got ['only']"),
    "layer-name": ("layers", [[1, []]], "layer entry must be [name, patterns], got [1, []]"),
    "layer-patterns": (
        "layers",
        [["web", ["ok", 2]]],
        "layer patterns must be strings, got ['web', ['ok', 2]]",
    ),
    "required-ends": (
        "required",
        [{"source": "a"}],
        "required entry needs string source and must_reach, got {'source': 'a'}",
    ),
    "required-within": (
        "required",
        [{"source": "a", "must_reach": "b", "within": 3}],
        "required within must be a string or null, got 3",
    ),
}


def _snapshot_with(root: Path, rules: Mapping[str, object]) -> Path:
    """A real snapshot of a one-module project, with its rules replaced."""
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    assert CliRunner().invoke(main, ["snapshot", str(root)]).exit_code == 0
    path = root / ".archy" / "baseline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rules"] = rules
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_well_formed_rules_are_read(tmp_path: Path):
    """Control: the same entries in their right shapes load whole, so each refusal
    below is about the one thing changed."""
    rules = {
        "forbid": [["a", "b"]],
        "layers": [["web", ["pkg.**"]]],
        "required": [
            {"source": "a", "must_reach": "b", "within": "pkg"},
            {"source": "c", "must_reach": "d", "within": None},
        ],
    }

    snapshot = read_snapshot(_snapshot_with(tmp_path, rules))

    assert snapshot is not None and snapshot.rules is not None
    assert snapshot.rules.forbid == (("a", "b"),)
    assert snapshot.rules.layers == (("web", ("pkg.**",)),)
    assert snapshot.rules.required == (("a", "b", "pkg"), ("c", "d", None))


@pytest.mark.parametrize(("key", "value", "reason"), MALFORMED.values(), ids=MALFORMED)
def test_a_malformed_rule_is_refused_with_its_reason(
    tmp_path: Path, key: str, value: object, reason: str
):
    path = _snapshot_with(tmp_path, {key: value})

    with pytest.raises(ValueError, match="not a readable archy snapshot") as caught:
        read_snapshot(path)
    assert str(caught.value).endswith(f"re-run `archy snapshot`: {reason}")

    result = CliRunner().invoke(main, ["diff", str(tmp_path), "--baseline", str(path)])

    assert result.exit_code == 1
    assert result.output.startswith("Error: ")
    assert result.output.rstrip().endswith(reason)
