"""What the README says `min_layers_present` does and does not catch (#762).

Pinned so the text cannot drift from the gate: an empty package counts as a
present layer, and the coverage line is what shows the code has left.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from archy.cli import main

CONFIG = """\
layers:
  domain: {modules: ["shop.domain.**"]}
  infra: {modules: ["shop.infra.**"]}
  api: {modules: ["shop.api.**"]}
forbid:
  - {from: domain, to: infra}
min_layers_present: 3
"""


def _collapsed(root: Path) -> Path:
    """All the code in one file, with the three layer packages left behind empty."""
    for layer in ("domain", "infra", "api"):
        (root / "shop" / layer).mkdir(parents=True)
        (root / "shop" / layer / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "everything.py").write_text("import json\n", encoding="utf-8")
    (root / "archy.yaml").write_text(CONFIG, encoding="utf-8")
    return root


def test_empty_layer_packages_satisfy_the_floor_and_coverage_says_why(tmp_path: Path):
    result = CliRunner().invoke(main, ["check", str(_collapsed(tmp_path))])

    assert result.exit_code == 0, result.output
    assert "0 of 0 internal edges" in result.output
    assert "no forbid rule can fire" in result.output


def test_the_floor_fails_once_a_layer_package_is_gone(tmp_path: Path):
    root = _collapsed(tmp_path)
    (root / "shop" / "api" / "__init__.py").unlink()
    (root / "shop" / "api").rmdir()

    result = CliRunner().invoke(main, ["check", str(root)])

    assert result.exit_code == 1
    assert "2 layer(s) present, min_layers_present is 3" in result.output
