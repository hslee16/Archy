#!/usr/bin/env python
"""Score `archy conventions` against a hand-written answer key, at pinned SHAs.

    uv run python bench/conventions_bench.py                # score every repo
    uv run python bench/conventions_bench.py --set heldout  # only the held-out set
    uv run python bench/conventions_bench.py --repo click --verbose
    uv run python bench/conventions_bench.py --determinism  # only the stability check

WHY THIS EXISTS. #410 shipped a real out-of-sample measurement -- four projects,
scored by hand against their own source -- and shipped none of the apparatus.
The numbers lived in a pull request description, so nobody could re-derive them
and neither could the next change to the heuristics. This turns that table into
a regression test: the next edit to the shadow-subtree thresholds or the
doc-matching ladder either holds the score or does not.

🔴 THE DEVELOPMENT SET IS NOT A TEST SET, AND THE KEY SAYS SO.
`click`, `mypy`, `pydantic` and `pytest` motivated every feature in #410 and
then scored it. Worse, two heuristics were tuned against them directly: the
doc-strictness ladder took three passes against mypy until it reached 78 of 78,
and the shadow-subtree thresholds were tuned until they caught `pydantic.v1`.
Those four repos are marked `set: development` and their score is a FIT, not a
result. Only `set: heldout` rows are evidence, and they were keyed from the
repos' own source BEFORE this bench was ever run against them.

WHAT A ROW ASSERTS. Each row names a question, the section that must answer it,
and a machine-checkable expectation. A row may also assert SILENCE -- mypy
documents all 78 of its error codes, so the correct output is no doc gap at all,
and pydantic has genuinely not decided its gate question. Scoring "correctly
reports nothing" as a pass is only honest if the key can express it, so
`expect: absent` is a first-class outcome rather than the absence of a row.

DETERMINISM. A flaky renderer makes a flaky score, so every repo is analysed
twice and the two payloads must be byte-identical before any row is scored.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.append(str(REPO_ROOT / "bench"))

from archy.conventions import compute_conventions  # noqa: E402
from run import clone_or_update, load_manifest  # noqa: E402

KEY_PATH = Path(__file__).with_name("conventions_key.yaml")


def analyse(path: Path) -> dict:
    """Run the census twice and refuse to score anything that is not stable."""
    first = compute_conventions(path).model_dump(mode="json")
    second = compute_conventions(path).model_dump(mode="json")
    a = json.dumps(first, sort_keys=True)
    b = json.dumps(second, sort_keys=True)
    if a != b:
        raise SystemExit(
            f"🔴 NON-DETERMINISTIC: two runs over {path} disagree. Scoring is meaningless "
            "until that is fixed; a flaky census makes a flaky regression test."
        )
    return first


# --- one checker per expectation kind -------------------------------------
# Each returns (ok, what_was_actually_found). The second element is what makes a
# failure diagnosable: "expected X, found Y" beats a bare False every time.


def _check_base(report: dict, e: dict):
    fams = [b for b in report["bases"] if b["base"] == e["family"]]
    if not fams:
        return False, "no kind family named %r (found: %s)" % (
            e["family"],
            ", ".join(b["base"] for b in report["bases"][:6]) or "none",
        )
    f = fams[0]
    if "home_module" in e and f["home_module"] != e["home_module"]:
        return False, "home is %s, expected %s" % (f["home_module"], e["home_module"])
    if "min_count" in e and f["count"] < e["min_count"]:
        return False, "count %d < %d" % (f["count"], e["min_count"])
    return True, "%s n=%d @ %s" % (f["base"], f["count"], f["home_module"])


def _check_constant(report: dict, e: dict):
    for b in report["bases"]:
        if b["base"] != e["family"]:
            continue
        for c in b["shared_constants"]:
            if c["name"] == e["constant"]:
                vals = {v for v, _ in c["distribution"]}
                if "values" in e and not set(map(str, e["values"])) <= vals:
                    return False, "%s has values %s, expected to include %s" % (
                        c["name"], sorted(vals), e["values"])
                return True, "%s = %s" % (c["name"], sorted(vals))
        return False, "family %s declares no shared constant %r" % (e["family"], e["constant"])
    return False, "no kind family named %r" % e["family"]


def _check_registry(report: dict, e: dict):
    regs = [r for r in report["registries"] if r["constructor"] == e["constructor"]]
    if not regs:
        return False, "no registry for %r (found: %s)" % (
            e["constructor"],
            ", ".join(r["constructor"] for r in report["registries"][:6]) or "none",
        )
    r = regs[0]
    if "home_module" in e and r["home_module"] != e["home_module"]:
        return False, "home is %s, expected %s" % (r["home_module"], e["home_module"])
    if "min_count" in e and r["count"] < e["min_count"]:
        return False, "count %d < %d" % (r["count"], e["min_count"])
    if "keyword" in e:
        kws = {k["name"] for k in r["keyword_defaults"]}
        if e["keyword"] not in kws:
            return False, "registry has keywords %s, expected %s" % (sorted(kws), e["keyword"])
    return True, "%s n=%d @ %s" % (r["constructor"], r["count"], r["home_module"])


def _check_gap(report: dict, e: dict, field: str):
    gaps = [g for g in report[field] if g["family"] == e["family"]]
    if e.get("absent"):
        # 🔴 Asserting silence. mypy names all 78 of its codes in its own docs, so
        #    a gap there is a FALSE POSITIVE, which is the one thing this section
        #    must never emit. A missing row could not say that.
        return (not gaps), ("no gap, as expected" if not gaps else
                            "reported a gap: missing %s" % gaps[0]["missing"])
    if not gaps:
        return False, "no %s for family %r" % (field, e["family"])
    g = gaps[0]
    missing = set(g["missing"])
    want = set(e.get("missing", []))
    if want and not want <= missing:
        return False, "missing=%s, expected to include %s" % (sorted(missing), sorted(want))
    return True, "%d/%d missing %s" % (g["documented"] if field == "doc_gaps" else g["exported"],
                                       g["defined"], sorted(missing))


def _check_naming(report: dict, e: dict):
    """Suffix families, for projects where inheritance is NOT the convention.

    `attrs` names 9 of 9 exceptions `*Error` but gives them no shared local
    base -- each subclasses whichever stdlib exception matches its semantics.
    A kind census structurally cannot answer that repo, so the report has to
    answer it by the other route or not at all.
    """
    for home in report["naming"]:
        for f in home["families"]:
            if f["suffix"] != e["suffix"]:
                continue
            if "home_module" in e and f["home_module"] != e["home_module"]:
                continue
            if "min_count" in e and f["count"] < e["min_count"]:
                continue
            return True, "*%s n=%d @ %s" % (f["suffix"], f["count"], f["home_module"])
    seen = ["*%s@%s" % (f["suffix"], f["home_module"])
            for h in report["naming"] for f in h["families"]][:6]
    return False, "no *%s family matching (found: %s)" % (e["suffix"], ", ".join(seen) or "none")


def _check_partition(report: dict, e: dict):
    p = report.get("partition") or {}
    roots = set(p.get("shadow_roots") or [])
    want = set(e.get("shadow_roots", []))
    if not want <= roots:
        return False, "shadow_roots=%s, expected to include %s" % (sorted(roots), sorted(want))
    return True, "set aside %s" % (sorted(roots) or "nothing")


CHECKS = {
    "kind_family": _check_base,
    "naming_family": _check_naming,
    "shared_constant": _check_constant,
    "registry": _check_registry,
    "export_gap": lambda r, e: _check_gap(r, e, "export_gaps"),
    "doc_gap": lambda r, e: _check_gap(r, e, "doc_gaps"),
    "partition": _check_partition,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["development", "heldout", "all"], default="all")
    ap.add_argument("--repo", help="score only this repo")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--determinism", action="store_true", help="stability check only")
    args = ap.parse_args()

    key = yaml.safe_load(KEY_PATH.read_text())
    manifest = {p["name"]: p for p in load_manifest()}
    rows = [r for r in key["repos"]
            if (args.set in ("all", r["set"])) and (not args.repo or r["name"] == args.repo)]
    if not rows:
        raise SystemExit("no repos matched")

    totals: dict[str, list[int]] = {}
    for entry in rows:
        name = entry["name"]
        proj = manifest.get(name) or entry.get("project")
        if proj is None:
            raise SystemExit(
                f"🔴 {name} is in the key but not in projects.yaml and carries no inline "
                "`project:` block. A repo with no pinned SHA cannot be scored reproducibly."
            )
        path = clone_or_update(proj)
        report = analyse(path)
        if args.determinism:
            print("  %-12s deterministic ✅  (sha %s)" % (name, proj["sha"]))
            continue

        passed = 0
        print("\n=== %s  (%s, sha %s) ===" % (name, entry["set"], proj["sha"]))
        for q in entry["questions"]:
            check = CHECKS[q["kind"]]
            ok, found = check(report, q)
            passed += ok
            mark = "PASS" if ok else "FAIL"
            print("  [%s] %-14s %s" % (mark, q["id"], found))
            if args.verbose and not ok:
                print("         expected: %s" % {k: v for k, v in q.items() if k not in ("id", "kind")})
        totals.setdefault(entry["set"], [0, 0])
        totals[entry["set"]][0] += passed
        totals[entry["set"]][1] += len(entry["questions"])
        print("  -> %d/%d" % (passed, len(entry["questions"])))

    if args.determinism:
        return 0

    print("\n" + "=" * 66)
    for s in ("development", "heldout"):
        if s not in totals:
            continue
        got, tot = totals[s]
        print("  %-12s %d/%d  (%.0f%%)" % (s, got, tot, 100 * got / tot))
    if "development" in totals and "heldout" in totals:
        d = totals["development"][0] / totals["development"][1]
        h = totals["heldout"][0] / totals["heldout"][1]
        print("\n  🔴 ONLY THE HELD-OUT NUMBER IS EVIDENCE. The development set was used to")
        print("     build these heuristics, so its score is a fit.")
        if h < d - 0.25:
            print("  🔴 HELD-OUT IS %.0f POINTS BELOW DEVELOPMENT: the heuristics are fitted to"
                  % (100 * (d - h)))
            print("     the four repos they were written against. Treat #410's table accordingly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
