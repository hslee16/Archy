"""Tree-sitter-based extraction of import statements from Python source.

Robust to syntax errors: tree-sitter produces a partial tree with ERROR nodes,
and we still recover whatever imports parsed cleanly.
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter_python as tsp
from pydantic import BaseModel, ConfigDict
from tree_sitter import Language, Parser, Query, QueryCursor

from archy.complexity import FunctionComplexity, walk_functions

PY_LANGUAGE = Language(tsp.language())

# We capture each import statement as a whole, then walk its fields. This is
# clearer than juggling many top-level captures whose grouping back into a
# single statement is ambiguous when the statement imports multiple names.
_IMPORT_QUERY_SRC = """
(import_statement) @import
(import_from_statement) @from_import
"""

_IMPORT_QUERY = Query(PY_LANGUAGE, _IMPORT_QUERY_SRC)

_CALL_QUERY = Query(PY_LANGUAGE, "(call) @call")

# Heads we never want to attribute as cross-module calls. `self`/`cls` are
# method dispatch within the same class; `super` is parent-class dispatch.
# Common builtins keep static-resolution noise out of the call graph.
_SKIP_CALL_HEADS = frozenset(
    {
        "self",
        "cls",
        "super",
        "print",
        "len",
        "range",
        "isinstance",
        "issubclass",
        "type",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "tuple",
        "dict",
        "set",
        "frozenset",
        "bytes",
        "bytearray",
        "iter",
        "next",
        "getattr",
        "setattr",
        "hasattr",
        "delattr",
        "callable",
        "id",
        "hash",
        "repr",
        "open",
        "zip",
        "map",
        "filter",
        "sorted",
        "reversed",
        "enumerate",
        "min",
        "max",
        "sum",
        "abs",
        "round",
        "any",
        "all",
        "vars",
        "dir",
        "object",
        "Exception",
        "ValueError",
        "TypeError",
        "KeyError",
        "AttributeError",
        "RuntimeError",
        "StopIteration",
        "NotImplementedError",
        "ImportError",
        "IndexError",
        "OSError",
        "FileNotFoundError",
    }
)


class ImportRef(BaseModel):
    """A single import statement extracted from a source file.

    `module` is the dotted path written after `import` or `from`. For
    relative imports the leading dots are preserved (e.g. '.', '..pkg').
    `imported_names` is the tuple of source names from a
    `from X import a, b, c` form - these may be submodules of `X`, so the
    graph resolver checks both `X` and `X.a` against the internal module
    set. For plain `import X` forms it is empty. `imported_aliases` runs
    parallel to `imported_names`: each entry is the local rebinding name
    from `as`, or None if the name was imported under its source spelling.
    """

    model_config = ConfigDict(frozen=True)

    module: str
    imported_names: tuple[str, ...]
    is_relative: bool
    line: int  # 1-indexed
    imported_aliases: tuple[str | None, ...] = ()


class CallRef(BaseModel):
    """A single call site `f(...)` or `a.b.c(...)` extracted from a source file.

    `head` is the leftmost identifier of the callee expression (the binding
    we look up in the file's import alias table). `chain` is the remaining
    attribute path after the head: `mod.sub.foo()` → head=`mod`, chain=
    `('sub','foo')`. Calls whose function expression doesn't start with a
    bare identifier (subscript, lambda, nested call result, etc.) are
    dropped at parse time and never appear here. The line is 1-indexed.
    """

    model_config = ConfigDict(frozen=True)

    head: str
    chain: tuple[str, ...]
    line: int


class ParseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    imports: tuple[ImportRef, ...]
    has_errors: bool
    calls: tuple[CallRef, ...] = ()
    functions: tuple[FunctionComplexity, ...] = ()


def parse_file(path: Path) -> ParseResult:
    return parse_source(path.read_bytes())


def parse_source(source: bytes) -> ParseResult:
    parser = Parser(PY_LANGUAGE)
    tree = parser.parse(source)
    cursor = QueryCursor(_IMPORT_QUERY)
    captures = cursor.captures(tree.root_node)

    imports: list[ImportRef] = []
    for node in captures.get("import", []):
        imports.extend(_handle_import(node, source))
    for node in captures.get("from_import", []):
        imports.extend(_handle_from_import(node, source))

    imports.sort(key=lambda ref: (ref.line, ref.module))

    call_cursor = QueryCursor(_CALL_QUERY)
    call_captures = call_cursor.captures(tree.root_node)
    calls: list[CallRef] = []
    for node in call_captures.get("call", []):
        ref = _handle_call(node, source)
        if ref is not None:
            calls.append(ref)
    calls.sort(key=lambda ref: (ref.line, ref.head, ref.chain))

    functions = walk_functions(tree.root_node, source)

    return ParseResult(
        imports=tuple(imports),
        has_errors=tree.root_node.has_error,
        calls=tuple(calls),
        functions=functions,
    )


def _handle_import(node, source: bytes) -> list[ImportRef]:
    """Handle `import a`, `import a.b`, `import a as x`, `import a, b`.

    For aliased plain imports (`import X.Y as Z`), the alias is recorded
    in `imported_aliases` even though `imported_names` is empty - this is
    how call-graph resolution learns the local binding (`Z` here) maps to
    the deepest module (`X.Y`). Non-aliased plain imports leave both
    tuples empty; consumers infer the top-level binding from `module`.
    """
    refs: list[ImportRef] = []
    line = node.start_point[0] + 1
    for child in node.children_by_field_name("name"):
        module = _extract_module_name(child, source)
        if not module:
            continue
        alias = _extract_alias(child, source)
        refs.append(
            ImportRef(
                module=module,
                imported_names=(),
                is_relative=False,
                line=line,
                imported_aliases=(alias,) if alias else (),
            )
        )
    return refs


def _handle_from_import(node, source: bytes) -> list[ImportRef]:
    """Handle `from M import a, b` and relative variants."""
    line = node.start_point[0] + 1
    module_node = node.child_by_field_name("module_name")
    if module_node is None:
        return []
    module_text = _node_text(module_node, source)
    is_relative = module_node.type == "relative_import"

    name_nodes = node.children_by_field_name("name")
    imported: list[str] = []
    aliases: list[str | None] = []
    for n in name_nodes:
        name = _extract_module_name(n, source)
        if not name:
            continue
        imported.append(name)
        aliases.append(_extract_alias(n, source))

    return [
        ImportRef(
            module=module_text,
            imported_names=tuple(imported),
            is_relative=is_relative,
            line=line,
            imported_aliases=tuple(aliases),
        )
    ]


def _extract_alias(node, source: bytes) -> str | None:
    if node.type != "aliased_import":
        return None
    alias = node.child_by_field_name("alias")
    return _node_text(alias, source) if alias is not None else None


def _extract_module_name(node, source: bytes) -> str:
    if node.type == "aliased_import":
        inner = node.child_by_field_name("name")
        if inner is not None:
            return _node_text(inner, source)
        return ""
    return _node_text(node, source)


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _handle_call(node, source: bytes) -> CallRef | None:
    """Extract the head identifier and attribute chain from a `(call)` node.

    Returns None for callees whose left-hand side is not a bare identifier
    (subscript indexing, lambda invocation, nested call result, etc.) and
    for heads we deliberately skip (self/cls/super, common builtins).
    """
    function = node.child_by_field_name("function")
    if function is None:
        return None
    chain: list[str] = []
    head_node = function
    # Walk attribute chains right-to-left: outer node is (attribute
    # object=<inner> attribute=<name>). Continue until we hit a bare
    # identifier (the head) or a non-attribute non-identifier (skip).
    while head_node.type == "attribute":
        attr = head_node.child_by_field_name("attribute")
        obj = head_node.child_by_field_name("object")
        if attr is None or obj is None:
            return None
        chain.append(_node_text(attr, source))
        head_node = obj
    if head_node.type != "identifier":
        return None
    head_text = _node_text(head_node, source)
    if head_text in _SKIP_CALL_HEADS:
        return None
    chain.reverse()
    return CallRef(
        head=head_text,
        chain=tuple(chain),
        line=node.start_point[0] + 1,
    )
