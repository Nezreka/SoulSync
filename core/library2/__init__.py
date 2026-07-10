"""Library Manager v2 — opt-in, database-as-source-of-truth library subsystem.

This package is the foundation of the parallel "Library v2" redesign. It is gated
behind the ``features.library_v2`` config flag and never touches the existing
``artists`` / ``albums`` / ``tracks`` tables destructively — it lives in its own
``lib2_*`` tables and *imports* from the legacy library read-only.

Modules:
- ``schema``   — idempotent DDL (``ensure_library_v2_schema``).
- ``importer`` — populate ``lib2_*`` from the existing library (re-runnable).
- ``status``   — pure read helpers: metadata gaps, quality tier, file/roll-up status.
"""

from .schema import ensure_library_v2_schema

# ADR-01 (admin-only): Library v2 has exactly one authoritative user intent —
# the admin profile (profiles.id = 1). The lib2 monitored columns are global,
# so scoping them to any other profile would let one user overwrite another's
# state (audit P0-02). Enforced by the API write guard and the importer.
ADMIN_PROFILE_ID = 1

__all__ = ["ensure_library_v2_schema", "ADMIN_PROFILE_ID"]
