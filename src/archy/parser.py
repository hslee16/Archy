"""Tree-sitter-based extraction of import statements from Python source.

Robust to syntax errors: tree-sitter produces a partial tree with ERROR nodes,
and we still recover whatever imports parsed cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tree_sitter_python as tsp
from tree_sitter import Language, Parser, Query, QueryCursor

PY_LANGUAGE = Language(tsp.language())

# We capture each import statement as a whole, then walk its fields. This is
# clearer than juggling many top-level captures whose grouping back into a
# single statement is ambiguous when the statement imports multiple names.
_IMPORT_QUERY_SRC = """
(import_statement) @import
(import_from_statement) @from_import
"""

_IMPORT_QUERY = Query(PY_LANGUAGE, _IMPORT_QUERY_SRC)


@dataclass(frozen=True)
class ImportRef:
    """A single import statement extracted from a source file.

    `module` is the dotted path written after `import` or `from`. For
    relative imports the leading dots are preserved (e.g. '.', '..pkg').
    `imported_names` is the tuple of names brought into the local namespace
    by a `from X import a, b, c` form — these may be submodules of `X`,
    so the graph resolver checks both `X` and `X.a` against the internal
    module set. For plain `import X` forms it is empty.
    """

    module: str
    imported_names: tuple[str, ...]
    is_relative: bool
    line: int  # 1-indexed


@dataclass(frozen=True)
class ParseResult:
    imports: tuple[ImportRef, ...]
    has_errors: bool


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
    return ParseResult(imports=tuple(imports), has_errors=tree.root_node.has_error)


def _handle_import(node, source: bytes) -> list[ImportRef]:
    """Handle `import a`, `import a.b`, `import a as x`, `import a, b`."""
    refs: list[ImportRef] = []
    line = node.start_point[0] + 1
    for child in node.children_by_field_name("name"):
        module = _extract_module_name(child, source)
        if module:
            refs.append(
                ImportRef(
                    module=module,
                    imported_names=(),
                    is_relative=False,
                    line=line,
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
    for n in name_nodes:
        name = _extract_module_name(n, source)
        if name:
            imported.append(name)

    return [
        ImportRef(
            module=module_text,
            imported_names=tuple(imported),
            is_relative=is_relative,
            line=line,
        )
    ]


def _extract_module_name(node, source: bytes) -> str:
    """Pull the underlying dotted name from `dotted_name` or `aliased_import`."""
    if node.type == "aliased_import":
        inner = node.child_by_field_name("name")
        if inner is not None:
            return _node_text(inner, source)
        return ""
    return _node_text(node, source)


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
