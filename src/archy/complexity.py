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
    """

    model_config = ConfigDict(frozen=True)

    name: str
    qualified_name: str
    line: int
    cyclomatic: int


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
        out.append(
            FunctionComplexity(
                name=name,
                qualified_name=qualified,
                line=node.start_point[0] + 1,
                cyclomatic=_count_cc(node),
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


def _count_cc(func_node) -> int:
    """McCabe CC for a single function: 1 + branch-node count, excluding nested defs/classes."""
    body = func_node.child_by_field_name("body")
    if body is None:
        return 1
    count = 1
    stack = list(body.named_children)
    while stack:
        n = stack.pop()
        if n.type in ("function_definition", "class_definition"):
            # Nested definitions get their own CC row; their branches must
            # not inflate the enclosing function's count.
            continue
        if n.type in _BRANCH_NODE_TYPES:
            count += 1
        stack.extend(n.named_children)
    return count


def _name_of(node, source: bytes) -> str:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return "<anonymous>"
    return source[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
