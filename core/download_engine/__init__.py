"""Download Engine — central owner of cross-source download state,
thread workers, search retry, rate-limits, and fallback chains.

This is the second leg of the multi-source download dispatcher
refactor (the first leg, ``core/download_plugins/``, defined the
contract). The engine takes ownership of everything that used to
be duplicated across the per-source clients (background thread
workers, active_downloads dicts, search retry ladders, quality
filtering, hybrid fallback). Clients become DUMB — just hit the
API for their source, manage their own auth state, and let the
engine drive everything else.

This package is built up in phases (see
``docs/download-engine-refactor-plan.md`` for the full plan):

- Phase B (current) — engine skeleton + state lift.
- Phase C — background download worker.
- Phase D — search retry + quality filter.
- Phase E — rate-limit pool.
- Phase F — fallback chain.

Each phase is purely additive at first (engine grows, clients
unchanged). Migration to the new shape happens one source per
commit so behavior never breaks across the suite.
"""

from core.download_engine.engine import DownloadEngine

__all__ = ["DownloadEngine"]
