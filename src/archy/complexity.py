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
from collections import Counter

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

    def visit(node, scope: tuple[str, ...]) -> None:
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

    _walk_function_defs(root_node, source, visit)
    out.sort(key=lambda f: (f.line, f.qualified_name))
    return tuple(out)


def _walk_function_defs(node, source: bytes, visit, scope: tuple[str, ...] = ()) -> None:
    """Depth-first walk calling `visit(function_definition_node, scope)` per function.

    Owns the scope-tracking traversal skeleton (functions and classes extend the
    dotted scope; other nodes recurse unchanged) so callers supply only the
    per-function payload. `scope` is the tuple of enclosing class/function names.
    """
    if node.type in ("function_definition", "class_definition"):
        name = _name_of(node, source)
        if node.type == "function_definition":
            visit(node, scope)
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.named_children:
                _walk_function_defs(child, source, visit, (*scope, name))
        return
    for child in node.named_children:
        _walk_function_defs(child, source, visit, scope)


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
    count, tokens = _walk_body_tokens(func_node)
    if not tokens:
        return count, 0, ""
    return count, len(tokens), _hash_tokens(tokens)


def _walk_body_tokens(func_node) -> tuple[int, list[str]]:
    """The shared body walk: (cyclomatic, normalized-token stream).

    The single source of the normalization used by `shape_hash` (sequence hash),
    `size` (`len(tokens)`), and the Type-3 token *multiset* (`extract_token_bags`,
    #246), so all three fold identifiers/literals identically. Returns `1, []`
    for a bodiless def (matching the old `1, 0, ""` for `_analyze_body`).
    """
    body = func_node.child_by_field_name("body")
    if body is None:
        return 1, []
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
    return count, tokens


def _hash_tokens(tokens: list[str]) -> str:
    """blake2b-128 hexdigest of a NUL-joined token stream (shared by both hashers)."""
    return hashlib.blake2b("\x00".join(tokens).encode(), digest_size=16).hexdigest()


def _name_of(node, source: bytes) -> str:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return "<anonymous>"
    return source[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")


class FunctionFeatures(BaseModel):
    """Extra per-function signals used to de-noise duplicate clusters (issue #242).

    `decorators` is the tuple of decorator head names on the function (dotted, so
    `@typing.overload` -> `"typing.overload"`, `@app.get("/x")` -> `"app.get"`).
    `is_trivial` marks a body that is pure boilerplate (no branch, no call, no
    nested block) - a getter, a `__init__` of assignments, a `pass` stub - which
    clusters by shape but is not refactorable duplication. Both are computed
    lazily (see `extract_function_features`) only for already-clustered members,
    so they never touch the persisted `ParseResult` / `FunctionComplexity`.
    """

    model_config = ConfigDict(frozen=True)

    decorators: tuple[str, ...] = ()
    is_trivial: bool = False
    concrete_hash: str = ""


# A body containing any of these is not trivial boilerplate. Branch nodes are
# already covered by _BRANCH_NODE_TYPES; the rest are blocks/effects that make a
# body worth reading. `call` is the load-bearing one: it separates a getter
# (`return self._x`) from a delegation (`return self._x.foo()`).
_NONTRIVIAL_NODE_TYPES = frozenset(
    _BRANCH_NODE_TYPES
    | {
        "call",
        "with_statement",
        "try_statement",
        "await",
        "yield",
        "function_definition",
        "class_definition",
    }
)


def extract_function_features(source: bytes) -> dict[int, FunctionFeatures]:
    """Parse `source` and return per-function features keyed by 1-indexed def line.

    The key matches `FunctionComplexity.line` (the `def` line, not the decorator
    line), so a duplicate-cluster member can be looked up directly. Reuses the
    shared tree-sitter parser; no new dependency.
    """
    parser = Parser(PY_LANGUAGE)
    tree = parser.parse(source)
    out: dict[int, FunctionFeatures] = {}

    def visit(node, scope: tuple[str, ...]) -> None:
        out[node.start_point[0] + 1] = FunctionFeatures(
            decorators=_decorator_names(node, source),
            is_trivial=_is_trivial_body(node),
            concrete_hash=_concrete_hash(node, source),
        )

    _walk_function_defs(tree.root_node, source, visit)
    return out


def extract_token_bags(source: bytes) -> dict[int, Counter[str]]:
    """Parse `source` and return each function's normalized-token *multiset*.

    Keyed by 1-indexed `def` line (matching `FunctionComplexity.line`), the same
    key `compute_duplicates` members carry. The multiset is the bag of the same
    folded tokens `shape_hash` hashes as a *sequence* (`_walk_body_tokens`), so a
    Type-3 clone - which reorders/inserts/deletes and thus changes the sequence
    (and the hash) - keeps a nearly-identical bag. This is the position-agnostic
    representation the token-overlap near-miss pass (#246) compares. Computed on
    demand (a second parse, like `extract_function_features`); nothing is
    persisted, so the warm index cache is untouched.
    """
    parser = Parser(PY_LANGUAGE)
    tree = parser.parse(source)
    out: dict[int, Counter[str]] = {}

    def visit(node, scope: tuple[str, ...]) -> None:
        _, tokens = _walk_body_tokens(node)
        out[node.start_point[0] + 1] = Counter(tokens)

    _walk_function_defs(tree.root_node, source, visit)
    return out


def _concrete_hash(func_node, source: bytes) -> str:
    """Hash of the UN-normalized body: node types plus each leaf's actual text.

    Unlike the shape hash (identifiers/literals folded to placeholders), this
    preserves the concrete tokens, so two functions share it only when their
    bodies are byte-identical modulo whitespace and comments (a Type-1 clone).
    Members of a shape cluster that also share this are exact copy-paste, the
    highest-confidence duplicates; those that differ are Type-2 (parameterized).
    """
    body = func_node.child_by_field_name("body")
    if body is None:
        return ""
    tokens: list[str] = []
    stack = [body]
    while stack:
        n = stack.pop()
        if n.type == "comment":
            continue
        if n.child_count == 0:
            tokens.append(source[n.start_byte : n.end_byte].decode("utf-8", errors="replace"))
        else:
            tokens.append(n.type)
        stack.extend(reversed(n.children))
    if not tokens:
        return ""
    return _hash_tokens(tokens)


def _decorator_names(func_node, source: bytes) -> tuple[str, ...]:
    """Decorator head names on a function, or () if undecorated.

    A decorated function is wrapped in a `decorated_definition`; each `decorator`
    child holds one expression. For `@app.get(...)` the call wrapper is stripped
    to its `function` (`app.get`) so the arguments do not pollute the name.
    """
    parent = func_node.parent
    if parent is None or parent.type != "decorated_definition":
        return ()
    names: list[str] = []
    for child in parent.children:
        if child.type != "decorator":
            continue
        expr = next((c for c in child.named_children), None)
        if expr is None:
            continue
        if expr.type == "call":
            fn = expr.child_by_field_name("function")
            if fn is not None:
                expr = fn
        names.append(source[expr.start_byte : expr.end_byte].decode("utf-8", errors="replace"))
    return tuple(names)


def _is_trivial_body(func_node) -> bool:
    """True when the body is pure boilerplate: no branch, call, block, or nested def."""
    body = func_node.child_by_field_name("body")
    if body is None:
        return True
    stack = list(body.named_children)
    while stack:
        n = stack.pop()
        if n.type in _NONTRIVIAL_NODE_TYPES:
            return False
        stack.extend(n.named_children)
    return True
