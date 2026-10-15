"""Precision pilot for #387 item 4: does "some siblings reach it, some do not" name real gaps?

Pre-registration, written and committed before this file existed:
[`docs/research/REACH_ASYMMETRY_PRECISION.md`](../docs/research/REACH_ASYMMETRY_PRECISION.md).
The sensor ships only if precision >= 0.40, lift over the below-band control
>= 2.0, and there are >= 30 scorable firings from >= 5 projects.

Static and git-only, so it spends no agent time. Each project is extracted at
two snapshots (`T0`, a year before the pinned SHA, and `T1`, the pinned SHA)
with `git archive`, so the cached clones under `bench/repo_cache/` are read
and never checked out or modified.

    uv run --with pyyaml python bench/reach_asymmetry_precision.py --canary
    uv run --with pyyaml python bench/reach_asymmetry_precision.py --count-only
    uv run --with pyyaml python bench/reach_asymmetry_precision.py

archy:owns        Firing, firings, main
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
from _common import REPO_ROOT, load_manifest

from archy.duplicates import is_test_path
from archy.graph import build_graph
from archy.reach import with_package_init_edges

CACHE = REPO_ROOT / "bench" / "repo_cache"
LEDGER = REPO_ROOT / "bench" / "reach_asymmetry_precision.jsonl"

# Every value below is fixed by the pre-registration. Changing one after the
# run is a different hypothesis, not a tuning step.
MIN_SIBLINGS = 6
BAND = (0.2, 0.8)
MIN_SIDE = 2
WINDOW_DAYS = 365
GATE_PRECISION = 0.40
GATE_LIFT = 2.0
GATE_FIRINGS = 30
GATE_PROJECTS = 5


@dataclass(frozen=True)
class Firing:
    """One reach-set class inside one package: the unit a reader would see."""

    package: str
    siblings: frozenset[str]
    reaching: frozenset[str]
    targets: frozenset[str] = field(compare=False)

    @property
    def missing(self) -> frozenset[str]:
        return self.siblings - self.reaching


def _internal(graph: nx.DiGraph, src_root: Path) -> set[str]:
    """Internal non-test modules. Test status is judged on the path RELATIVE to
    the snapshot root, because the absolute path runs through a temp directory
    whose own components could otherwise match a test-directory name."""
    keep = set()
    base = src_root.resolve()  # macOS temp dirs sit behind a /private symlink
    for node, data in graph.nodes(data=True):
        if data.get("external"):
            continue
        path = data.get("path")
        rel = Path(path).resolve().relative_to(base) if path else Path(node.replace(".", "/"))
        if not is_test_path(str(rel)):
            keep.add(node)
    return keep


def _parent(module: str) -> str | None:
    return module.rsplit(".", 1)[0] if "." in module else None


def _ancestors(module: str) -> set[str]:
    parts = module.split(".")
    return {".".join(parts[:i]) for i in range(1, len(parts))}


def firings(graph: nx.DiGraph, src_root: Path) -> tuple[list[Firing], list[Firing]]:
    """(in-band firings, below-band control classes) for one snapshot."""
    reach_graph = with_package_init_edges(graph)
    internal = _internal(graph, src_root)
    children: dict[str, set[str]] = defaultdict(set)
    for module in internal:
        parent = _parent(module)
        if parent is not None and parent in internal:
            children[parent].add(module)

    fired: list[Firing] = []
    control: list[Firing] = []
    for package, siblings in sorted(children.items()):
        n = len(siblings)
        if n < MIN_SIBLINGS:
            continue
        excluded = siblings | {package} | _ancestors(package)
        reached_by: dict[str, set[str]] = defaultdict(set)
        for sibling in siblings:
            for target in nx.descendants(reach_graph, sibling) & internal:
                if target not in excluded:
                    reached_by[target].add(sibling)
        classes: dict[frozenset[str], set[str]] = defaultdict(set)
        for target, reaching in reached_by.items():
            classes[frozenset(reaching)].add(target)
        for reaching, targets in classes.items():
            k = len(reaching)
            item = Firing(package, frozenset(siblings), reaching, frozenset(targets))
            if BAND[0] <= k / n <= BAND[1] and min(k, n - k) >= MIN_SIDE:
                fired.append(item)
            elif k / n < BAND[0]:
                control.append(item)
    return fired, control


def score(item: Firing, later: nx.DiGraph, later_internal: set[str]) -> str:
    """'tp' / 'fp' / 'unscorable' under the propagation oracle, plus whether the
    split was retracted instead (reported, never gating)."""
    reach_graph = with_package_init_edges(later)
    missing = item.missing & later_internal
    targets = item.targets & later_internal
    if not missing or not targets:
        return "unscorable"
    for module in sorted(missing):
        if nx.descendants(reach_graph, module) & targets:
            return "tp"
    reaching = item.reaching & later_internal
    if reaching and not any(nx.descendants(reach_graph, m) & targets for m in reaching):
        return "fp_retracted"
    return "fp"


def _git(clone: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(clone), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _extract(clone: Path, sha: str, src_dir: str, into: Path) -> Path | None:
    """`src_dir` at `sha` under `into`, or None when it did not exist then."""
    res = subprocess.run(
        ["git", "-C", str(clone), "archive", sha, "--", src_dir], capture_output=True
    )
    if res.returncode != 0 or not res.stdout:
        return None
    with tarfile.open(fileobj=io.BytesIO(res.stdout)) as tar:
        tar.extractall(into, filter="data")
    root = into / src_dir
    return root if root.is_dir() else None


def _snapshot(clone: Path, sha: str, src_dir: str, workdir: Path):
    root = _extract(clone, sha, src_dir, workdir)
    if root is None:
        return None
    graph = build_graph(root, max_modules=0)
    return graph, root


def run_project(proj: dict, count_only: bool) -> dict:
    name, src_dir = proj["name"], proj["src_dir"]
    clone = CACHE / name
    if not clone.is_dir():
        return {"project": name, "status": "skipped", "reason": "no cached clone"}
    t1 = _git(clone, "rev-parse", f"{proj['sha']}^{{commit}}")
    t1_date = dt.datetime.fromisoformat(_git(clone, "log", "-1", "--format=%cI", t1))
    cutoff = (t1_date - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    t0 = _git(clone, "rev-list", "--first-parent", "-1", f"--before={cutoff}", t1)
    if not t0:
        return {"project": name, "status": "skipped", "reason": "no commit a year before T1"}
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        before = _snapshot(clone, t0, src_dir, Path(a))
        if before is None:
            return {"project": name, "status": "skipped", "reason": f"{src_dir} absent at T0"}
        fired, control = firings(*before)
        row: dict = {
            "project": name,
            "status": "ok",
            "t0": t0[:12],
            "t1": t1[:12],
            "firings": len(fired),
            "control": len(control),
        }
        if count_only:
            return row
        after = _snapshot(clone, t1, src_dir, Path(b))
        if after is None:
            return {**row, "status": "skipped", "reason": f"{src_dir} absent at T1"}
        later, later_root = after
        later_internal = _internal(later, later_root)
        row["fired"] = [_record(f, score(f, later, later_internal)) for f in fired]
        row["control_scores"] = [score(c, later, later_internal) for c in control]
    return row


def _record(item: Firing, verdict: str) -> dict:
    return {
        "package": item.package,
        "n": len(item.siblings),
        "k": len(item.reaching),
        "targets": sorted(item.targets)[:5],
        "target_count": len(item.targets),
        "missing": sorted(item.missing)[:5],
        "verdict": verdict,
    }


def _canary() -> bool:
    """The incident, rebuilt: 34 commands, 11 import the registry, 2 reach it via
    an unrelated module, 21 do not. Then remove one reaching edge."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "app"
        (root / "commands").mkdir(parents=True)
        (root / "core").mkdir()
        for path in ("__init__.py", "commands/__init__.py", "core/__init__.py"):
            (root / path).write_text("")
        (root / "core" / "registry.py").write_text("")
        (root / "core" / "billing.py").write_text("from app.core import registry\n")
        # The second transitive reacher gets there only through a package
        # `__init__`, so the canary goes red if the init edges are dropped.
        (root / "core" / "models").mkdir()
        (root / "core" / "models" / "__init__.py").write_text("from app.core import registry\n")
        (root / "core" / "models" / "user.py").write_text("")
        for i in range(34):
            body = ""
            if i < 11:
                body = "from app.core import registry\n"
            elif i == 11:
                body = "from app.core import billing\n"
            elif i == 12:
                body = "from app.core.models import user\n"
            # 8 of 34 share a sibling helper, in band, so the canary goes red
            # if siblings stop being excluded as targets.
            if i < 8:
                body += "from app.commands import cmd33\n"
            (root / "commands" / f"cmd{i:02}.py").write_text(body)
        fired, _ = firings(build_graph(root, max_modules=0), root)
        hit = [f for f in fired if "app.core.registry" in f.targets]
        transitive = {"app.commands.cmd11", "app.commands.cmd12"}
        ok = (
            len(hit) == 1
            and len(hit[0].missing) == 21
            and transitive <= hit[0].reaching
            and len(fired) == 1
        )
        firing = hit[0] if hit else None

        def verdict() -> str:
            later = build_graph(root, max_modules=0)
            return score(firing, later, _internal(later, root)) if firing else "none"

        unchanged = verdict()
        (root / "commands" / "cmd20.py").write_text("from app.core import registry\n")
        propagated = verdict()
        (root / "commands" / "cmd20.py").write_text("")
        # Unscorable when every missing module is gone. (Deleting the registry
        # alone is not enough: `app.core` shares its reach class, via the init
        # edges, and survives as a target.)
        missing = sorted(firing.missing) if firing else []
        for module in missing:
            (root / "commands" / f"{module.rsplit('.', 1)[1]}.py").unlink()
        gone = verdict()
        for module in missing:
            (root / "commands" / f"{module.rsplit('.', 1)[1]}.py").write_text("")
        ok = ok and (unchanged, propagated, gone) == ("fp", "tp", "unscorable")

        (root / "commands" / "cmd00.py").write_text("")
        fired, _ = firings(build_graph(root, max_modules=0), root)
        hit2 = [f for f in fired if "app.core.registry" in f.targets]
        ok = ok and len(hit2) == 1 and len(hit2[0].missing) == 22
    print(f"canary: {'PASS' if ok else 'FAIL'}")
    return ok


def _done() -> dict[str, dict]:
    if not LEDGER.exists():
        return {}
    rows = [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]
    return {r["project"]: r for r in rows if r.get("status") in {"ok", "skipped"}}


def summarize(rows: list[dict]) -> dict:
    fired = [f for r in rows if r.get("status") == "ok" for f in r.get("fired", [])]
    scorable = [f for f in fired if f["verdict"] != "unscorable"]
    tp = sum(f["verdict"] == "tp" for f in scorable)
    control = [v for r in rows if r.get("status") == "ok" for v in r.get("control_scores", [])]
    control_scorable = [v for v in control if v != "unscorable"]
    control_tp = sum(v == "tp" for v in control_scorable)
    precision = tp / len(scorable) if scorable else 0.0
    base = control_tp / len(control_scorable) if control_scorable else 0.0
    lift = precision / base if base else float("inf") if precision else 0.0
    projects = {
        r["project"] for r in rows if any(f["verdict"] != "unscorable" for f in r.get("fired", []))
    }
    powered = len(scorable) >= GATE_FIRINGS and len(projects) >= GATE_PROJECTS
    passed = powered and precision >= GATE_PRECISION and lift >= GATE_LIFT
    return {
        "firings": len(fired),
        "scorable": len(scorable),
        "unscorable": len(fired) - len(scorable),
        "tp": tp,
        "retracted": sum(f["verdict"] == "fp_retracted" for f in scorable),
        "precision": round(precision, 3),
        "control_scorable": len(control_scorable),
        "control_tp": control_tp,
        "control_rate": round(base, 3),
        "lift": round(lift, 2) if lift != float("inf") else "inf",
        "projects_scored": len(projects),
        "verdict": "PASS" if passed else ("FAIL" if powered else "UNDERPOWERED (no verdict)"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--canary", action="store_true", help="Run only the incident canary.")
    parser.add_argument("--count-only", action="store_true", help="T0 population sizes, no oracle.")
    parser.add_argument("--only", nargs="*", help="Restrict to these project names.")
    args = parser.parse_args()

    if not _canary():
        return 1
    if args.canary:
        return 0
    projects = [p for p in load_manifest() if not args.only or p["name"] in args.only]
    if args.count_only:
        for proj in projects:
            print(json.dumps(run_project(proj, count_only=True)), flush=True)
        return 0

    done = _done()
    for proj in projects:
        if proj["name"] in done:
            continue
        try:
            row = run_project(proj, count_only=False)
        except Exception as exc:  # one bad project must not end the sweep
            row = {
                "project": proj["name"],
                "status": "error",
                "reason": f"{type(exc).__name__}: {exc}"[:200],
            }
        with LEDGER.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        print(f"{row['project']}: {row['status']} {row.get('reason', '')}", flush=True)
    rows = list(_done().values())
    print(json.dumps(summarize(rows), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
