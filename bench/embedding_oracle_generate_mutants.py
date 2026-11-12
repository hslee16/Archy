"""Mutation check for the #587 generator side: does any test notice each deliberate break?

Covers the four mutations the pre-registration names (cutoff direction; drop the "exists at base
and head" condition; swap A and B in the pooled sample; key the unit by PR only) and one mutant
per strict-validation check of the vector loader and per new rule of the generator and recount.

Each mutant replaces one exact line of one source file. The helper exits non-zero if a match is
missing or ambiguous (a mutation that did not apply proves nothing), if the unmutated suite is
not green first, or if any mutant SURVIVES. `__pycache__` is cleared before every run and every
source is restored in a `finally`, so an interrupt cannot leave a mutant behind.

    uv run --with numpy python bench/embedding_oracle_generate_mutants.py

archy:owns        main
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
B = "bench/"
TESTS = [
    "tests/test_embedding_oracle_vectors.py",
    "tests/test_embedding_oracle_units.py",
    "tests/test_embedding_oracle_generate.py",
    "tests/test_embedding_oracle_a_target.py",
    "tests/test_embedding_oracle_canary.py",
    "tests/test_embedding_oracle_pairs.py",
    "tests/test_embedding_oracle_score.py",
]

# (file, name, exact old text, new text)
MUTANTS: list[tuple[str, str, str, str]] = [
    # --- the four named in the pre-registration -------------------------------------------
    (
        B + "embedding_oracle_pairs.py",
        "cutoff direction: calibration scans ascending",
        "for s in per_pr}, reverse=True)",
        "for s in per_pr}, reverse=False)",
    ),
    (
        B + "embedding_oracle_generate.py",
        "cutoff direction: emit keeps pairs BELOW it",
        "if s.sim >= cutoff]",
        "if s.sim <= cutoff]",
    ),
    (
        B + "embedding_oracle_generate.py",
        "cutoff direction: volume counts scores below t",
        "sum(s >= t for s in sims)",
        "sum(s <= t for s in sims)",
    ),
    (
        B + "embedding_oracle_units.py",
        "existence dropped: every base function is a neighbour",
        'existing = [r for r in base if r["counterpart"]]',
        "existing = base",
    ),
    (
        B + "embedding_oracle_a_target.py",
        "existence dropped from the A recount",
        "return bool(base and head and m.qualname in base.funcs and m.qualname in head.funcs)",
        "return True",
    ),
    (
        B + "embedding_oracle_generate.py",
        "A and B swapped: B pairs tagged A",
        'generators=("B",),',
        'generators=("A",),',
    ),
    (
        B + "embedding_oracle_pairs.py",
        "A and B swapped: A pairs tagged B",
        'o.qualname)), ("A",))',
        'o.qualname)), ("B",))',
    ),
    (
        B + "embedding_oracle_pairs.py",
        "unit keyed by PR only",
        'return f"{self.pr}|{self.new_qualname}"',
        'return f"{self.pr}"',
    ),
    (
        B + "embedding_oracle_generate_run.py",
        "ledger keyed without the PR",
        'return f"{model}|{shard}|{pr}"',
        'return f"{model}|{shard}"',
    ),
    # --- strict validation of the returned vectors ---------------------------------------
    (
        B + "embedding_oracle_vectors.py",
        "revision pin not checked",
        'man.get("revision") == PINS[model]',
        "True",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "model name not checked",
        'man.get("model") == model,',
        "True,",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "dtype not checked",
        'man.get("dtype") == "float32"',
        "True",
    ),
    (B + "embedding_oracle_vectors.py", "TF32 not checked", 'man.get("tf32") is False', "True"),
    (
        B + "embedding_oracle_vectors.py",
        "determinism setting not checked",
        'man.get("deterministic_algorithms") is True',
        "True",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "pooling not checked",
        'man.get("pooling") == POOLING[model]',
        "True",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "manifest shard hash not compared to SHARDS.json",
        'man.get("shard_sha256") == want["sha256"]',
        "True",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "manifest row count not compared",
        'man.get("rows") == want["rows"]',
        "True",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "second pass equality not required",
        'det.get("full_second_pass_bitwise_equal") is True',
        "True",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "shard file hash not compared",
        '_sha256(shard_file) == meta["shards"][shard]["sha256"]',
        "True",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "matrix hash not compared",
        '_sha256(npy) == man.get("vectors_sha256")',
        "True",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "matrix width not compared",
        'arr.shape[1] == man.get("dim")',
        "True",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "vector count not compared to ids",
        "arr.shape[0] == len(ids)",
        "True",
    ),
    (B + "embedding_oracle_vectors.py", "NaN not refused", "bool(np.isfinite(arr).all())", "True"),
    (
        B + "embedding_oracle_vectors.py",
        "zero vector not refused",
        "bool((norms > 0).all())",
        "True",
    ),
    (B + "embedding_oracle_vectors.py", "configurations mixed", "a.config == b.config", "True"),
    (
        B + "embedding_oracle_vectors.py",
        "evaluation shard nameable",
        "_require(shard in SHARDS,",
        "_require(True,",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "unknown model accepted at load",
        '_require(model in PINS, f"unknown model {model!r}")\n    meta = read_shards_meta(root)',
        "meta = read_shards_meta(root)",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "stale SHARDS.json accepted",
        'meta.get("ok") is True',
        "True",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "missing extractor version accepted",
        '"extractor_version" in meta["shards"].get(name, {})',
        "True",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "ids read with splitlines",
        'read_text(encoding="utf-8").split("\\n") if line]',
        'read_text(encoding="utf-8").splitlines() if line]',
    ),
    (
        B + "embedding_oracle_vectors.py",
        "unsorted ids accepted",
        "if ids != sorted(set(ids)):",
        "if False:",
    ),
    (
        B + "embedding_oracle_vectors.py",
        "config key drops the libraries",
        'libs = tuple(sorted((manifest.get("libs") or {}).items()))',
        "libs = ()",
    ),
    # --- units and generator --------------------------------------------------------------
    (
        B + "embedding_oracle_units.py",
        "units read with splitlines",
        '.split("\\n")  # never',
        ".splitlines()  # never",
    ),
    (
        B + "embedding_oracle_units.py",
        "occurrence dropped from the key",
        "return (self.path, self.qualname, self.occurrence)",
        "return (self.path, self.qualname)",
    ),
    (
        B + "embedding_oracle_units.py",
        "stale index accepted",
        "if missing:\n            raise UnitIndexError",
        "if False:\n            raise UnitIndexError",
    ),
    (
        B + "embedding_oracle_units.py",
        "new = any head row",
        'r["side"] == "head" and r["in_added"] is True',
        'r["side"] == "head"',
    ),
    (
        B + "embedding_oracle_generate.py",
        "a new function may pair with itself",
        "if e.name_key == new.name_key:",
        "if False:",
    ),
    (
        B + "embedding_oracle_generate.py",
        "tie broken by position, not name",
        "min(tied, key=lambda i: existing[i].name_key)",
        "min(tied)",
    ),
    (
        B + "embedding_oracle_generate.py",
        "cap applied BEFORE the cutoff",
        "    passing = [to_pair(s) for ps in prs for s in ps.scored if s.sim >= cutoff]\n"
        "    return cap_pairs(passing)",
        "    capped = cap_pairs([to_pair(s) for ps in prs for s in ps.scored])\n"
        "    return [p for p in capped if p.similarity is not None and p.similarity >= cutoff]",
    ),
    (
        B + "embedding_oracle_generate.py",
        "scored-only denominator ignored",
        'if denominator == "scored-only":',
        "if False:",
    ),
    (
        B + "embedding_oracle_generate.py",
        "unknown denominator accepted",
        "if denominator not in DENOMINATORS:",
        "if False:",
    ),
    (
        B + "embedding_oracle_generate.py",
        "target-reached flag always true",
        "mean >= target, mean)",
        "True, mean)",
    ),
    (
        B + "embedding_oracle_generate_run.py",
        "cutoff applied across configurations",
        'if json.loads(json.dumps(vs.config)) != cut["config"]:',
        "if False:",
    ),
    (
        B + "embedding_oracle_generate_run.py",
        "incomplete ledger accepted",
        "return None if any(r is None for r in rows.values()) else rows",
        "return rows",
    ),
    (
        B + "embedding_oracle_generate_run.py",
        "extractor version mismatch accepted",
        'if _meta()["shards"][shard]["extractor_version"] != cut["extractor_version"]:',
        "if False:",
    ),
    (
        B + "embedding_oracle_generate_run.py",
        "a crash ends the loop",
        "            errors += 1\n",
        "            errors += 1\n            raise",
    ),
    (
        B + "embedding_oracle_generate_run.py",
        "finished PRs re-scored",
        "        if ledger.is_done(keyfn(pr)):\n            continue\n",
        "",
    ),
    (
        B + "embedding_oracle_generate_run.py",
        "empty PRs not distinguished from unmeasurable",
        "if why == NO_NEW_FUNCTION)",
        "if why != NO_NEW_FUNCTION)",
    ),
    # --- the A recount --------------------------------------------------------------------
    (
        B + "embedding_oracle_a_target.py",
        "cap hit never triggers a re-parse",
        'return reparse == "capped" and new_members(row) > K',
        'return reparse == "capped" and new_members(row) > K * 100',
    ),
    (
        B + "embedding_oracle_a_target.py",
        "pairs from two firings not pooled",
        "pooled.update({p.pair_id: p for p in pairs})",
        'pooled.update({f"{len(pooled)}|{p.pair_id}": p for p in pairs})',
    ),
    (
        B + "embedding_oracle_a_target.py",
        "partner chosen by lowest overlap",
        "overlap=lambda a, b: _jaccard(bag(a), bag(b))",
        "overlap=lambda a, b: -_jaccard(bag(a), bag(b))",
    ),
    (
        B + "embedding_oracle_a_target.py",
        "provisional count uncapped",
        "return min(K, new_members(row))",
        "return new_members(row)",
    ),
    (
        B + "embedding_oracle_a_target.py",
        "ledger read with splitlines",
        '.read_text(encoding="utf-8").split("\\n"):',
        '.read_text(encoding="utf-8").splitlines():',
    ),
]


def _suite_passes() -> bool:
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    run = subprocess.run(
        ["uv", "run", "--with", "numpy", "pytest", *TESTS, "-x", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return run.returncode == 0


def _check_matches(sources: dict[str, str]) -> int:
    for rel, name, old, _new in MUTANTS:
        if sources[rel].count(old) != 1:
            print(f"NO MATCH ({sources[rel].count(old)}x): {name}: {old!r}")
            return 1
    return 0


def main() -> int:
    sources = {rel: (ROOT / rel).read_text() for rel in {m[0] for m in MUTANTS}}
    if _check_matches(sources):
        return 1
    if not _suite_passes():
        print("the unmutated suite is not green; nothing to prove")
        return 2
    survivors: list[str] = []
    try:
        for rel, name, old, new in MUTANTS:
            (ROOT / rel).write_text(sources[rel].replace(old, new))
            killed = not _suite_passes()
            (ROOT / rel).write_text(sources[rel])
            print(("killed   " if killed else "SURVIVED ") + name)
            if not killed:
                survivors.append(name)
    finally:
        for rel, text in sources.items():
            (ROOT / rel).write_text(text)
    print(f"{len(MUTANTS) - len(survivors)} of {len(MUTANTS)} killed")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
