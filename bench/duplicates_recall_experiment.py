"""Duplicate-detection recall by clone type, via synthetic clone injection.

`archy duplicates`' precision has an FP gate (`bench/duplicates_sweep.py` +
§12b-§12f); this measures the other axis: **recall** - of the duplicates that
exist, how many does the shape-hash detector find? There is no labelled
duplicate corpus in the bench, so ground truth is *constructed*: take real
functions, mutate each into known clone types, and measure whether the detector
recovers the clone. Recovery = the clone shares the seed's `shape_hash` above
the `--min-nodes` floor, which is exactly what `compute_duplicates` clusters on
(so this measures the detector's true clustering behaviour, not a proxy).

Clone taxonomy (Bellon et al. / BigCloneBench):
* **Type-1** - byte-identical copy.
* **Type-2** - identifiers renamed and literals changed (parameterized).
* **Type-3** - a *gapped* clone: statements inserted/deleted/reordered, or an
  operator changed. The plurality of real clones (~52% mean on BigCloneBench).

The shape-hash folds identifiers/literals to placeholders, so Type-1/2 recover
fully; Type-3 changes the AST node stream, so the exact hash cannot match. This
experiment measures both the exact shape-hash and the #246 token-overlap
primitive (`compute_near_duplicates`), so the Type-3 rows show the exact ~0% next
to the token-overlap lift. Writeup: RESEARCH_METRICS §12g (the gap) / §12h (the
near-miss primitive + its FP gate).

Usage:
    uv run --with pyyaml python bench/duplicates_recall_experiment.py
    uv run --with pyyaml python bench/duplicates_recall_experiment.py --stdout
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

from _common import REPO_ROOT, clone_or_update, load_manifest

from archy.complexity import compute_function_complexity, extract_token_bags
from archy.duplicates import DEFAULT_MIN_SIMILARITY

RESULTS = REPO_ROOT / "bench" / "duplicates_recall_results.md"
MIN_SIZE = 30  # the shipping --min-nodes default
SEED_MIN_SIZE = 35  # margin above the floor so a mutation cannot fall under it
SEED_TARGET = 120
SEED_REPOS = ("django", "fastapi")  # diverse real source for the seed functions

TYPES = (
    "t1_identical",
    "t2_renamed",
    "t3_insert_1stmt",
    "t3_delete_1stmt",
    "t3_reorder_2stmt",
    "t3_flip_operator",
)


def top_shape(src: str) -> tuple[str, int] | None:
    """(shape_hash, size) of the first top-level def in `src`, or None."""
    try:
        rows = compute_function_complexity(src.encode())
    except Exception:
        return None
    top = [r for r in rows if "." not in r.qualified_name]
    if not top or not top[0].shape_hash:
        return None
    return top[0].shape_hash, top[0].size


def top_bag(src: str) -> Counter[str] | None:
    """Normalized-token multiset of the first top-level def (the #246 primitive)."""
    try:
        bags = extract_token_bags(src.encode())
    except Exception:
        return None
    return bags.get(1)  # the def is at line 1 after ast.unparse normalization


def _jaccard(a: Counter[str], b: Counter[str]) -> float:
    union = sum((a | b).values())
    return sum((a & b).values()) / union if union else 0.0


class _Rename(ast.NodeTransformer):
    """Type-2 mutation: rename every identifier and perturb every literal.

    Both are shape-hash-normalized, so a correct detector must still cluster the
    result with the seed - this is precisely the Type-2 recall test.
    """

    def __init__(self) -> None:
        self._n = 0
        self._names: dict[str, str] = {}

    def _fresh(self, name: str) -> str:
        if name not in self._names:
            self._names[name] = f"v{self._n}"
            self._n += 1
        return self._names[name]

    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = self._fresh(node.id)
        return node

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.arg = self._fresh(node.arg)
        node.annotation = None
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        if isinstance(node.value, bool):
            return node
        if isinstance(node.value, int):
            node.value += 7
        elif isinstance(node.value, str):
            node.value += "_x"
        return node


def _fn(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef:
    fn = tree.body[0]
    assert isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
    return fn


def mutate(src: str) -> dict[str, str | None]:
    """Clone variants of one seed def; None where a mutation does not apply."""
    out: dict[str, str | None] = {}
    body = _fn(ast.parse(src)).body

    t1 = ast.parse(src)
    _fn(t1).name = "clone_fn"
    out["t1_identical"] = ast.unparse(t1)

    t2 = _Rename().visit(ast.parse(src))
    ast.fix_missing_locations(t2)
    _fn(t2).name = "clone_fn"
    out["t2_renamed"] = ast.unparse(t2)

    t3i = ast.parse(src)
    _fn(t3i).body.insert(0, ast.parse("_gap = 0").body[0])
    ast.fix_missing_locations(t3i)
    out["t3_insert_1stmt"] = ast.unparse(t3i)

    if len(body) >= 2:
        t3d = ast.parse(src)
        del _fn(t3d).body[-1]
        out["t3_delete_1stmt"] = ast.unparse(t3d)
        t3r = ast.parse(src)
        b = _fn(t3r).body
        b[0], b[1] = b[1], b[0]
        out["t3_reorder_2stmt"] = ast.unparse(t3r)
    else:
        out["t3_delete_1stmt"] = None
        out["t3_reorder_2stmt"] = None

    out["t3_flip_operator"] = _flip_operator(src)
    return out


def _flip_operator(src: str) -> str | None:
    flipped = {"done": False}

    class _Op(ast.NodeTransformer):
        def visit_BinOp(self, node: ast.BinOp) -> ast.BinOp:
            self.generic_visit(node)
            if flipped["done"]:
                return node
            if isinstance(node.op, ast.Add):
                node.op = ast.Sub()
                flipped["done"] = True
            elif isinstance(node.op, ast.Sub):
                node.op = ast.Add()
                flipped["done"] = True
            return node

    tree = _Op().visit(ast.parse(src))
    ast.fix_missing_locations(tree)
    return ast.unparse(tree) if flipped["done"] else None


def collect_seeds(roots: list[Path]) -> list[str]:
    seeds: list[str] = []
    for root in roots:
        for py in root.rglob("*.py"):
            if len(seeds) >= SEED_TARGET:
                return seeds
            text = py.read_text(encoding="utf-8", errors="ignore")
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in tree.body:  # top-level defs only, for a clean extraction
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                src = ast.get_source_segment(text, node)
                if not src:
                    continue
                try:
                    src = ast.unparse(ast.parse(src))  # normalize so seed == t1 baseline
                except Exception:
                    continue
                sh = top_shape(src)
                if sh and sh[1] >= SEED_MIN_SIZE:
                    seeds.append(src)
                    if len(seeds) >= SEED_TARGET:
                        return seeds
    return seeds


def _seed_roots() -> list[Path]:
    by_name = {p["name"]: p for p in load_manifest()}
    roots: list[Path] = []
    for name in SEED_REPOS:
        proj = by_name.get(name)
        root = clone_or_update(proj) if proj else None
        if root is None:
            continue
        src = root / proj.get("src_dir", ".")
        if src.exists():
            roots.append(src)
    return roots


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()

    seeds = collect_seeds(_seed_roots())
    print(f"# seeds: {len(seeds)} real functions (size >= {SEED_MIN_SIZE})", file=sys.stderr)

    # Two recovery predicates side by side: the exact shape-hash (the old
    # primitive) and the #246 token-overlap (multiset Jaccard >= the shipped
    # similarity floor), so the Type-3 rows show the exact 0% next to the lift.
    recovered = dict.fromkeys(TYPES, 0)
    recovered_tokens = dict.fromkeys(TYPES, 0)
    applicable = dict.fromkeys(TYPES, 0)
    for src in seeds:
        seed = top_shape(src)
        seed_bag = top_bag(src)
        if seed is None or seed_bag is None:
            continue
        for name, clone_src in mutate(src).items():
            if clone_src is None:
                continue
            cs = top_shape(clone_src)
            cb = top_bag(clone_src)
            if cs is None or cb is None or cs[1] < MIN_SIZE:
                continue  # clone unparseable or fell under the floor: not applicable
            applicable[name] += 1
            if cs[0] == seed[0]:
                recovered[name] += 1
            if _jaccard(seed_bag, cb) >= DEFAULT_MIN_SIMILARITY:
                recovered_tokens[name] += 1

    out: list[str] = []
    out.append("# Duplicate-detection recall by clone type (#246)")
    out.append("")
    out.append(
        f"Output of `uv run --with pyyaml python bench/duplicates_recall_experiment.py`. "
        f"Captured {dt.date.today().isoformat()}. {len(seeds)} real seed functions from "
        f"{' + '.join(SEED_REPOS)} (size >= {SEED_MIN_SIZE}), each mutated into known clone "
        f"types. `shape-hash` recovery = clone shares the seed's `shape_hash`; `token-overlap` "
        f"recovery = multiset Jaccard >= {DEFAULT_MIN_SIMILARITY} (the shipped near-miss floor); "
        f"both at min-nodes {MIN_SIZE}."
    )
    out.append("")
    out.append("| clone type | shape-hash recall | token-overlap recall | applicable |")
    out.append("| --- | ---: | ---: | ---: |")
    for name in TYPES:
        a, r, rt = applicable[name], recovered[name], recovered_tokens[name]
        p = f"{100 * r / a:.1f}%" if a else "n/a"
        pt = f"{100 * rt / a:.1f}%" if a else "n/a"
        out.append(f"| `{name}` | {p} | {pt} | {a} |")
    out.append("")
    out.append(
        "The exact shape-hash recovers Type-1/2 fully but ~0% of any Type-3 gap (one "
        "statement inserted/deleted or a single operator flipped moves the hash). The "
        "token-overlap primitive (#246, `compute_near_duplicates`) recovers the Type-3 rows "
        "the hash misses, at the cost of a lower-precision tier (its FP gate is "
        "RESEARCH_METRICS §12h). Overall recall on real code is bounded by the clone-type "
        "mix; Type-3 is the plurality (~52% on BigCloneBench). See RESEARCH_METRICS §12g/§12h."
    )
    report = "\n".join(out) + "\n"
    if args.stdout:
        sys.stdout.write(report)
    else:
        RESULTS.write_text(report)
        print(f"# wrote {RESULTS.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
