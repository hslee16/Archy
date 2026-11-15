"""Strict labels, the pre-registered verdict and the arm tables for #587.

Reads the two labelers' ledgers and `key.json` from the packets directory, turns them into
strict truth (both TRUE), and reports: the primary's B precision, A's precision, the
headline only-by-B share (exact tier excluded) and the share with it, the verdict, the leak
check (precision split by byte-identical bodies), Cohen's kappa, drops by reason and
generator, and each sensitivity arm's precision over its arm-only pairs. Arm results are
labeled `sensitivity` and never enter the primary numbers.

archy:owns        cohen_kappa, load_labels, main, report
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from embedding_oracle_pairs import Pair  # ty: ignore[unresolved-import]  (bench/ is sys.path[0])
from embedding_oracle_pool import ARMS, read_pairs  # ty: ignore[unresolved-import]
from embedding_oracle_score import b_precision, verdict  # ty: ignore[unresolved-import]
from embedding_oracle_share import resolve_labels, share  # ty: ignore[unresolved-import]

ROOT = Path(__file__).resolve().parent.parent / "bench" / "cache" / "emb587"
LABELERS = ("sonnet", "opus")


def load_labels(path: Path) -> dict[str, str]:
    """`{packet id: label}` from the `ok` rows of one labeler's ledger."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("status") == "ok":
            out[row["id"]] = row["label"]
    return out


def cohen_kappa(a: Mapping[str, str], b: Mapping[str, str]) -> float | None:
    """Cohen's kappa over the ids both labelers called TRUE or FALSE; None if undefined."""
    ids = [i for i in a if i in b and "UNSCORABLE" not in (a[i], b[i])]
    n = len(ids)
    if not n:
        return None
    observed = sum(a[i] == b[i] for i in ids) / n
    expected = sum(
        (sum(a[i] == c for i in ids) / n) * (sum(b[i] == c for i in ids) / n)
        for c in ("TRUE", "FALSE")
    )
    return None if expected == 1 else (observed - expected) / (1 - expected)


def _rate(k: int, n: int) -> float | None:
    return k / n if n else None


def report(
    key: Mapping[str, Mapping[str, object]],
    labels: Mapping[str, Mapping[str, str]],
    pairs_by_set: Mapping[str, Sequence[Pair]],
    identical: Mapping[str, bool],
) -> dict[str, object]:
    """Every pre-registered number from the labels. `pairs_by_set` has `primary` and arms."""
    a = {k: v for k, v in labels["sonnet"].items() if k in labels["opus"]}
    b = {k: labels["opus"][k] for k in a}
    truth = {key[k]["pair_id"]: t for k, t in resolve_labels(a, b).items()}
    return {
        "labelers": list(LABELERS),
        "packets_labeled_by_both": len(a),
        "kappa": cohen_kappa(a, b),
        "unscorable_by_either": sum("UNSCORABLE" in (a[k], b[k]) for k in a),
        "primary": _primary(pairs_by_set["primary"], truth, identical),
        "arms_sensitivity": {
            m: b_precision(pairs_by_set[f"arm:{m}"], truth).model_dump() for m in ARMS
        },
    }


def _primary(
    primary: Sequence[Pair], truth: Mapping[str, bool], identical: Mapping[str, bool]
) -> dict[str, object]:
    headline = share(primary, truth)
    # `share` drops exact-tier pairs by predicate; unequal shapes make every pair count.
    unfiltered = [p.model_copy(update={"new_shape": "n", "existing_shape": "e"}) for p in primary]
    prec = b_precision(primary, truth)
    scored = [p for p in primary if p.pair_id in truth]
    a_pairs = [p for p in scored if "A" in p.generators]
    non_exact = [p for p in primary if not p.exact_tier]
    return {
        "pairs": len(primary),
        "scored": len(scored),
        "b_precision": prec.model_dump(),
        "b_precision_without_exact": b_precision(non_exact, truth).model_dump(),
        "a_precision": _rate(sum(truth[p.pair_id] for p in a_pairs), len(a_pairs)),
        "a_scored": len(a_pairs),
        "share_headline": headline.model_dump(),
        "share_with_exact": share(unfiltered, truth).model_dump(),
        "verdict": verdict(prec, headline).model_dump(),
        "leak_check": _leak(scored, truth, identical),
    }


def _leak(
    scored: Sequence[Pair], truth: Mapping[str, bool], identical: Mapping[str, bool]
) -> dict[str, object]:
    cells: dict[str, Counter[str]] = {}
    for p in scored:
        for g in p.generators:
            c = cells.setdefault(
                f"{g}:{'identical' if identical.get(p.pair_id) else 'different'}", Counter()
            )
            c["n"] += 1
            c["true"] += truth[p.pair_id]
    return {
        k: {"n": c["n"], "true": c["true"], "precision": _rate(c["true"], c["n"])}
        for k, c in sorted(cells.items())
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dir", type=Path, default=ROOT / "labels")
    args = parser.parse_args(argv)
    key = json.loads((args.dir / "key.json").read_text(encoding="utf-8"))
    labels = {n: load_labels(args.dir / f"labels_{n}.jsonl") for n in LABELERS}
    pairs_by_set = {"primary": read_pairs(ROOT / "label_pool.evaluation.jsonl")}
    for m in ARMS:
        pairs_by_set[f"arm:{m}"] = read_pairs(ROOT / f"label_arm.{m}.evaluation.jsonl")
    identical = {}
    for k, v in key.items():
        f = args.dir / "packets" / f"{k}.json"
        if f.exists():
            pk = json.loads(f.read_text(encoding="utf-8"))
            identical[v["pair_id"]] = pk["new"][0]["source"] == pk["existing"][0]["source"]
    out = report(key, labels, pairs_by_set, identical)
    (args.dir / "report.json").write_text(
        json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
