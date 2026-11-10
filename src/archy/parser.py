"""Tree-sitter-based extraction of import statements from Python source.

Robust to syntax errors: tree-sitter produces a partial tree with ERROR nodes,
and we still recover whatever imports parsed cleanly.

archy:owns        CallRef, ImportRef, ParseResult, parse_file, parse_source
archy:mirrored-by CallRef -> archy.graph_assemble, archy.graph_resolve,
                  ImportRef -> archy.graph_assemble, archy.graph_resolve,
                  ParseResult -> archy.duplicates, archy.graph, archy.graph_assemble,
                  archy.graph_resolve, archy.index, parse_file -> archy.graph,
                  archy.index
"""

from __future__ import annotations

import io
import tokenize
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

    `type_checking` is True when the statement sits in the body of an
    `if TYPE_CHECKING:` (or `if typing.TYPE_CHECKING:`) block: it never runs,
    so it is not a runtime dependency, which is what lets `archy cycles` offer
    to leave such edges out. An `else:` or `elif` branch, a function-local
    import, and any other spelling of the guard are plain imports (#520).
    """

    model_config = ConfigDict(frozen=True)

    module: str
    imported_names: tuple[str, ...]
    is_relative: bool
    line: int  # 1-indexed
    imported_aliases: tuple[str | None, ...] = ()
    type_checking: bool = False


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
    """Everything archy reads from one file in a single parse.

    `has_main_guard` is True when the module body has a top-level
    `if __name__ == "__main__":` (either operand order), i.e. the file is
    written to be run directly. It is one of the facts `required:` rules use to
    derive the `@entrypoints` set, so a script added outside every declared
    glob is still governed.
    """

    model_config = ConfigDict(frozen=True)

    imports: tuple[ImportRef, ...]
    has_errors: bool
    calls: tuple[CallRef, ...] = ()
    functions: tuple[FunctionComplexity, ...] = ()
    has_main_guard: bool = False


def parse_file(path: Path) -> ParseResult:
    source = path.read_bytes()
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
    except (SyntaxError, ValueError):
        # A broken or unknown cookie must not crash a whole-tree scan; parse
        # the raw bytes as-is (#649).
        return parse_source(source)
    if encoding.lower().replace("_", "-") in ("utf-8", "utf-8-sig", "ascii"):
        return parse_source(source)
    try:
        # PEP 263: the cookie names the codec, so hand tree-sitter UTF-8
        # re-encoded from it; decode/encode preserves line numbers (#649).
        return parse_source(source.decode(encoding).encode("utf-8"))
    except UnicodeDecodeError:
        # Bytes that do not match their own cookie are parsed raw rather than
        # crashing the scan (#649).
        return parse_source(source)


def parse_source(source: bytes) -> ParseResult:
    parser = Parser(PY_LANGUAGE)
    tree = parser.parse(source)
    cursor = QueryCursor(_IMPORT_QUERY)
    captures = cursor.captures(tree.root_node)

    imports: list[ImportRef] = []
    for node in captures.get("import", []):
        imports.extend(_mark_guarded(_handle_import(node, source), node, source))
    for node in captures.get("from_import", []):
        imports.extend(_mark_guarded(_handle_from_import(node, source), node, source))

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
        has_main_guard=_has_main_guard(tree.root_node, source),
    )


def _mark_guarded(refs: list[ImportRef], node, source: bytes) -> list[ImportRef]:
    """Return `refs` flagged `type_checking` when `node` is under a TYPE_CHECKING guard."""
    if not refs or not _under_type_checking_guard(node, source):
        return refs
    return [ref.model_copy(update={"type_checking": True}) for ref in refs]


def _under_type_checking_guard(node, source: bytes) -> bool:
    """True if `node` is in the body of an `if TYPE_CHECKING:` statement.

    Walks the ancestors. Only the `if` body counts: reaching the guard through
    its `else:` or an `elif:` means the import runs when the guard is false, so
    it is a plain import. Crossing a function or lambda stops the walk and
    answers False, because a function-local import runs on call and is a real
    coupling (#520); the guard around the `def` says nothing about it. When
    unsure the answer is False, so a doubtful import still counts as a cycle.
    """
    child = node
    parent = node.parent
    while parent is not None:
        if parent.type in ("function_definition", "lambda"):
            return False
        if parent.type == "if_statement" and child == parent.child_by_field_name("consequence"):
            condition = parent.child_by_field_name("condition")
            if condition is not None and _is_type_checking_name(condition, source):
                return True
        child, parent = parent, parent.parent
    return False


def _is_type_checking_name(node, source: bytes) -> bool:
    """True for exactly `TYPE_CHECKING` or `typing.TYPE_CHECKING`."""
    return _node_text(node, source) in ("TYPE_CHECKING", "typing.TYPE_CHECKING")


def _has_main_guard(root, source: bytes) -> bool:
    """True if a TOP-LEVEL statement is `if __name__ == "__main__":`.

    Only direct children of the module are checked: a guard nested in a
    function or class never runs as a script entry, so it does not make the
    file an entrypoint.
    """
    for stmt in root.children:
        if stmt.type != "if_statement":
            continue
        cond = stmt.child_by_field_name("condition")
        if cond is not None and _tests_for_main(cond, source):
            return True
    return False


def _tests_for_main(node, source: bytes) -> bool:
    """True if `node` is `__name__ == "__main__"`, possibly parenthesized or
    one operand of an `and`.

    `(__name__ == "__main__")` and `__name__ == "__main__" and not DEBUG` are
    both written to run only as a script, so both make the file an entrypoint.
    `or` is not followed: its body also runs on import, so it says nothing
    about the file being meant to run directly.
    """
    if node.type == "parenthesized_expression" and len(node.children) == 3:
        return _tests_for_main(node.children[1], source)
    if node.type == "boolean_operator":
        operator = node.child_by_field_name("operator")
        if operator is None or operator.type != "and":
            return False
        return any(
            _tests_for_main(side, source)
            for side in (node.child_by_field_name("left"), node.child_by_field_name("right"))
            if side is not None
        )
    if node.type != "comparison_operator" or len(node.children) != 3:
        return False
    left, op, right = node.children
    if op.type != "==":
        return False
    return (
        {left.type, right.type} == {"identifier", "string"}
        and any(
            side.type == "identifier" and _node_text(side, source) == "__name__"
            for side in (left, right)
        )
        and any(_is_main_literal(side, source) for side in (left, right))
    )


def _is_main_literal(node, source: bytes) -> bool:
    """True for a plain string literal whose VALUE is `__main__`.

    Compared on the parsed content, not the source text, so `"'__main__'"`
    (a different value) does not match. Bytes and f-strings never equal a
    module name at runtime, so only no prefix, `r` or `u` qualifies.
    """
    if node.type != "string":
        return False
    parts = node.children
    if len(parts) != 3 or parts[1].type != "string_content":
        return False
    prefix = _node_text(parts[0], source).rstrip("'\"").lower()
    return set(prefix) <= {"r", "u"} and _node_text(parts[1], source) == "__main__"


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
