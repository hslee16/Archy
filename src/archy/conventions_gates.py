"""Which findings fail a build, and which exits are bad input: the gate and error census."""

from __future__ import annotations

import ast
from collections.abc import Iterable

from archy.conventions_collect import _dotted, _ModuleFacts
from archy.conventions_models import Gate

# Call/raise targets that end the process non-zero. `ctx.exit` and a bare
# `exit` are matched on their last dotted segment so any Click context
# variable name works, not just the conventional `ctx`.
_EXIT_CALLS = frozenset({"sys.exit", "os._exit", "exit"})


_EXIT_RAISES = frozenset({"SystemExit", "ClickException", "UsageError", "Abort", "Exit"})


def _click_flags(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    """Map parameter name -> the CLI flag that fills it, from Click decorators.

    Click's own resolution rules: an explicit non-dash string argument names
    the destination, otherwise the longest `--long-option` becomes it with
    dashes folded to underscores.
    """
    flags: dict[str, str] = {}
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        target = _dotted(dec.func).rsplit(".", 1)[-1]
        if target not in {"option", "argument"}:
            continue
        literals: list[str] = []
        for arg in dec.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                literals.append(arg.value)
        longs = sorted(
            (s for s in literals if s.startswith("--")), key=lambda s: len(s), reverse=True
        )
        explicit = [s for s in literals if not s.startswith("-")]
        display = longs[0] if longs else (explicit[0] if explicit else "")
        if not display:
            continue
        dest = explicit[0] if explicit else longs[0].lstrip("-").replace("-", "_")
        flags[dest] = display
    return flags


def _is_command(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _dotted(dec.func if isinstance(dec, ast.Call) else dec).endswith(("command", "group"))
        for dec in fn.decorator_list
    )


def _literal_code(call: ast.expr | None) -> int | None:
    """The literal exit status of an exit call, or None when it is computed.

    `sys.exit(0 if ok else 1)` deliberately returns None: two codes are
    possible and reporting either one would be a lie.
    """
    if not isinstance(call, ast.Call) or not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, int):
        return first.value
    return None


def _exit_site(node: ast.stmt) -> tuple[str, str, int | None] | None:
    """`(category, kind, code)` for an exiting statement, else None.

    A raised framework exception is the ERROR channel (the framework picks
    the code); an explicit exit call or `SystemExit` is the GATE channel
    (the code is the result). See `Gate` for why the split matters.
    """
    if isinstance(node, ast.Raise) and node.exc is not None:
        call = node.exc if isinstance(node.exc, ast.Call) else None
        exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        name = _dotted(exc).rsplit(".", 1)[-1]
        if name == "SystemExit":
            return ("gate", name, _literal_code(call))
        if name in _EXIT_RAISES:
            return ("error", name, None)
        return None
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        call = node.value
        dotted = _dotted(call.func)
        if dotted in _EXIT_CALLS or dotted.rsplit(".", 1)[-1] == "exit":
            return ("gate", dotted, _literal_code(call))
    return None


def _control_for(condition: ast.expr | None, params: set[str], flags: dict[str, str]) -> str:
    """Name the lever that decides whether the innermost guard fires."""
    if condition is None:
        return "hardcoded"
    names = [n.id for n in ast.walk(condition) if isinstance(n, ast.Name)]
    for name in names:
        if name in flags:
            return f"flag:{flags[name]}"
    # An attribute read beats a bare parameter: `config.fail_on_error` names
    # the field a caller sets, where `param:config` only names the object it
    # is reached through, which is not a lever anybody can turn.
    attrs = [_dotted(n) for n in ast.walk(condition) if isinstance(n, ast.Attribute) and _dotted(n)]
    if attrs:
        return f"config:{sorted(attrs)[0]}"
    for name in names:
        if name in params:
            return f"param:{name}"
    return "hardcoded"


def _scan_gates(
    body: Iterable[ast.stmt],
    *,
    condition: ast.expr | None,
    out: list[tuple[tuple[str, str, int | None], ast.expr | None]],
) -> None:
    """Walk statements, remembering the innermost `if` test above each exit."""
    for node in body:
        site = _exit_site(node)
        if site is not None:
            out.append((site, condition))
            continue
        if isinstance(node, ast.If):
            _scan_gates(node.body, condition=node.test, out=out)
            _scan_gates(node.orelse, condition=condition, out=out)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            # A nested def is scanned as its own function by the caller's
            # `ast.walk`; descending here would double-count it.
            continue
        else:
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(node, field, None)
                if isinstance(nested, list):
                    _scan_gates(nested, condition=condition, out=out)
            for handler in getattr(node, "handlers", []) or []:
                _scan_gates(handler.body, condition=condition, out=out)


def _gates(facts: Iterable[_ModuleFacts]) -> tuple[tuple[Gate, ...], tuple[Gate, ...]]:
    """`(gates, errors)`.

    Only FUNCTION bodies are scanned, which is also what drops the
    `if __name__ == "__main__": sys.exit(main())` entry point: it is
    module-level, and it forwards a return value rather than stating a
    verdict, so counting it as a gate would inflate every Click project by
    exactly one.
    """
    found_gates: list[Gate] = []
    found_errors: list[Gate] = []
    for f in facts:
        for fn in f.functions:
            found: list[tuple[tuple[str, str, int | None], ast.expr | None]] = []
            _scan_gates(fn.body, condition=None, out=found)
            if not found:
                continue
            flags = _click_flags(fn)
            args = fn.args
            params = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
            for (category, kind, code), condition in found:
                site = Gate(
                    module=f.qualname,
                    function=fn.name,
                    category=category,
                    kind=kind,
                    code=code,
                    control=_control_for(condition, params, flags),
                    is_command=_is_command(fn),
                )
                (found_gates if category == "gate" else found_errors).append(site)
    key = lambda g: (g.module, g.function, g.kind, g.control)  # noqa: E731
    found_gates.sort(key=key)
    found_errors.sort(key=key)
    return tuple(found_gates), tuple(found_errors)
