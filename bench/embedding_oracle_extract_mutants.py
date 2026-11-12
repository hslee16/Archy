"""Mutation check for the #587 extractor: does any test notice each deliberate break?

Each mutant replaces one exact fragment of `bench/embedding_oracle_extract.py`. The helper
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
    ("floor made exclusive", "f.nodes >= MIN_NODES", "f.nodes > MIN_NODES"),
    ("floor dropped", "bool(f.shape) and f.nodes >= MIN_NODES", "bool(f.shape)"),
    (
        "source-only filter off",
        "if _path_excluded(rel) or not result.functions:",
        "if not result.functions:",
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
    ("mismatch ignored by main", "if _write_manifest(shards):", "if True:"),
    ("manifest ok ignores mismatches", "ok = not any(", "ok = True or not any("),
    ("pilot drop reason not compared", 'if v["ledger"] != v["mine"]', "if False"),
    ("untracked files count as dirty", '"--untracked-files=no",', '"--untracked-files=all",'),
    ("no-new-function PR kept", "if not any(f.in_added for f in head_fns):", "if False:"),
    # --- review fixes
    (
        "lines split like str.splitlines",
        'lines = source.split(b"\\n")',
        'lines = [x.encode() for x in source.decode("utf-8", "replace").splitlines()]',
    ),
    ("occurrence never counted", "occurrence = seen[(rel, f.qualname)]", "occurrence = 0"),
    (
        "counterpart always false",
        '"counterpart": (f.path, f.qualname) in other',
        '"counterpart": False',
    ),
    (
        "counterpart ignores small functions",
        "names.update((rel, f.qualname) for f in functions)",
        "names.update((rel, f.qualname) for f in functions if _clears_floor(f))",
    ),
    ("transient drops are cached", "if is_final(result):", "if True:"),
    ("stale cache accepted", 'entry.get("version") == EXTRACT_VERSION', "True"),
    ("torn cache crashes", "except (OSError, ValueError):", "except OSError:"),
    (
        "one PR's failure ends the run",
        'return extract_pr(target, row["base"], row["head"])\n    except Exception as exc:',
        'return extract_pr(target, row["base"], row["head"])\n    except ZeroDivisionError as exc:',
    ),
    ("parity ignores newness", 'bool(m["new"]))', "False)"),
    (
        "parity compares nothing",
        'members = [m for firing in row.get("firings", []) for m in firing["members"]]',
        "members = []",
    ),
    ("write is not atomic", "os.replace(tmp, path)", "path.write_bytes(data)"),
    (
        "shard rows not ASCII",
        'json.dumps({"id": k, "text": texts[k]})',
        'json.dumps({"id": k, "text": texts[k]}, ensure_ascii=False)',
    ),
    ("no resume message", "run the same command again to resume", "interrupted"),
    (
        "manifest drops foreign keys",
        'json.dumps({**prior, "ok": ok, "shards": merged}',
        'json.dumps({"ok": ok, "shards": merged}',
    ),
    (
        "legacy sha kept",
        'prior.pop("extractor_git_sha", None)',
        "pass",
    ),
]


def _suite_exit_code() -> int:
    """pytest's exit code: 0 all passed, 1 some test FAILED, anything else is not a verdict
    (2 interrupted or a collection error, 3 internal error, 4 usage, 5 nothing collected)."""
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    run = subprocess.run(
        ["uv", "run", "pytest", TEST, "-x", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return run.returncode


def main() -> int:
    original = TARGET.read_text()
    if _suite_exit_code() != 0:
        print("the unmutated suite is not green; nothing to prove")
        return 2
    survivors: list[str] = []
    try:
        for name, old, new in MUTANTS:
            if original.count(old) != 1:
                print(f"NO MATCH ({original.count(old)}x): {name}: {old!r}")
                return 1
            TARGET.write_text(original.replace(old, new))
            code = _suite_exit_code()
            if code not in (0, 1):
                print(
                    f"BROKEN   {name}: pytest exited {code}, which is neither killed nor survived"
                )
                return 1
            print(("killed   " if code == 1 else "SURVIVED ") + name)
            if code == 0:
                survivors.append(name)
    finally:
        TARGET.write_text(original)
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
