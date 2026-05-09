from archy.parser import parse_source


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


def test_partial_recovery_on_syntax_error():
    # The third line is broken; we should still get the two clean imports.
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
