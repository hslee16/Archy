"""The skill tells agents to report what a check covered, by field name (#739).

A renamed field would leave the instruction pointing at nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from archy.layers_models import LayerCoverage
from archy.mcp_payloads import BriefPayload, CheckPayload

ROOT = Path(__file__).resolve().parents[1]
SKILL = (ROOT / "skills" / "archy" / "SKILL.md").read_text(encoding="utf-8")
SECTION = SKILL.split("**Reporting what a check covered.**")[1].split("**Which graph tool")[0]

COVERAGE_FIELDS = {
    "module_ratio",
    "edge_ratio",
    "empty_layers",
    "modules_outside_declared_roots",
    "governs_nothing",
    "governs_no_edges",
}
PAYLOAD_FIELDS = {"coverage", "transitive_checked", "transitive_unverified_reason"}


def _serialized_fields(model: type[BaseModel]) -> set[str]:
    return set(model.model_json_schema(mode="serialization")["properties"])


def test_every_field_the_guidance_names_is_on_the_wire():
    named = {name for name in re.findall(r"`([a-z_]+)`", SECTION) if "_" in name}
    tools = {"archy_check", "archy_brief"}

    assert named >= COVERAGE_FIELDS
    assert named - tools - {"forbid", "required"} <= COVERAGE_FIELDS | PAYLOAD_FIELDS
    assert _serialized_fields(LayerCoverage) >= COVERAGE_FIELDS
    for payload in (CheckPayload, BriefPayload):
        assert _serialized_fields(payload) >= PAYLOAD_FIELDS, payload.__name__


def test_both_readme_rows_point_at_the_guidance():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    rows = [
        line
        for line in readme.splitlines()
        if line.startswith(("| `archy_check` |", "| `archy_brief` |"))
    ]

    assert len(rows) == 2
    assert all("Reporting what a check covered" in row for row in rows)
