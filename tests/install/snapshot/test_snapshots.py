"""Layer 2: snapshot the bytes each adapter would emit per (adapter, OS, scope).

Writers are pure functions of (scope, paths), so all three OSes are snapshotted
from one Linux runner by faking the platform via `simulate_os`. Paths are
tokenized (`<HOME>`, `<APPDATA>`, ...) so the snapshot is stable across runners
and tmp dirs. A drift in any emitted config fails here loudly.
"""

from __future__ import annotations

import pytest

from archy.install.base import Scope, apply_plan
from archy.install.registry import adapter_ids, get_adapter
from archy.install.writer import DryRunWriteSystem
from tests.install.conftest import PLATFORMS


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("scope", [Scope.GLOBAL, Scope.LOCAL])
@pytest.mark.parametrize("adapter_id", adapter_ids())
def test_emitted_config_snapshot(adapter_id, scope, platform, simulate_os, snapshot):
    fake = simulate_os(platform)
    adapter = get_adapter(adapter_id)
    project_root = fake.home / "myproject"

    ws = DryRunWriteSystem()
    plan = adapter.plan(scope, project_root=project_root, seed_permissions=True)
    apply_plan(plan, ws)

    emitted = {fake.tokenize(r.path): r.content for r in ws.records}
    assert emitted == snapshot
