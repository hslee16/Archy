#!/usr/bin/env python
"""The reproducible walkthrough: an agent breaks a layer rule, archy catches it.

Run it:

    uv run python bench/walkthrough.py            # print the transcript
    uv run python bench/walkthrough.py --keep DIR # also leave the fixture behind

This is deliberately NOT a benchmark. archy measured agent-side benefit twice
and got nulls both times (#282 footprint, #289 reads), and
`RESEARCH_METRICS.md` 14c.7 forbids an archy-specific token-savings claim. So
this makes no claim about making an agent cheaper or better. It shows one
structural regression, which surfaces catch it, and which do not.

WHY THIS PARTICULAR REGRESSION
------------------------------
`docs/FUTURE.md` records the strongest counterargument archy has to its own
value: in a 2026-05 session on a real codebase, a fresh agent caught a
forbidden cross-layer import *by reading `archy.yaml`*, without ever calling an
archy tool. If reading the config is enough, the tool adds nothing.

That same entry records where reading is NOT enough: "when violations are
transitive multi-hop". This walkthrough is built on exactly that case, because
it is the honest one. The edit adds no forbidden import to any file. It adds a
permitted import, to a permitted helper, which happens to already reach the
forbidden layer. Nothing you can read in the diff says "store imports api".

The script asserts its own expected outcomes, so if archy's behavior ever
changes the walkthrough fails loudly instead of quietly documenting a fiction.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# The fixture. Synthetic, and labelled as such in docs/WALKTHROUGH.md rather
# than dressed up as a real project. The SHAPE is the real part: an indirection
# that routes around a layer boundary, which is the same shape as the
# psycopg-through-db-engine case recorded in FUTURE.md.
# --------------------------------------------------------------------------

FILES: dict[str, str] = {
    "shipping/__init__.py": "",
    "shipping/api/__init__.py": "",
    "shipping/service/__init__.py": "",
    "shipping/store/__init__.py": "",
    "shipping/common/__init__.py": "",
    "shipping/api/context.py": '''"""Per-request context, owned by the API layer."""

_CURRENT_TENANT: str | None = None


def current_tenant() -> str | None:
    return _CURRENT_TENANT
''',
    "shipping/api/routes.py": """from shipping.service.orders import place_order


def post_order(payload: dict) -> dict:
    return {"id": place_order(payload["sku"], payload["qty"])}
""",
    "shipping/service/orders.py": """from shipping.store.repository import save_order


def place_order(sku: str, qty: int) -> str:
    return save_order(sku, qty)
""",
    # `common` is allowed to see API request context. That is its whole job,
    # and it is what makes the later violation invisible to a direct-edge read.
    "shipping/common/tenancy.py": '''"""Tenancy helpers. May read API request context by design."""

from shipping.api.context import current_tenant


def tenant_or_default() -> str:
    return current_tenant() or "public"
''',
    "archy.yaml": """# Intended dependency direction:
#   api -> service -> store
#
# `common` is a shared helper layer deliberately allowed to see API request
# context. The store is NOT: a persisted row must not depend on how the
# request happened to arrive.
layers:
  api:
    modules: ["shipping.api"]
  service:
    modules: ["shipping.service"]
  store:
    modules: ["shipping.store"]
  common:
    modules: ["shipping.common"]

forbid:
  - {from: store, to: api}
  - {from: store, to: service}
  - {from: service, to: api}
""",
}

BEFORE = """_ROWS: list[dict] = []


def save_order(sku: str, qty: int) -> str:
    oid = f"o{len(_ROWS)}"
    _ROWS.append({"id": oid, "sku": sku, "qty": qty})
    return oid
"""

# The agent edit. The task was "stamp the tenant on each saved order". This is
# a reasonable way to do it: the helper already exists and is in a layer the
# store is allowed to import. No forbidden import appears anywhere in the diff.
AFTER = """from shipping.common.tenancy import tenant_or_default

_ROWS: list[dict] = []


def save_order(sku: str, qty: int) -> str:
    oid = f"o{len(_ROWS)}"
    _ROWS.append({"id": oid, "sku": sku, "qty": qty, "tenant": tenant_or_default()})
    return oid
"""

TARGET = "shipping/store/repository.py"


def materialize(root: Path) -> None:
    for rel, body in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (root / TARGET).write_text(BEFORE, encoding="utf-8")


def run(root: Path, *args: str) -> tuple[int, str]:
    """Invoke archy through the current interpreter so the walkthrough tests
    THIS checkout, not whatever archy happens to be on PATH."""
    proc = subprocess.run(
        [sys.executable, "-m", "archy.cli", *args, str(root)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def show(title: str, code: int, out: str, *, limit: int = 24) -> None:
    print(f"\n$ archy {title}")
    lines = out.splitlines()
    for line in lines[:limit]:
        print(line)
    if len(lines) > limit:
        print(f"... ({len(lines) - limit} more lines)")
    print(f"[exit {code}]")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--keep", type=Path, help="write the fixture here and leave it")
    args = parser.parse_args()

    tmp = None
    if args.keep:
        root = args.keep
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
    else:
        tmp = tempfile.mkdtemp(prefix="archy_walkthrough_")
        root = Path(tmp)

    failures: list[str] = []

    def expect(cond: bool, what: str) -> None:
        if not cond:
            failures.append(what)

    try:
        materialize(root)

        print("=" * 72)
        print("BEFORE the edit: the codebase obeys its own rules")
        print("=" * 72)
        code, out = run(root, "check", "--config", str(root / "archy.yaml"))
        show("check .", code, out)
        expect(code == 0, "baseline `archy check` should pass")
        code_c, out_c = run(root, "contracts")
        show("contracts .", code_c, out_c)
        expect(code_c == 0, "baseline `archy contracts` should pass")

        print()
        print("=" * 72)
        print("THE EDIT: stamp the tenant on each saved order")
        print("=" * 72)
        print(f"\n--- a/{TARGET}\n+++ b/{TARGET}")
        print("+from shipping.common.tenancy import tenant_or_default")
        print('+    ... "tenant": tenant_or_default()')
        print("\nNote what is NOT in this diff: any mention of `api`.")
        (root / TARGET).write_text(AFTER, encoding="utf-8")

        print()
        print("=" * 72)
        print("AFTER: three surfaces miss it, one catches it")
        print("=" * 72)

        code, out = run(root, "check", "--config", str(root / "archy.yaml"))
        show("check .   <-- MISSES IT", code, out)
        expect(code == 0, "`archy check` is expected to MISS this (direct edges only)")
        expect("No layer violations" in out, "`archy check` should report no violations")

        code, out = run(root, "cycles")
        show("cycles .   <-- nothing to see, this is not a cycle", code, out)
        expect("No cycles" in out, "the edit should not create a cycle")

        code, out = run(root, "dsm", "--group", "topological")
        show("dsm . --group topological   <-- no back-edge", code, out, limit=30)
        # Assert the miss rather than just displaying it. Under topological
        # grouping a back-edge is a cell below the diagonal (row > col); this
        # violation is a FORWARD edge that happens to cross a forbidden
        # boundary, so there is nothing for the DSM to flag.
        _, out_j = run(root, "dsm", "--group", "topological", "--format", "json")
        cells = json.loads(out_j)["cells"]
        below = [c for c in cells if c["row"] > c["col"]]
        expect(not below, "dsm is expected to show NO back-edge for this violation")

        code, out = run(root, "contracts")
        show("contracts .   <-- CATCHES IT", code, out, limit=30)
        expect(code == 1, "`archy contracts` must exit 1 on the transitive violation")
        expect(
            "shipping.store.repository -> shipping.common.tenancy -> shipping.api.context" in out,
            "contracts must print the full import chain",
        )
        expect(
            "service" in out and "shipping.service.orders" in out,
            "contracts must also surface the SECOND, unintended violation (service -> api)",
        )

        print()
        print("=" * 72)
        print("WHAT THIS DOES AND DOES NOT SHOW")
        print("=" * 72)
        print(
            "\n"
            "The edit adds no forbidden import. Reading the diff, or reading\n"
            "archy.yaml, tells you nothing is wrong: the store imported a helper\n"
            "it is allowed to import. The rule breaks two hops away.\n"
            "\n"
            "`archy check` misses it by design (direct edges only).\n"
            "`archy cycles` misses it: this is not a cycle.\n"
            "`archy dsm` misses it: a forward edge across a forbidden boundary\n"
            "  is not a back-edge, so nothing turns red.\n"
            "`archy contracts` catches it, names the chain, and finds a SECOND\n"
            "  violation the agent never touched: service now reaches api too,\n"
            "  three hops out, through a module the edit never opened.\n"
            "\n"
            "That second one is the actual argument for the tool. Tracing it by\n"
            "hand means holding the whole import graph in your head.\n"
        )

        if failures:
            print("WALKTHROUGH DRIFTED, these expectations no longer hold:")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("All walkthrough expectations hold.")
        return 0
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        elif args.keep:
            print(f"\nfixture left at {root}")


if __name__ == "__main__":
    raise SystemExit(main())
