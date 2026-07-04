"""Per-function cyclomatic complexity over Python source.

Tree-sitter walk over (function_definition) nodes. CC = 1 + branch count,
counting each: `if`, `elif`, `for`, `while`, `except`, `case`,
conditional expressions (`a if b else c`), boolean operators (`and`/`or`),
and comprehension `for`/`if` clauses. `assert` is excluded to match
radon's default; `try`/`else`/`finally` add nothing. Descendants of
nested `function_definition` / `class_definition` are skipped so each
function carries only its own branches; nested defs get their own
FunctionComplexity row with a dotted qualified_name (Class.method,
outer.inner).

Module-level branches (top-level `if`/`for` outside any function) are
not counted anywhere - they don't belong to any function, and Python's
module scope doesn't have a CC analogue.
"""

from __future__ import annotations

import hashlib

import tree_sitter_python as tsp
from pydantic import BaseModel, ConfigDict
from tree_sitter import Language, Parser

PY_LANGUAGE = Language(tsp.language())

_BRANCH_NODE_TYPES = frozenset(
    {
        "if_statement",
        "elif_clause",
        "for_statement",
        "while_statement",
        "except_clause",
        "case_clause",
        "conditional_expression",
        "boolean_operator",
        # Comprehension clauses: `[x for y in z if w]` counts the `for` and
        # `if` separately. Multiple `for`s in a nested comprehension each
        # count, matching radon.
        "for_in_clause",
        "if_clause",
    }
)


class FunctionComplexity(BaseModel):
    """A single function (or method, or nested def) and its CC.

    `qualified_name` is dotted via class/function ancestors, e.g.
    `Foo.bar` for a method or `outer.inner` for a nested def. Module
    functions are unqualified.

    `shape_hash` / `size` describe the function *body* as a normalized
    structural fingerprint (see `_analyze_body`): identifiers and literals
    are folded to placeholders, so two functions that differ only by names
    or literal values share a hash. `size` is the count of normalized tokens
    (an AST-node count, layout-invariant). Both power duplicate-function
    detection (`archy.duplicates`); `shape_hash` is `""` for an empty body.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    qualified_name: str
    line: int
    cyclomatic: int
    shape_hash: str = ""
    size: int = 0


def compute_function_complexity(source: bytes) -> tuple[FunctionComplexity, ...]:
    """Extract per-function CC from the given Python source bytes.

    Returns functions in (line, qualified_name) order so callers can rely
    on stable iteration without re-sorting.
    """
    parser = Parser(PY_LANGUAGE)
    tree = parser.parse(source)
    return walk_functions(tree.root_node, source)


def walk_functions(root_node, source: bytes) -> tuple[FunctionComplexity, ...]:
    """Walk a pre-parsed tree-sitter root node and return per-function CC.

    Exists so the caller (parser.parse_source) can share its already-parsed
    tree with import extraction rather than running tree-sitter twice per file.
    """
    out: list[FunctionComplexity] = []
    _walk(root_node, (), out, source)
    out.sort(key=lambda f: (f.line, f.qualified_name))
    return tuple(out)


def _walk(
    node,
    scope: tuple[str, ...],
    out: list[FunctionComplexity],
    source: bytes,
) -> None:
    if node.type == "function_definition":
        name = _name_of(node, source)
        qualified = ".".join((*scope, name)) if scope else name
        cyclomatic, size, shape_hash = _analyze_body(node)
        out.append(
            FunctionComplexity(
                name=name,
                qualified_name=qualified,
                line=node.start_point[0] + 1,
                cyclomatic=cyclomatic,
                shape_hash=shape_hash,
                size=size,
            )
        )
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.named_children:
                _walk(child, (*scope, name), out, source)
        return
    if node.type == "class_definition":
        name = _name_of(node, source)
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.named_children:
                _walk(child, (*scope, name), out, source)
        return
    for child in node.named_children:
        _walk(child, scope, out, source)


# Leaf/literal node types folded to a single placeholder token so that two
# functions differing only by names or literal values share a shape. Strings
# are collapsed whole (their internal string_start/content/end structure is not
# emitted); identifiers and the literal keywords map to fixed tokens.
_STR_TYPES = frozenset({"string", "concatenated_string"})
_TOKEN = {
    "identifier": "ID",
    "integer": "INT",
    "float": "FLOAT",
    "true": "BOOL",
    "false": "BOOL",
    "none": "NONE",
}


def _analyze_body(func_node) -> tuple[int, int, str]:
    """One pre-order body walk yielding (cyclomatic, size, shape_hash).

    Folds the McCabe CC count, the normalized-token count (`size`), and the
    structural fingerprint (`shape_hash`) into a single traversal so no extra
    parse or walk is added. The token stream is the pre-order sequence of
    `node.type` over every child (anonymous children included, so `a + b` and
    `a - b` differ), with identifiers/literals folded to placeholders, comments
    dropped, and strings collapsed to one token. Nested `function_definition` /
    `class_definition` subtrees are skipped (they get their own row/hash),
    exactly as the CC count excludes them.

    CC parity with the previous `named_children`-only walk holds because every
    branch node in `_BRANCH_NODE_TYPES` is a named node, so widening the walk to
    all children reaches the same branch set.
    """
    body = func_node.child_by_field_name("body")
    if body is None:
        return 1, 0, ""
    count = 1
    tokens: list[str] = []
    stack = [body]
    while stack:
        n = stack.pop()
        t = n.type
        if t in ("function_definition", "class_definition"):
            # Nested definitions get their own row; their branches and shape
            # must not inflate the enclosing function.
            continue
        if t == "comment":
            continue
        if t in _STR_TYPES:
            tokens.append("STR")
            continue
        if t in _BRANCH_NODE_TYPES:
            count += 1
        tokens.append(_TOKEN.get(t, t))
        stack.extend(reversed(n.children))
    if not tokens:
        return count, 0, ""
    shape_hash = hashlib.blake2b("\x00".join(tokens).encode(), digest_size=16).hexdigest()
    return count, len(tokens), shape_hash


def _name_of(node, source: bytes) -> str:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return "<anonymous>"
    return source[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
