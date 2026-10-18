"""Review decision policy shared by queue readers and action selection.

Queue buckets and execution use the same effective album plan. Individual
proposals are executable only after every required album decision is settled.
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


def supported_version_metadata(details: dict) -> bool:
    """An exact fingerprint-backed recording can supply omitted version text."""
    if details.get('title_difference_kind') != 'version' or details.get('confidence') != 'clear':
        return False
    if details.get('audio_conflict') or details.get('version_conflict'):
        return False
    expected = details.get('recording_mbid')
    tags = details.get('current_tags') or {}
    local, remote = tags.get('duration_ms'), details.get('expected_duration_ms')
    if not expected or not local or not remote or abs(float(local) - float(remote)) > 3000:
        return False
    return any(row.get('mbid') == expected and float(row.get('score') or 0) >= .9
               for row in (details.get('acoustid') or {}).get('recordings') or [])


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
            _TITLE_CHANGE_KEYS.intersection(changes) and not supported_version_metadata(details)):
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


def queue_filter(*, conn, status: str, job_id: str = "", q: str = "", category: str = "", risk: str = "") -> tuple[str, list[Any]]:
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
    if risk:
        modes = effective_case_modes(conn, status=status, job_id=job_id)
        keys = [key for key, value in modes.items() if value['mode'] == risk]
        search += ' AND review_case_key IN (' + ','.join('?' for _ in keys) + ')' if keys else ' AND 0'
        params.extend(keys)
    where = f"status = ? {job_filter} {search}"
    return where, params

def case_mode(items: list[dict]) -> str:
    required = [item for item in items if finding_category(item) != 'pending_fulfillment']
    unresolved = [item for item in required if item.get('review_tier') != 'ready']
    if any(item.get('review_tier') == 'destructive' for item in unresolved):
        return 'destructive'
    return 'manual' if unresolved else 'ready' if required else 'waiting'


def effective_case_modes(conn, *, status: str = 'pending', job_id: str = '') -> dict:
    """One classification for counts, filtering and execution, including draft choices."""
    import json
    from core.library import reconciliation_choices as choices
    choices.ensure_schema(conn)
    saved = choices.load_choices(conn)
    grouped = {}
    for fid, key, kind, error, raw in conn.execute(
            "SELECT id, review_case_key, finding_type, last_error, details_json FROM repair_findings "
            "WHERE status = ? AND (? = '' OR job_id = ?)", (status, job_id, job_id)):
        try:
            details = json.loads(raw or '{}')
        except (TypeError, ValueError):
            details = {}
        details = choices.project(details if isinstance(details, dict) else {}, saved.get(fid), case_id=key, raw_json=raw)
        grouped.setdefault(key, []).append({'id': fid, 'finding_type': kind, 'details': details, 'last_error': error})
    result = {}
    for key, items in grouped.items():
        blocked = any(item['finding_type'] in _GLOBAL_BLOCKER_TYPES for item in items)
        for item in items:
            item['review_tier'], _ = _finding_tier(item, global_blocked=blocked)
        required = [item for item in items if finding_category(item) != 'pending_fulfillment']
        unresolved = [item for item in required if item['review_tier'] != 'ready']
        ready = [item['id'] for item in required if item['review_tier'] == 'ready'] if not unresolved else []
        mode = case_mode(items)
        result[key] = {'mode': mode, 'ready_ids': ready, 'items': items}
    return result


def count_ready_findings(conn, *, status: str, job_id: str = "") -> int:
    return sum(len(case['ready_ids']) for case in effective_case_modes(conn, status=status, job_id=job_id).values())
