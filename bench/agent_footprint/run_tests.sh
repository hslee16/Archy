#!/bin/sh
# Test gate for the #282 footprint run. Invoked with cwd = the variant repo dir,
# so the matching per-variant venv is derived from the dir name (each variant has
# flask installed editable into its own venv, which is what gives `flask` package
# metadata that tests/test_cli.py::test_get_version needs).
set -e
root=$(dirname "$PWD")
exec "$root/venv-$(basename "$PWD")/bin/python" -m pytest -q -p no:randomly
