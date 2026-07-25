"""The published walkthrough must keep being true.

`docs/WALKTHROUGH.md` makes four falsifiable claims about archy's behavior:
`check`, `cycles`, and `dsm` miss a transitive layer violation, and `contracts`
catches it and names the chain. `bench/walkthrough.py` asserts all four and
exits non-zero if any stops holding.

Running it here means a change to layer checking, cycle detection, the DSM, or
the contracts wrapper fails CI rather than silently turning a published page
into a false claim. It costs ~1s.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bench" / "walkthrough.py"

pytest.importorskip(
    "importlinter",
    reason="the walkthrough's payoff step is `archy contracts`, which needs archy[contracts]",
)


def test_walkthrough_claims_still_hold():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, (
        "bench/walkthrough.py reported that docs/WALKTHROUGH.md is now wrong.\n"
        "Fix the doc and the script together; do not just silence this.\n\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    # Spot-check the two claims that carry the page, so a script that starts
    # exiting 0 without doing anything cannot pass this test.
    assert "MISSES IT" in proc.stdout
    assert "CATCHES IT" in proc.stdout
    assert (
        "shipping.store.repository -> shipping.common.tenancy -> shipping.api.context"
        in proc.stdout
    )
