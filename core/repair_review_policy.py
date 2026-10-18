"""Review decision policy shared by queue readers and action selection.

Finding readiness, whole-album queue buckets, and the global ready count
are separate projections. Keep their contracts explicit; a ready finding may
belong to an album that still needs input.
"""
from __future__ import annotations
from typing import Any

_DETAILS = "CASE WHEN json_valid(details_json) THEN details_json ELSE '{}' END"


def _artist_replacement_sql(details: str = _DETAILS) -> str:
    return '(' + ' OR '.join(
        f"(COALESCE(json_extract({details}, '$.changes.{field}.old'), '') != '' "
        f"AND lower(COALESCE(json_extract({details}, '$.changes.{field}.old'), '')) "
        f"!= lower(COALESCE(json_extract({details}, '$.changes.{field}.new'), '')))"
        for field in ('artist', 'track_artist', 'library_track_artist')
    ) + ')'

_DESTRUCTIVE_TYPES = frozenset({
    'reconciliation_extra', 'reconciliation_duplicate',
})
_GLOBAL_BLOCKER_TYPES = frozenset({'reconciliation_release'})
_POSITION_CHANGE_KEYS = frozenset({
    'track_number', 'disc_number', 'library_track_number', 'library_disc_number',
})
_TITLE_CHANGE_KEYS = frozenset({'title', 'library_title', 'filename'})
_TRACK_ARTIST_CHANGE_KEYS = frozenset({
    'artist', 'track_artist', 'library_track_artist',
})
_RECORDING_ID_CHANGE_KEYS = frozenset({
    'musicbrainz_recording_id', 'library_recording_mbid',
})


def _replaces_artist_credit(changes: dict[str, Any]) -> bool:
    """An unrelated nonempty credit needs a person, even on a clear match."""
    for field in _TRACK_ARTIST_CHANGE_KEYS:
        change = changes.get(field)
        old, new = _change_value(change, 'old'), _change_value(change, 'new')
        if old and new and old.casefold() != new.casefold():
            return True
    return False


def _change_value(change: Any, key: str) -> str:
    value = change.get(key) if isinstance(change, dict) else None
    return str(value or '').strip()


def finding_category(item: dict[str, Any]) -> str:
    """User-facing reconciliation category, independent of scanner wording."""
    finding_type = str(item.get('finding_type') or '')
    details = item.get('details') if isinstance(item.get('details'), dict) else {}
    changes = details.get('changes') if isinstance(details.get('changes'), dict) else {}
    if finding_type == 'reconciliation_release':
        return 'release_conflicts'
    if (finding_type == 'reconciliation_missing' and
            details.get('fulfillment_state') in ('wishlisted', 'downloading', 'downloaded')):
        return 'pending_fulfillment'
    if finding_type in ('reconciliation_intent', 'reconciliation_missing'):
        return 'album_completeness'
    if details.get('audio_conflict') == 'different_recording':
        return 'audio_conflicts'
    if finding_type in _DESTRUCTIVE_TYPES:
        return 'duplicates' if finding_type == 'reconciliation_duplicate' else 'extra_files'
    if finding_type == 'reconciliation_unresolved':
        return 'audio_conflicts'
    # A possibly different recording version is the controlling decision even
    # when its atomic repair also carries track-number or filename changes.
    # The grouped numbering button must never conceal a material title edit.
    if (details.get('title_difference_kind') in ('material', 'version') and
            _TITLE_CHANGE_KEYS.intersection(changes)):
        return 'title_issues'
    if _POSITION_CHANGE_KEYS.intersection(changes):
        return 'numbering'
    if (details.get('title_difference_kind') in ('typography', 'flavor') and
            _TITLE_CHANGE_KEYS.intersection(changes)):
        return 'title_conventions'
    return 'metadata'


def _finding_tier(item: dict[str, Any], *, global_blocked: bool = False) -> tuple[str, str]:
    """Classify one reconciliation action for review and bulk application."""
    finding_type = str(item.get('finding_type') or '')
    details = item.get('details') if isinstance(item.get('details'), dict) else {}
    changes = details.get('changes') if isinstance(details.get('changes'), dict) else {}
    if item.get('last_error'):
        return 'manual', 'The previous apply attempt failed'
    if (finding_type == 'reconciliation_missing' and
            details.get('fulfillment_state') in ('wishlisted', 'downloading', 'downloaded')):
        return 'blocked', ('Downloaded; waiting for a verified library assignment' if details.get('fulfillment_state') == 'downloaded' else 'The missing track is already on the wishlist')
    if finding_type == 'reconciliation_unresolved' and details.get('plan_approved'):
        return ('blocked', 'Choose the intended release first') if global_blocked else ('ready', 'File action saved in the album plan')
    if finding_type in _DESTRUCTIVE_TYPES:
        if details.get('plan_approved') or details.get('plan_keep'):
            if global_blocked:
                return 'blocked', 'Choose the intended release first'
            return 'ready', 'File decision saved; it will run only when this album plan is applied'
        if details.get('audio_conflict') == 'different_recording':
            return ('destructive',
                    'The fingerprint identifies different audio than the expected release track')
        return 'destructive', 'This action moves or removes a file and requires review'
    if finding_type != 'reconciliation_metadata':
        return 'manual', {
            'reconciliation_release': 'Choose the intended release first',
            'reconciliation_unresolved': 'The track assignment is not decisive',
            'reconciliation_intent': 'Choose whether the album should be complete',
            'reconciliation_missing': 'Confirm that the absent track is wanted',
            'reconciliation_numbering': 'Choose a track numbering convention',
        }.get(finding_type, 'This finding requires a decision')
    if global_blocked:
        return 'blocked', 'The selected release must be resolved first'
    if details.get('plan_approved') or details.get('plan_keep'):
        return 'ready', 'Saved in the album plan; no file changes applied yet'
    if (details.get('title_difference_kind') in ('material', 'version') and
            _TITLE_CHANGE_KEYS.intersection(changes)):
        return 'manual', 'The title may identify a different song or recording version'
    if _POSITION_CHANGE_KEYS.intersection(changes):
        return 'manual', 'The release uses a different disc or track numbering scheme'
    if details.get('confidence') not in (None, '', 'clear'):
        return 'manual', 'The file-to-track assignment is not clear'
    if _replaces_artist_credit(changes):
        return 'manual', 'Confirm the artist credit before replacing an existing credit'
    return 'ready', 'Audio and release evidence support this metadata repair'


def _position_change_sql(details: str) -> str:
    return '(' + ' OR '.join(
        f"json_type({details}, '$.changes.{field}') IS NOT NULL"
        for field in sorted(_POSITION_CHANGE_KEYS)) + ')'


def _metadata_review_sql(details: str, error: str, *, artist_credit: bool = True) -> str:
    """SQL projection of the same approval requirements shown in the album plan."""
    alias = error.rsplit('.', 1)[0] + '.' if '.' in error else 'repair_findings.'
    saved = ("SELECT 1 FROM reconcile_review_choices choice WHERE "
             f"choice.finding_id = {alias}id AND choice.case_id = {alias}review_case_key "
             f"AND choice.evidence_json = {alias}details_json")
    approved = f"EXISTS ({saved} AND choice.choice = 'fix')"
    kept = f"EXISTS ({saved} AND choice.choice = 'dismiss' AND choice.category != 'numbering')"
    kept_numbers = f"EXISTS ({saved} AND choice.choice = 'dismiss' AND choice.category = 'numbering')"
    predicates = [
        f'({_position_change_sql(details)} AND NOT {kept_numbers})',
        f"COALESCE({error}, '') != ''",
        f"COALESCE(json_extract({details}, '$.confidence'), 'clear') NOT IN ('','clear')",
        f"COALESCE(json_extract({details}, '$.title_difference_kind'), '') IN ('material','version')",
    ]
    if artist_credit:
        predicates.append(_artist_replacement_sql(details))
    return f"(COALESCE({error}, '') != '' OR (NOT ({approved} OR {kept}) AND (" + ' OR '.join(predicates) + ')))'


def _related_finding_sql(alias: str) -> str:
    return (f'{alias}.status = repair_findings.status AND '
            f'{alias}.job_id = repair_findings.job_id AND '
            f'{alias}.review_case_key = repair_findings.review_case_key')


def queue_filter(*, status: str, job_id: str = "", q: str = "", category: str = "", risk: str = "") -> tuple[str, list[Any]]:
    params: list[Any] = [status]
    job_filter = ''
    if job_id:
        job_filter = 'AND job_id = ?'
        params.append(job_id)
    search = ''
    if q.strip():
        search = "AND (title LIKE ? OR file_path LIKE ? OR details_json LIKE ?)"
        needle = f'%{q.strip()}%'
        params.extend([needle] * 3)
    category_conditions = {
        'numbering': ("""finding_type = 'reconciliation_metadata'
            AND COALESCE(json_extract(%s, '$.title_difference_kind'), '')
                NOT IN ('material','version') AND (
            json_type(%s, '$.changes.track_number') IS NOT NULL OR
            json_type(%s, '$.changes.disc_number') IS NOT NULL OR
            json_type(%s, '$.changes.library_track_number') IS NOT NULL OR
            json_type(%s, '$.changes.library_disc_number') IS NOT NULL)""" %
            (_DETAILS, _DETAILS, _DETAILS, _DETAILS, _DETAILS)),
        'title_conventions': ("finding_type = 'reconciliation_metadata' AND "
            "json_extract(%s, '$.title_difference_kind') IN ('typography','flavor') "
            "AND (json_type(%s, '$.changes.title') IS NOT NULL OR "
            "json_type(%s, '$.changes.library_title') IS NOT NULL OR "
            "json_type(%s, '$.changes.filename') IS NOT NULL) "
            "AND json_type(%s, '$.changes.track_number') IS NULL "
            "AND json_type(%s, '$.changes.disc_number') IS NULL "
            "AND json_type(%s, '$.changes.library_track_number') IS NULL "
            "AND json_type(%s, '$.changes.library_disc_number') IS NULL" %
            (_DETAILS, _DETAILS, _DETAILS, _DETAILS, _DETAILS,
             _DETAILS, _DETAILS, _DETAILS)),
        'title_issues': ("json_extract(%s, '$.title_difference_kind') "
            "IN ('material','version') AND ("
            "json_type(%s, '$.changes.title') IS NOT NULL OR "
            "json_type(%s, '$.changes.library_title') IS NOT NULL OR "
            "json_type(%s, '$.changes.filename') IS NOT NULL)" %
            (_DETAILS, _DETAILS, _DETAILS, _DETAILS)),
        'release_conflicts': "finding_type = 'reconciliation_release'",
        'audio_conflicts': ("finding_type = 'reconciliation_unresolved' OR "
            "(finding_type = 'reconciliation_extra' AND "
            "json_extract(%s, '$.audio_conflict') = 'different_recording')" % _DETAILS),
        'album_completeness': (
            "finding_type IN ('reconciliation_intent','reconciliation_missing')"),
        'duplicates': "finding_type = 'reconciliation_duplicate'",
        'extra_files': ("finding_type = 'reconciliation_extra' AND "
            "COALESCE(json_extract(%s, '$.audio_conflict'), '') != 'different_recording'" % _DETAILS),
        'metadata': ("finding_type = 'reconciliation_metadata' AND "
                     "NOT (COALESCE(json_extract(%s, '$.title_difference_kind'), '') "
                     "IN ('typography','flavor','material','version') AND ("
                     "json_type(%s, '$.changes.title') IS NOT NULL OR "
                     "json_type(%s, '$.changes.library_title') IS NOT NULL OR "
                     "json_type(%s, '$.changes.filename') IS NOT NULL)) AND "
                     "json_type(%s, '$.changes.track_number') IS NULL AND "
                     "json_type(%s, '$.changes.disc_number') IS NULL AND "
                     "json_type(%s, '$.changes.library_track_number') IS NULL AND "
                     "json_type(%s, '$.changes.library_disc_number') IS NULL" %
                     (_DETAILS, _DETAILS, _DETAILS, _DETAILS,
                      _DETAILS, _DETAILS, _DETAILS, _DETAILS)),
    }
    if category in category_conditions:
        search += f' AND ({category_conditions[category]})'
    manual = _metadata_review_sql(_DETAILS, 'last_error')
    blocker_details = "CASE WHEN json_valid(blocker.details_json) THEN blocker.details_json ELSE '{}' END"
    blockers = _metadata_review_sql(blocker_details, 'blocker.last_error')
    related = _related_finding_sql('blocker')
    if risk == 'ready':
        search += (" AND finding_type = 'reconciliation_metadata' AND NOT " + manual +
                   " AND NOT EXISTS (SELECT 1 FROM repair_findings blocker WHERE " + related +
                   " AND (blocker.finding_type != 'reconciliation_metadata' OR " + blockers + '))')
    elif risk == 'destructive':
        search += " AND finding_type IN ('reconciliation_extra','reconciliation_duplicate')"
    elif risk == 'manual':
        search += (
            ' AND NOT EXISTS (SELECT 1 FROM repair_findings file_action WHERE ' +
            _related_finding_sql('file_action') +
            " AND file_action.finding_type IN ('reconciliation_extra','reconciliation_duplicate')) "
            "AND (finding_type NOT IN ('reconciliation_metadata','reconciliation_extra',"
            "'reconciliation_duplicate') OR " + manual +
            " OR (finding_type = 'reconciliation_metadata' AND EXISTS ("
            'SELECT 1 FROM repair_findings blocker WHERE ' + related +
            " AND blocker.finding_type != 'reconciliation_metadata'))) ")
    where = f"status = ? {job_filter} {search}"
    return where, params

def count_ready_findings(conn, *, status: str, job_id: str = "") -> int:
    from core.library.reconciliation_choices import ensure_schema
    ensure_schema(conn)
    ready_total = 0
    if not job_id or job_id == 'library_reconciliation':
        ready_params: list[Any] = [status]
        ready_job_filter = ''
        if job_id:
            ready_job_filter = 'AND f.job_id = ?'
            ready_params.append(job_id)
        ready_total = conn.execute(f"""
            SELECT COUNT(*) FROM repair_findings f
            WHERE f.status = ? {ready_job_filter}
              AND f.finding_type = 'reconciliation_metadata'
              AND NOT {_metadata_review_sql(
                  "CASE WHEN json_valid(f.details_json) THEN f.details_json ELSE '{}' END",
                  'f.last_error')}
              AND NOT EXISTS (
                  SELECT 1 FROM repair_findings blocker
                  WHERE blocker.status = f.status
                    AND blocker.job_id = f.job_id
                    AND blocker.review_case_key = f.review_case_key
                    AND blocker.finding_type = 'reconciliation_release'
              )
        """, ready_params).fetchone()[0]
    return ready_total
