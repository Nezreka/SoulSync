"""Monitoring intent created only after an extra album file is registered."""
from core.imports.paths import import_owner_id
from core.library_scope import scope_for_owner
from core.library2.sql_util import intent_profile_id
from core.library2.monitor_rules import PROVENANCE_FILE, PROVENANCE_USER, record_rule
from core.library2.profile_lookup import assign_quality_profile
from core.library2.wanted import recompute_wanted


def monitor_imported_extra(conn, track_id: int, context: dict) -> None:
    profile_id = intent_profile_id(scope_for_owner(import_owner_id(context)))
    existing = conn.execute("SELECT monitored, provenance FROM lib2_monitor_rules WHERE entity_type='track' AND entity_id=? AND profile_id=?", (track_id, profile_id)).fetchone()
    if existing and existing['provenance'] == PROVENANCE_USER:
        # Including a deliberate opt-in: keep the stronger provenance intact.
        return
    qp = (context.get('track_info') or {}).get('quality_profile_id')
    if qp is not None:
        assign_quality_profile(conn, 'tracks', track_id, int(qp))
    record_rule(conn, 'track', track_id, True, PROVENANCE_FILE, profile_id=profile_id)
    if profile_id == 1:
        conn.execute('UPDATE lib2_tracks SET monitored=1 WHERE id=?', (track_id,))
    recompute_wanted(conn, profile_id=profile_id, track_ids=[track_id])
