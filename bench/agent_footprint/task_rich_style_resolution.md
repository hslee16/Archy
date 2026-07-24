Applications that build up custom themes have no supported way to ask a console
what a style name actually resolves to once the theme stack and style
combination have been applied, and an undefined style name currently fails
quietly.

Implement this behavior on the console object:

- A new method `resolve_style(name)` that returns the fully resolved style for a
  style name, as it would be applied when rendering: the active theme stack is
  consulted with the most recently pushed theme winning, and a name that
  combines several styles (for example `"bold red on white"`) resolves to their
  combination.
- `resolve_style` returns `None` for a name that is not defined by any theme in
  the stack and is not a valid style definition on its own.
- A new console option `strict_styles`, defaulting to `False`. When it is
  `True`, resolving or rendering a style name that is not defined raises an
  error whose message names the offending style, instead of the current quiet
  fallback.
- With `strict_styles` left at its default, all existing behavior is unchanged.

Keep all existing public behavior unchanged, and do not modify the existing
tests.
