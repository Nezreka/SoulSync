"""Persist a finished watchlist scan to the History ledger (#831 / #933).

Both the manual scan (``web_server.start_watchlist_scan``) and the automatic
scan (``core.watchlist.auto_scan.process_watchlist_scan_automatically``) finish
with the same ``watchlist_scan_state`` shape, but only the manual path used to
record a history row — so scheduled/nightly scans never showed up in the
History modal (#933). This single helper is the shared seam: both paths call it,
so they can't drift apart again.

Pure except for the one ``database.save_watchlist_scan_run`` call — the field
extraction is unit-testable with a fake database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional


def _iso(value: Any) -> Optional[str]:
    """ISO-format a datetime; pass through an already-stringified timestamp."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)


def persist_scan_run(database: Any, state: Dict[str, Any], *,
                     profile_id: Any, was_cancelled: bool) -> bool:
    """Record one watchlist scan run + its track ledger from ``state``.

    Reads the counts/timestamps/ledger off the live ``watchlist_scan_state`` the
    scanner just finished writing, and writes a single history row. ``run_id``
    comes from ``state['scan_run_id']`` (both paths stamp it); a timestamp
    fallback keeps it from ever colliding if that's somehow missing. Returns the
    DB call's truthiness; callers wrap in their own try/except so a history-write
    failure never breaks the scan.
    """
    summary = state.get('summary') or {}
    run_id = state.get('scan_run_id') or datetime.now().strftime('%Y%m%d-%H%M%S')
    return database.save_watchlist_scan_run(
        run_id=run_id,
        profile_id=profile_id if profile_id else 1,
        status='cancelled' if was_cancelled else 'completed',
        started_at=_iso(state.get('started_at')),
        completed_at=_iso(state.get('completed_at')) or datetime.now().isoformat(),
        total_artists=summary.get('total_artists', state.get('total_artists', 0)),
        artists_scanned=summary.get('successful_scans', 0),
        tracks_found=state.get('tracks_found_this_scan', 0),
        tracks_added=state.get('tracks_added_this_scan', 0),
        track_events=state.get('scan_track_events') or [],
    )
