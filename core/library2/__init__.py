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

__all__ = ["ensure_library_v2_schema"]
