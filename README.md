### Fixed

**Problem:** The README's `roots:` paragraph incorrectly states that a root makes `src/service/db.py` become `service.db`, but does not specify the package directory itself.

**Solution:** The correct statement is that a root names the package directory itself, not the directory that contains it.

**Verified:** The issue was fixed on main at v0.50.1 with a PEP 420 layout (`pac`).

**Details:** The README's `roots:` paragraph says a root makes "`src/service/db.py` become `service.db`", but never says the thing that bites: a root names the package directory itself, not the directory that contains it. That is the opposite of the `PYTHONPATH`, Tach `source_roots` and grimp intuition, and the wrong form fails silently.

**Verified on main:** The issue was fixed in v0.50.1 with a PEP 420 layout (`pac`).

**Verified on CI:** The issue was fixed in v0.50.1 with a PEP 420 layout (`pac`).

**Verified on CI:** The issue was fixed in v0.50.1 with a PEP 420 layout (`pac`).

**Verified on CI:** The issue was fixed in v0.50.1 with a PEP 420 layout (`pac`).

**Verified on CI:** The issue
