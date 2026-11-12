"""Mutation check for the #587 extractor: does any test notice each deliberate break?

Each mutant replaces one exact line of `bench/embedding_oracle_extract.py`. The helper
exits non-zero if a match is missing or ambiguous (a mutation that did not apply proves
nothing), if the unmutated suite is not green first, or if any mutant SURVIVES (the test
file still passes). `__pycache__` is cleared before every run and the source is restored
in a `finally`, so an interrupt cannot leave a mutant behind.

    uv run python bench/embedding_oracle_extract_mutants.py

archy:owns        main
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "bench" / "embedding_oracle_extract.py"
TEST = "tests/test_embedding_oracle_extract.py"

MUTANTS: list[tuple[str, str, str]] = [
    ("floor made exclusive", "f.size >= MIN_NODES", "f.size > MIN_NODES"),
    ("floor dropped", "f.shape_hash and f.size >= MIN_NODES", "f.shape_hash"),
    (
        "source-only filter off",
        "if _path_excluded(rel):",
        "if False:",
    ),
    ("CRLF not normalised", 'text.replace("\\r\\n", "\\n").replace("\\r", "\\n")', "text"),
    ("trailing newlines kept", '.rstrip("\\n")', ""),
    (
        "key hashes the raw text",
        "hashlib.sha256(normalise(text).encode",
        "hashlib.sha256(text.encode",
    ),
    (
        "sides swapped",
        'side = "base" if added is None else "head"',
        'side = "head" if added is None else "base"',
    ),
    ("added never marked", "else _in_added(rel, f.line, added)", "else True"),
    ("draw over unsorted keys", "ordered = sorted(repo_by_key)", "ordered = list(repo_by_key)"),
    ("repo cap off by one", "if taken[repo] >= cap:", "if taken[repo] > cap:"),
    ("disjointness not enforced", "    if both:", "    if False:"),
    ("dropped rows counted measured", 'and "dropped" not in row', ""),
    ("shard rows unsorted", "for k in sorted(texts)", "for k in reversed(sorted(texts))"),
    (
        "head text shipped for every function",
        "for f in head_fns if f.in_added}",
        "for f in head_fns}",
    ),
    ("reproduction mismatch ignored", "if clashes:", "if False:"),
    ("pilot drop reason not compared", 'if v["ledger"] != v["mine"]', "if False"),
    ("untracked files count as dirty", '"--untracked-files=no",', '"--untracked-files=all",'),
    ("no-new-function PR kept", "if not any(f.in_added for f in head_fns):", "if False:"),
]


def _suite_passes() -> bool:
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    run = subprocess.run(
        ["uv", "run", "pytest", TEST, "-x", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return run.returncode == 0


def main() -> int:
    original = TARGET.read_text()
    if not _suite_passes():
        print("the unmutated suite is not green; nothing to prove")
        return 2
    survivors: list[str] = []
    try:
        for name, old, new in MUTANTS:
            if original.count(old) != 1:
                print(f"NO MATCH ({original.count(old)}x): {name}: {old!r}")
                return 1
            TARGET.write_text(original.replace(old, new))
            killed = not _suite_passes()
            print(("killed   " if killed else "SURVIVED ") + name)
            if not killed:
                survivors.append(name)
    finally:
        TARGET.write_text(original)
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
