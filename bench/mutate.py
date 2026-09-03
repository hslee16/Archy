"""Mutation-test archy: which changes to `src/` does NO test notice?

A surviving mutation is a place the suite cannot fail. That is the EMPIRICAL
version of the question a reading pass can only guess at: not "does this test
look weak" but "is there any test at all that this break would trip".

🔴 IT EXISTS BECAUSE READING WAS NOT ENOUGH, IN BOTH DIRECTIONS. A suite-wide
audit by inspection produced roughly twenty candidates. Running them found two
real gaps nobody had noticed (#438) and DISPROVED two confident claims: the
scan reported `DEFAULT_MAX_MODULES = 1` and an edge-dropping `graph_to_dict` as
green repo-wide, and they fail 170 and 4 tests respectively. Half the value here
is refuting findings, not producing them.

Two-stage for speed: run the mutated module's own test file first, and only if
that passes run the whole suite to confirm nothing else catches it.

    uv run python bench/mutate.py 60          # sample 60 mutations
    uv run python bench/mutate.py 60 7        # ... with seed 7

Not wired into CI. Each surviving mutation costs a full suite run, so this is a
tool to reach for when auditing a test file or before trusting a gate, not a
gate itself. A kill-rate threshold would need pre-registering rather than
fitting to whatever today's number happens to be.

archy:owns        candidates, main, run
"""

from __future__ import annotations

import ast
import io
import random
import re
import subprocess
import sys
import tokenize
from pathlib import Path

SRC = Path("src/archy")
TESTS = Path("tests")

# Ordered: the first pattern that matches a line is the one applied. Each is a
# behaviour change no correct implementation should be indifferent to.
MUTATIONS: list[tuple[str, str, str]] = [
    (r"(?<![=!<>])== ", "!= ", "equality flipped"),
    (r" != ", " == ", "inequality flipped"),
    (r" >= ", " > ", "boundary loosened"),
    (r" <= ", " < ", "boundary loosened"),
    (r" and ", " or ", "and -> or"),
    (r"\bnot in\b", "in", "membership flipped"),
    (r"\bif not ", "if ", "guard removed"),
    (r"\breturn True\b", "return False", "return True -> False"),
    (r"\breturn False\b", "return True", "return False -> True"),
]

SKIP = re.compile(r"^\s*(#|from |import )")


def _prose_lines(source: str) -> set[int]:
    """0-based lines occupied by string literals or comments.

    Mutating an `and` inside a docstring changes nothing and no test can fail,
    so every such line reports as a survivor. The first run of this harness
    produced exactly one "survivor" and it was a sentence of English prose.
    """
    covered: set[int] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return covered
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            covered.update(range(node.lineno - 1, end))
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                covered.update(range(tok.start[0] - 1, tok.end[0]))
    except (tokenize.TokenError, IndentationError):
        pass
    return covered


def candidates() -> list[tuple[Path, int, str, str, str]]:
    out = []
    for path in sorted(SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        prose = _prose_lines(source)
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if i in prose or SKIP.search(line) or not line.strip():
                continue
            for pat, repl, label in MUTATIONS:
                if re.search(pat, line):
                    out.append((path, i, pat, repl, label))
                    break
    return out


def run(cmd: list[str], timeout: int) -> bool:
    """True when the command passes."""
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    picks = candidates()
    random.Random(seed).shuffle(picks)
    picks = picks[:n]
    print(f"# {len(picks)} mutations sampled from {len(candidates())} candidate lines\n")

    survivors = []
    for k, (path, i, pat, repl, label) in enumerate(picks, 1):
        original = path.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)
        lines[i] = re.sub(pat, repl, lines[i], count=1)
        path.write_text("".join(lines), encoding="utf-8")
        try:
            own = TESTS / f"test_{path.stem}.py"
            targeted = ["uv", "run", "pytest", "-x", "-q", str(own)] if own.exists() else None
            caught = False
            if (targeted and not run(targeted, 180)) or not run(
                ["uv", "run", "pytest", "-x", "-q"], 900
            ):
                caught = True
            if not caught:
                survivors.append((path, i + 1, label, lines[i].strip()[:88]))
                print(f"SURVIVED  {path}:{i + 1}  [{label}]\n          {lines[i].strip()[:88]}")
        finally:
            path.write_text(original, encoding="utf-8")
        if k % 10 == 0:
            print(f"# ... {k}/{len(picks)} done, {len(survivors)} survivors so far", flush=True)

    print(f"\n# {len(survivors)} of {len(picks)} mutations survived (no test failed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
