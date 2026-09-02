"""Precision pilot for #429: does "you changed X and did not update its mirrors" fire accurately?

Pre-registration, written before this file existed and not edited since:
[`docs/research/MIRROR_SURFACE_PRECISION.md`](../docs/research/MIRROR_SURFACE_PRECISION.md).
Threshold is precision >= 0.70 over at least 20 firings, and an underpowered
pass is explicitly not a pass.

Runs over archy's own git history at zero agent cost. Nothing here touches
`src/`: the point is to find out whether the detector is worth building before
building it, because #369 is the cautionary case for the other order.

    uv run python bench/mirror_precision.py --json bench/mirror_precision.json

archy:owns        main
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from collections import defaultdict
from pathlib import Path

# Only archy's own package. Tests and bench are not surfaces a change mirrors
# into, and including them would inflate the firing count with noise the
# proposed `check` line would never print.
SRC_PREFIX = "src/archy/"

# The oracle's window, fixed by the pre-registration. A later commit inside it
# that touches the named module and mentions the symbol makes the firing a true
# positive: somebody had to go back and do what the original commit skipped.
ORACLE_WINDOW = 5


def _run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=False).stdout


def _commits() -> list[str]:
    """Oldest first, so a commit's oracle window is the commits after it."""
    out = _run(["git", "rev-list", "--no-merges", "--reverse", "HEAD"])
    return [line for line in out.splitlines() if line.strip()]


def _changed_files(sha: str) -> set[str]:
    out = _run(["git", "show", "--format=", "--name-only", sha])
    return {line.strip() for line in out.splitlines() if line.strip()}


def _file_at(sha: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"{sha}:{path}"], capture_output=True, text=True, check=False
    )
    return proc.stdout if proc.returncode == 0 else None


def _top_level_symbols(source: str) -> dict[str, str]:
    """Map top-level function/class name -> its source text.

    Source text rather than a hash of the AST, because the question is whether
    the commit changed the symbol at all, and a comment-only edit still counts
    as the author having been in there.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    lines = source.splitlines()
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            end = getattr(node, "end_lineno", node.lineno)
            out[node.name] = "\n".join(lines[node.lineno - 1 : end])
    return out


def _module_name(path: str) -> str:
    """`src/archy/layers.py` -> `archy.layers`.

    The package prefix is load-bearing and was missing at first: without it the
    name came back `layers`, no `ImportFrom archy.layers` ever matched, and the
    detector fired zero times on every input. A rule set that cannot fire looks
    exactly like a clean codebase, which is why the canary below exists.
    """
    rel = path[len(SRC_PREFIX) :].removesuffix(".py").replace("/", ".")
    rel = rel.removesuffix(".__init__")
    return "archy" if rel == "__init__" else f"archy.{rel}"


def _changed_symbols(sha: str, path: str) -> set[str]:
    """Top-level symbols whose source text differs between this commit and its parent."""
    after = _file_at(sha, path)
    if after is None:
        return set()
    before = _file_at(f"{sha}~1", path)
    if before is None:
        # A new file has no mirrors to have failed to update.
        return set()
    a, b = _top_level_symbols(after), _top_level_symbols(before)
    return {name for name, text in a.items() if name in b and b[name] != text}


def _references(source: str, defining_mod: str, symbol: str) -> bool:
    """Does this module actually reference `defining_mod.symbol`?

    🔴 AST, NOT SUBSTRINGS, AND THE SMOKE RUN IS WHY. The first version asked
    `symbol in source` plus a loose import test, and reported 26 firings at
    precision 0.038 with 15 on one commit. Every one was vocabulary collision:
    the CLI command functions are named `check`, `diff`, `snapshot`, so any
    module containing the word "check" scored as a caller of `cli.check`. That
    is a measurement of English, not of the call graph, and reporting it would
    have killed the feature on a harness bug.

    A caller must either import the name from the defining module and use it as
    a bare Name, or reach it as an attribute on the imported module.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    imported_bare = False
    module_aliases: set[str] = set()
    tail = defining_mod.rsplit(".", 1)[-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == defining_mod:
                for alias in node.names:
                    if alias.name == symbol:
                        imported_bare = True
                    # `from archy import layers` binds the module itself.
                    elif alias.name == tail:
                        module_aliases.add(alias.asname or alias.name)
            elif node.module == defining_mod.rsplit(".", 1)[0]:
                for alias in node.names:
                    if alias.name == tail:
                        module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == defining_mod:
                    module_aliases.add(alias.asname or alias.name.split(".")[0])

    if not imported_bare and not module_aliases:
        return False

    for node in ast.walk(tree):
        if imported_bare and isinstance(node, ast.Name) and node.id == symbol:
            return True
        if (
            isinstance(node, ast.Attribute)
            and node.attr == symbol
            and isinstance(node.value, ast.Name)
            and node.value.id in module_aliases
        ):
            return True
    return False


def _caller_modules(sha: str, tracked: list[str], defining: str, symbol: str) -> set[str]:
    """Modules that actually reference the changed symbol.

    This is the derivation #429 asks for, done by hand here because archy's
    graph is module-granular: it carries call counts on an edge, not which
    symbol was called. If the pilot passes, resolving this inside archy is part
    of the work; if it fails, that work was never worth doing.
    """
    out: set[str] = set()
    defining_mod = _module_name(defining)
    for path in tracked:
        if path == defining:
            continue
        source = _file_at(sha, path)
        # The substring test is only a cheap prefilter now; `_references`
        # decides.
        if source is None or symbol not in source:
            continue
        if _references(source, defining_mod, symbol):
            out.add(path)
    return out


def _oracle(later: list[str], module_path: str, symbol: str) -> tuple[bool, str | None]:
    """True positive iff a commit in the window touches the module and mentions the symbol."""
    for sha in later:
        if module_path not in _changed_files(sha):
            continue
        patch = _run(["git", "show", "--format=", "-U0", sha, "--", module_path])
        for line in patch.splitlines():
            if line.startswith(("+", "-")) and symbol in line:
                return True, sha
    return False, None


# Symbols in this tree that are known to be referenced from BOTH surfaces. If
# the resolver stops seeing them it has silently stopped working, and a
# detector that cannot fire reports a clean history indistinguishable from a
# real null. Both of this harness's bugs were caught here rather than in a
# result: substring matching that fired on the word "check", and a module name
# missing its package prefix so no import ever matched.
_CANARY = (
    ("find_violations", "src/archy/layers.py", ("archy/cli.py", "archy/mcp.py")),
    ("compute_coverage", "src/archy/layers.py", ("archy/cli.py", "archy/mcp.py")),
)


def _canary(tracked: list[str]) -> bool:
    ok = True
    for symbol, defining, expected in _CANARY:
        callers = _caller_modules("HEAD", tracked, defining, symbol)
        hit = all(any(c.endswith(e) for c in callers) for e in expected)
        print(f"# canary {'PASS' if hit else 'FAIL'}: {symbol} -> {sorted(callers)}")
        ok = ok and hit
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="Write the full result here.")
    parser.add_argument("--limit", type=int, default=0, help="Only the last N commits (smoke).")
    parser.add_argument(
        "--canary", action="store_true", help="Only check that the resolver still works."
    )
    args = parser.parse_args()

    head_tracked = [
        p
        for p in _run(["git", "ls-tree", "-r", "--name-only", "HEAD"]).splitlines()
        if p.startswith(SRC_PREFIX) and p.endswith(".py")
    ]
    if not _canary(head_tracked):
        print("# resolver is broken; any number it produces is a fact about the harness")
        return 1
    if args.canary:
        return 0

    commits = _commits()
    if args.limit:
        commits = commits[-args.limit :]
    print(f"# scanning {len(commits)} non-merge commits")

    firings: list[dict] = []
    for i, sha in enumerate(commits):
        changed = _changed_files(sha)
        touched_src = sorted(p for p in changed if p.startswith(SRC_PREFIX) and p.endswith(".py"))
        if not touched_src:
            continue
        tracked = [
            p
            for p in _run(["git", "ls-tree", "-r", "--name-only", sha]).splitlines()
            if p.startswith(SRC_PREFIX) and p.endswith(".py")
        ]
        later = commits[i + 1 : i + 1 + ORACLE_WINDOW]

        for path in touched_src:
            for symbol in sorted(_changed_symbols(sha, path)):
                callers = _caller_modules(sha, tracked, path, symbol)
                if len(callers) < 2:
                    # No mirror relation to be missing.
                    continue
                untouched = sorted(callers - changed)
                if not untouched or not (callers & changed):
                    # The asymmetry IS the signal: the commit has to have
                    # updated one caller and skipped another.
                    continue
                for module_path in untouched:
                    hit, by = _oracle(later, module_path, symbol)
                    firings.append(
                        {
                            "commit": sha[:12],
                            "producer": path,
                            "symbol": symbol,
                            "unmirrored": module_path,
                            "updated": sorted(callers & changed),
                            "true_positive": hit,
                            "corrected_by": by[:12] if by else None,
                        }
                    )

    tp = sum(1 for f in firings if f["true_positive"])
    n = len(firings)
    precision = tp / n if n else 0.0
    per_commit = defaultdict(int)
    for f in firings:
        per_commit[f["commit"]] += 1

    print(f"\n# firings: {n}  true positives: {tp}  precision: {precision:.3f}")
    print(f"# distinct commits that fired: {len(per_commit)}")
    if per_commit:
        worst = max(per_commit.values())
        print(f"# most firings on a single commit: {worst}")

    # The pre-registered gate, read back rather than re-decided.
    verdict = (
        "UNDERPOWERED (no verdict; not a pass)"
        if n < 20
        else ("PASS" if precision >= 0.70 else "FAIL (does not ship)")
    )
    print(f"# threshold: precision >= 0.70 over n >= 20  ->  {verdict}")

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "firings": n,
                    "true_positives": tp,
                    "precision": round(precision, 4),
                    "distinct_commits": len(per_commit),
                    "verdict": verdict,
                    "detail": firings,
                },
                indent=2,
            )
            + "\n"
        )
        print(f"# wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
