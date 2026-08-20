"""Library Manager v2 — THE library. Not opt-in, not parallel, not a preview.

The ``lib2_*`` tables are the single maintained catalogue. The cutover already
happened: ``core.library2.feature.library_v2_enabled`` returns ``True``
unconditionally and the config key that used to disable it is read only to warn
that it is ignored (see that module — the decision is deliberately not
reversible through configuration).

The legacy ``artists`` / ``albums`` / ``tracks`` tables still exist, and are:

- the SOURCE of the one-shot upgrade import (``importer``), read-only; and
- the rollback boundary for an installation that upgraded.

Nothing at runtime reads or writes them. That is not a claim, it is enforced:
``tests/library2/test_legacy_usage_ratchet.py`` counts every legacy-table access
in ``core/``, ``database/``, ``api/``, ``utils/``, ``services/``,
``web_server.py`` and ``dev.py`` against a checked-in baseline that currently
reads ``reads: 0, writes: 0``, and fails in BOTH directions — dropping below the
baseline is also an error, so the number cannot quietly drift.

This docstring said "opt-in ... imports from the legacy library read-only" for
long after both halves stopped being true, which is worse than saying nothing:
it is the first thing a maintainer reads about the package.

Modules:
- ``schema``   — idempotent DDL (``ensure_library_v2_schema``).
- ``importer`` — one-shot population of ``lib2_*`` from a pre-v2 library (re-runnable).
- ``status``   — pure read helpers: metadata gaps, quality tier, file/roll-up status.
"""

from .schema import ensure_library_v2_schema

# ADR-01 (admin-only): Library v2 has exactly one authoritative user intent —
# the admin profile (profiles.id = 1). The lib2 monitored columns are global,
# so scoping them to any other profile would let one user overwrite another's
# state (audit P0-02). Enforced by the API write guard and the importer.
ADMIN_PROFILE_ID = 1

__all__ = ["ensure_library_v2_schema", "ADMIN_PROFILE_ID"]
