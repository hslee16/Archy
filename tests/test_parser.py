from __future__ import annotations

from pathlib import Path

import pytest

from archy.parser import parse_file, parse_source


def test_extracts_simple_import():
    result = parse_source(b"import os\n")
    assert [(r.module, r.is_relative) for r in result.imports] == [("os", False)]
    assert not result.has_errors


def test_extracts_dotted_import():
    result = parse_source(b"import xml.etree.ElementTree\n")
    assert [r.module for r in result.imports] == ["xml.etree.ElementTree"]


def test_extracts_aliased_import():
    result = parse_source(b"import numpy as np\n")
    assert [r.module for r in result.imports] == ["numpy"]


def test_extracts_from_import():
    result = parse_source(b"from collections import OrderedDict\n")
    assert [(r.module, r.is_relative) for r in result.imports] == [("collections", False)]


def test_extracts_relative_imports():
    src = b"from . import sibling\nfrom ..pkg import thing\n"
    result = parse_source(src)
    assert [(r.module, r.is_relative) for r in result.imports] == [
        (".", True),
        ("..pkg", True),
    ]


def test_multiple_imports_preserve_order_by_line():
    src = b"import a\nimport b\nfrom c import d\n"
    result = parse_source(src)
    assert [r.line for r in result.imports] == [1, 2, 3]
    assert [r.module for r in result.imports] == ["a", "b", "c"]


def test_from_import_captures_alias():
    src = b"from x import a as b\n"
    result = parse_source(src)
    [ref] = result.imports
    assert ref.imported_names == ("a",)
    assert ref.imported_aliases == ("b",)


def test_from_import_mixed_aliases():
    src = b"from x import a, b as c, d\n"
    result = parse_source(src)
    [ref] = result.imports
    assert ref.imported_names == ("a", "b", "d")
    assert ref.imported_aliases == (None, "c", None)


def test_plain_import_has_no_aliases_tuple():
    src = b"from x import a\n"
    result = parse_source(src)
    [ref] = result.imports
    assert ref.imported_names == ("a",)
    assert ref.imported_aliases == (None,)


def test_partial_recovery_on_syntax_error():
    src = b"import a\nimport b\ndef !!!broken(:\n"
    result = parse_source(src)
    modules = [r.module for r in result.imports]
    assert "a" in modules
    assert "b" in modules
    assert result.has_errors


def test_ignores_string_literals_that_look_like_imports():
    src = b'x = "import os"\n'
    result = parse_source(src)
    assert result.imports == ()


def test_wildcard_import_records_module_with_no_names():
    result = parse_source(b"from os import *\n")
    assert len(result.imports) == 1
    ref = result.imports[0]
    assert ref.module == "os"
    assert ref.imported_names == ()


def test_conditional_imports_are_extracted():
    src = b"if True:\n    import sys\nelse:\n    from os import path\n"
    result = parse_source(src)
    modules = {r.module for r in result.imports}
    assert modules == {"sys", "os"}


def test_function_local_imports_are_extracted():
    src = b"def f():\n    import json\n    from collections import OrderedDict\n"
    result = parse_source(src)
    modules = {r.module for r in result.imports}
    assert modules == {"json", "collections"}


def test_multiple_names_from_same_module():
    result = parse_source(b"from os.path import join, sep, dirname\n")
    assert len(result.imports) == 1
    ref = result.imports[0]
    assert ref.module == "os.path"
    assert set(ref.imported_names) == {"join", "sep", "dirname"}


def test_aliased_from_import():
    result = parse_source(b"from numpy import ndarray as ND\n")
    ref = result.imports[0]
    assert ref.module == "numpy"
    assert ref.imported_names == ("ndarray",)


# --- call extraction --------------------------------------------------------


def test_extracts_bare_call():
    result = parse_source(b"foo()\n")
    assert [(c.head, c.chain, c.line) for c in result.calls] == [("foo", (), 1)]


def test_extracts_attribute_call_chain():
    result = parse_source(b"mod.sub.foo(1, 2)\n")
    assert [(c.head, c.chain) for c in result.calls] == [("mod", ("sub", "foo"))]


def test_skips_self_and_cls_and_super():
    result = parse_source(b"class A:\n  def m(self):\n    self.x()\n    cls.y()\n    super().z()\n")
    assert result.calls == ()


def test_skips_common_builtins():
    result = parse_source(b"print('x')\nlen([])\nisinstance(x, int)\n")
    assert result.calls == ()


def test_skips_subscript_and_nested_call_function_expressions():
    # The outer call in `arr[0]()`, `f()()`, and `(lambda: 1)()` all have
    # non-identifier function heads and drop. The inner `f()` in `f()()`
    # *is* a valid bare-identifier call and survives - that's correct.
    result = parse_source(b"arr[0]()\nf()()\n(lambda: 1)()\n")
    assert [(c.head, c.chain) for c in result.calls] == [("f", ())]


def test_nested_call_inner_arg_is_extracted():
    # f(g()) - outer head is f, inner is g; both captured separately.
    result = parse_source(b"f(g(1))\n")
    heads = sorted((c.head, c.chain) for c in result.calls)
    assert heads == [("f", ()), ("g", ())]


def test_method_chain_only_resolves_leftmost_identifier():
    # a.b().c() - outer call's function is attribute(call.c), not an
    # identifier chain, so it drops. Inner call (a.b) is captured.
    result = parse_source(b"a.b().c()\n")
    heads = [(c.head, c.chain) for c in result.calls]
    assert heads == [("a", ("b",))]


def test_keyword_args_do_not_confuse_extraction():
    result = parse_source(b"foo(key=value, other=bar())\n")
    heads = sorted((c.head, c.chain) for c in result.calls)
    assert heads == [("bar", ()), ("foo", ())]


def test_calls_inside_function_body_are_extracted():
    src = b"def go():\n    helper.run(42)\n    return helper.done()\n"
    result = parse_source(src)
    heads = sorted({(c.head, c.chain) for c in result.calls})
    assert heads == [("helper", ("done",)), ("helper", ("run",))]


def test_parse_file_honours_a_latin1_cookie(tmp_path: Path):
    """PEP 263: the cookie names the codec, so the file parses instead of
    reaching tree-sitter as bytes a UTF-8 grammar cannot read (#649)."""
    src = '# -*- coding: latin-1 -*-\nGREETING = "Ol\xe1, caf\xe9"\nimport os\n'
    path = tmp_path / "legacy.py"
    path.write_bytes(src.encode("latin-1"))
    with pytest.raises(UnicodeDecodeError):
        path.read_bytes().decode("utf-8")  # the fixture must exercise the bug
    result = parse_file(path)
    assert not result.has_errors
    assert [(r.module, r.is_relative) for r in result.imports] == [("os", False)]


def test_parse_file_honours_a_shift_jis_cookie_whose_text_holds_0x5c(tmp_path: Path):
    """The last byte of 能 is 0x5C in Shift-JIS; to a UTF-8 grammar that byte is
    a backslash escaping the closing quote, so the string never ends (#649)."""
    src = "# -*- coding: shift_jis -*-\nLABEL = '機能'\nfrom pkg import b\n"
    path = tmp_path / "a.py"
    path.write_bytes(src.encode("shift_jis"))
    assert b"\x5c'" in path.read_bytes()  # the raw damage the fix exists for
    result = parse_file(path)
    assert not result.has_errors
    assert [(r.module, r.imported_names) for r in result.imports] == [("pkg", ("b",))]


def test_parse_file_survives_a_cookie_naming_an_unknown_codec(tmp_path: Path):
    """A broken cookie is one unparseable file among the whole tree a scan
    walks, so parse_file degrades to the raw bytes instead of raising (#649)."""
    path = tmp_path / "bad.py"
    path.write_bytes(b"# -*- coding: no-such-codec -*-\nimport os\n")
    result = parse_file(path)
    assert [(r.module, r.is_relative) for r in result.imports] == [("os", False)]
