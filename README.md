**Problem.** The README's `roots:` paragraph says a root makes "`src/service/db.py` become `service.db`", but never says the thing that bites: a root names **the package directory itself**, not the directory that contains it. That is the opposite of the `PYTHONPATH`, Tach `source_roots` and grimp intuition, and the wrong form fails silently.

Verified on main at v0.50.1 with a PEP 420 layout (`pac

File (README.md):
<!-- mcp-name: io.github.hslee16/archy -->

<p align="center"><img src="docs/assets/logo-wordmark.png" alt="archy" width="420"></p>

[![PyPI](https://img.shields.io/pypi/v/archy.svg)](https://pypi.org/project/archy/)
[![Python](https://img.shields.io/pypi/pyversions/archy.svg)](https://pypi.org/project/archy/)
[![CI](https://github.com/hslee16/archy/actions/workflows/ci.yml/badge.svg)](https://github.com/hslee16/archy/actions/workflows/ci.yml)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![License](https
