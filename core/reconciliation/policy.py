"""Review decision policy shared by queue readers and action selection.

Queue buckets and execution use the same effective album plan. Individual
proposals are executable only after every required album decision is settled.
"""
from __future__ import annotations
from typing import Any

_DETAILS = "CASE WHEN json_valid(details_json) THEN details_json ELSE '{}' END"


_DESTRUCTIVE_TYPES = frozenset({
    'reconciliation_extra', 'reconciliation_duplicate',
})
_GLOBAL_BLOCKER_TYPES = frozenset({'reconciliation_release'})
_POSITION_CHANGE_KEYS = frozenset({
    'track_number', 'disc_number', 'total_discs', 'library_track_number', 'library_disc_number',
})
_TRACK_ARTIST_CHANGE_KEYS = frozenset({
    'track_artist', 'library_track_artist',
})
_RECORDING_ID_CHANGE_KEYS = frozenset({
    'musicbrainz_recording_id', 'library_recording_mbid',
})


def _replaces_artist_credit(changes: dict[str, Any], details: dict | None = None) -> bool:
    """An unrelated nonempty credit needs a person, even on a clear match."""
    for field in (*_TRACK_ARTIST_CHANGE_KEYS, 'artist'):
        change = changes.get(field)
        old, new = _change_value(change, 'old'), _change_value(change, 'new')
        if old and new and old.casefold() != new.casefold():
            from core.reconciliation.metadata import credit_preserves_artists
            evidence = details or {}
            # `artist` is the album-artist tag, not the performer credit. A
            # generic compilation label can be corrected on a supported
            # single-artist release without replacing any track performer.
            if (field == 'artist' and old.casefold() in ('various artists', 'various')
                    and evidence.get('confidence') in ('clear', 'manual')
                    and evidence.get('assignment_evidence') in ('audio', 'user')
                    and evidence.get('expected_artist_names') == [new]):
                continue
            if (evidence.get('confidence') in ('clear', 'manual')
                    and evidence.get('assignment_evidence') in ('audio', 'user')
                    and credit_preserves_artists(old, new,
                        evidence.get('expected_artist_names') or [],
                        evidence.get('artist_aliases') or [])):
                continue
            return True
    return False


def _change_value(change: Any, key: str) -> str:
    value = change.get(key) if isinstance(change, dict) else None
    return str(value or '').strip()


def _changes_title_text(changes: dict) -> bool:
    """A filename's position prefix alone is not a title or version change."""
    if {'title', 'library_title'}.intersection(changes):
        return True
    if 'filename' not in changes:
        return False
    import os
    from core.text.track_prefix import parse_track_prefix
    def title(key):
        stem = os.path.splitext(_change_value(changes['filename'], key))[0]
        return parse_track_prefix(stem).remainder
    return title('old') != title('new')


def needs_numbering_review(details: dict) -> bool:
    if details.get('numbering_choice') in ('fix', 'dismiss'):
        return False
    changes = details.get('changes') or {}
    positions = _POSITION_CHANGE_KEYS.intersection(changes)
    if not positions:
        return False
    if details.get('numbering_recovery'):
        return True
    if details.get('routine_numbering') and details.get('confidence') in ('clear', 'manual'):
        return False
    # A fully assigned continuous sequence already has its final track numbers.
    # Clearing redundant disc markers is metadata normalization, not renumbering.
    normalized_disc = (positions <= {'disc_number', 'library_disc_number', 'total_discs'}
        and details.get('confidence') == 'clear'
        and details.get('release_disc_count') == 1
        and (details.get('local_file_count') or 0) - (details.get('companion_count') or 0) == details.get('release_track_count')
        and bool(details.get('release_track_count'))
        and all(str(_change_value(changes[key], 'new')) == '1' for key in positions))
    return not normalized_disc


def finding_category(item: dict[str, Any]) -> str:
    """User-facing reconciliation category, independent of scanner wording."""
    finding_type = str(item.get('finding_type') or '')
    details = item.get('details') if isinstance(item.get('details'), dict) else {}
    changes = details.get('changes') if isinstance(details.get('changes'), dict) else {}
    if item.get('last_error') and (details.get('plan_approved') or details.get('plan_keep')):
        return 'apply_blockers'
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
        return 'folder_conflicts' if details.get('folder_issue') else 'audio_conflicts'
    # A possibly different recording version is the controlling decision even
    # when its atomic repair also carries track-number or filename changes.
    # The grouped numbering button must never conceal a material title edit.
    if (details.get('title_difference_kind') in ('material', 'version') and
            _changes_title_text(changes) and not supported_version_metadata(details)):
        return 'title_issues'
    if needs_numbering_review(details):
        return 'numbering'
    if (details.get('routine_numbering') and _POSITION_CHANGE_KEYS.intersection(changes)
            and not details.get('numbering_choice') and _finding_tier(item)[0] == 'ready'):
        return 'numbering'
    if (details.get('title_difference_kind') in ('typography', 'flavor', 'cosmetic') and
            {'title', 'library_title'}.intersection(changes)):
        return 'title_conventions'
    return 'metadata'


def supported_version_metadata(details: dict) -> bool:
    """An exact fingerprint-backed recording can supply omitted version text."""
    if details.get('title_difference_kind') != 'version' or details.get('confidence') != 'clear':
        return False
    if details.get('audio_conflict') or details.get('version_conflict'):
        return False
    audio = details.get('acoustid') or {}
    if audio.get('status') not in (None, 'ok'):
        return False
    expected = details.get('recording_mbid')
    tags = details.get('current_tags') or {}
    local, remote = tags.get('duration_ms'), details.get('expected_duration_ms')
    if not expected or not local or not remote or abs(float(local) - float(remote)) > 3000:
        return False
    from core.matching.audio_verification import _detect_title_version
    expected_version = _detect_title_version(details.get('expected_title') or '')
    candidates = audio.get('recordings') or []
    for row in candidates:
        version = _detect_title_version(row.get('title') or '')
        if (float(row.get('score') or 0) >= .9 and version != 'original'
                and expected_version != 'original' and version != expected_version):
            return False
    return any(row.get('mbid') == expected and float(row.get('score') or 0) >= .9
               for row in candidates)


def supported_cosmetic_metadata(details: dict) -> bool:
    """Numeral/word formatting needs exact recording and close length support."""
    tags = details.get('current_tags') or {}
    local, remote = tags.get('duration_ms'), details.get('expected_duration_ms')
    expected = details.get('recording_mbid')
    return bool(expected and local and remote
        and abs(float(local) - float(remote)) <= 3000
        and details.get('confidence') == 'clear'
        and not details.get('audio_conflict') and not details.get('version_conflict')
        and (details.get('acoustid') or {}).get('status') == 'ok'
        and any(row.get('mbid') == expected and float(row.get('score') or 0) >= .9
                for row in (details.get('acoustid') or {}).get('recordings') or []))


def _finding_tier(item: dict[str, Any], *, global_blocked: bool = False) -> tuple[str, str]:
    """Classify one reconciliation action for review and bulk application."""
    finding_type = str(item.get('finding_type') or '')
    details = item.get('details') if isinstance(item.get('details'), dict) else {}
    changes = details.get('changes') if isinstance(details.get('changes'), dict) else {}
    if item.get('last_error'):
        return 'blocked', 'The saved action needs attention before it can be applied'
    if (finding_type == 'reconciliation_missing' and
            details.get('fulfillment_state') in ('wishlisted', 'downloading', 'downloaded')):
        return 'blocked', ('Downloaded; waiting for a verified library assignment' if details.get('fulfillment_state') == 'downloaded' else 'Another copy is already on the wishlist' if details.get('replacement_request') else 'The missing track is already on the wishlist')
    local = details.get('file_duration_ms') or (details.get('current_tags') or {}).get('duration_ms') or 0
    release = details.get('expected_duration_ms') or 0
    if (finding_type in ('reconciliation_unresolved', 'reconciliation_metadata') and local and release
            and abs(local - release) >= 30000 and abs(local - release) / release >= .2
            and details.get('confidence') != 'manual' and not (details.get('plan_approved') or details.get('plan_keep'))):
        return 'destructive', 'Large length mismatch; listen and verify the release before accepting'
    if finding_type == 'reconciliation_unresolved' and (details.get('plan_approved') or details.get('plan_keep')):
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
    if (details.get('numbering_mode') == 'digital' and
            (details.get('release_disc_count') or 0) > 1 and
            _POSITION_CHANGE_KEYS.intersection(changes)):
        return 'manual', 'Reanalyze this album to replace the old flattened numbering proposal'
    if details.get('plan_approved') or details.get('plan_keep'):
        return 'ready', 'Saved in the album plan; no file changes applied yet'
    if details.get('metadata_review_reason'):
        return 'manual', str(details['metadata_review_reason'])
    if ((details.get('acoustid') or {}).get('status') == 'fingerprint_error'
            and set(changes) - {'library_album_title'}):
        return 'manual', 'Audio could not be decoded; review before changing this file'
    if (details.get('title_difference_kind') in ('material', 'version') and
            _changes_title_text(changes) and not supported_version_metadata(details)):
        return 'manual', 'The title may identify a different song or recording version'
    if (details.get('title_difference_kind') == 'cosmetic'
            and _changes_title_text(changes)
            and not supported_cosmetic_metadata(details)):
        return 'manual', 'Confirm title formatting without an exact audio match'
    if needs_numbering_review(details):
        if details.get('numbering_recovery'):
            return 'manual', 'Review the recovery of previously flattened disc and track numbers'
        return 'manual', 'The release uses a different disc or track numbering scheme'
    if details.get('confidence') not in (None, '', 'clear', 'manual'):
        return 'manual', 'The file-to-track assignment is not clear'
    if _replaces_artist_credit(changes, details):
        return 'manual', 'Confirm the artist credit before replacing an existing credit'
    if details.get('assignment_evidence') == 'audio':
        return 'ready', 'Audio and release evidence support this metadata repair'
    if details.get('assignment_evidence') == 'user':
        return 'ready', 'The assignment was confirmed during review'
    return 'ready', 'Metadata supports this repair; audio identity is not independently confirmed'


def queue_filter(*, conn, case_modes=None, search_case_ids=None, status: str, job_id: str = "", q: str = "", category: str = "", risk: str = "") -> tuple[str, list[Any]]:
    params: list[Any] = [status]
    job_filter = ''
    if job_id:
        job_filter = 'AND job_id = ?'
        params.append(job_id)
    search = ''
    if q.strip():
        import json
        from core.reconciliation.search import matching_cases
        search = 'AND review_case_key IN (SELECT value FROM json_each(?))'
        params.append(json.dumps(search_case_ids if search_case_ids is not None else
                                 matching_cases(conn, status=status, job_id=job_id, query=q)))
    if category:
        import json
        modes = case_modes if case_modes is not None else effective_case_modes(conn, status=status, job_id=job_id, compact=True)
        categories = {category, 'pending_fulfillment'} if category == 'album_completeness' else {category}
        keys = [key for key, value in modes.items() if any(finding_category(item) in categories for item in value['items'])]
        search += ' AND review_case_key IN (SELECT value FROM json_each(?))'
        params.append(json.dumps(keys))
    if risk:
        modes = case_modes if case_modes is not None else effective_case_modes(conn, status=status, job_id=job_id, compact=True)
        keys = [key for key, value in modes.items() if value['mode'] == risk]
        import json
        search += ' AND review_case_key IN (SELECT value FROM json_each(?))'
        params.append(json.dumps(keys))
    where = f"status = ? {job_filter} {search}"
    return where, params

def case_mode(items: list[dict]) -> str:
    required = [item for item in items if finding_category(item) != 'pending_fulfillment']
    unresolved = [item for item in required if item.get('review_tier') != 'ready']
    if any(item.get('review_tier') == 'destructive' for item in unresolved):
        return 'destructive'
    if unresolved:
        return 'manual'
    return 'ready' if required else 'waiting' if items else 'ready'


def effective_case_modes(conn, *, status: str = 'pending', job_id: str = '', case_id: str = '', compact: bool = False) -> dict:
    """One classification for counts, filtering and execution, including draft choices."""
    import json
    from core.reconciliation import choices as choices
    choices.ensure_schema(conn)
    saved = {} if compact else choices.load_choices(conn)
    grouped = {}
    # Lists and counters need policy inputs, not thousands of complete release
    # candidates and evidence snapshots. Execution still reads full evidence.
    detail_sql, join = 'f.details_json', ''
    choice_sql = 'NULL, NULL, NULL, NULL'
    if compact:
        fields = ('changes', 'fulfillment_state', 'audio_conflict', 'version_conflict', 'folder_issue',
                  'title_difference_kind', 'confidence', 'numbering_mode',
                  'release_disc_count', 'local_file_count', 'release_track_count',
                  'recording_mbid', 'expected_duration_ms', 'expected_title',
                  'metadata_review_reason', 'assignment_evidence', 'companion_count', 'file_duration_ms',
                  'expected_artist_names', 'artist_aliases', 'routine_numbering', 'numbering_recovery')
        raw_details = "CASE WHEN json_valid(f.details_json) THEN f.details_json ELSE '{}' END"
        pairs = [f"'{field}', json_extract({raw_details}, '$.{field}')" for field in fields]
        pairs += [f"'current_tags', json_object('duration_ms', json_extract({raw_details}, '$.current_tags.duration_ms'))",
                  f"'acoustid', CASE WHEN json_extract({raw_details}, '$.title_difference_kind') IN ('version', 'cosmetic') THEN json_extract({raw_details}, '$.acoustid') ELSE json_object('status', json_extract({raw_details}, '$.acoustid.status')) END"]
        detail_sql = 'json_object(' + ', '.join(pairs) + ')'
        join = ('LEFT JOIN reconcile_review_choices c ON c.finding_id=f.id '
                'AND c.case_id=f.review_case_key AND c.evidence_json=f.details_json')
        choice_sql = 'c.category, c.choice, c.fix_action, c.numbering_choice'
    for fid, key, kind, error, raw, category, choice, action, numbering in conn.execute(
            f"SELECT f.id, f.review_case_key, f.finding_type, f.last_error, {detail_sql}, {choice_sql} "
            f"FROM repair_findings f {join} WHERE f.status = ? AND (? = '' OR f.job_id = ?) "
            "AND (? = '' OR f.review_case_key = ?)", (status, job_id, job_id, case_id, case_id)):
        try:
            details = json.loads(raw or '{}')
        except (TypeError, ValueError):
            details = {}
        if compact:
            details = {k: v for k, v in details.items() if v is not None}
            raw = json.dumps(details)
        saved_choice = (key, raw, category, choice, action, numbering) if compact and choice else saved.get(fid)
        details = choices.project(details if isinstance(details, dict) else {}, saved_choice, case_id=key, raw_json=raw)
        grouped.setdefault(key, []).append({'id': fid, 'finding_type': kind, 'details': details, 'last_error': error})
    result = {}
    for key, items in grouped.items():
        blocked = any(item['finding_type'] in _GLOBAL_BLOCKER_TYPES for item in items)
        for item in items:
            item['review_tier'], _ = _finding_tier(item, global_blocked=blocked)
        mode = case_mode(items)
        ready = [item['id'] for item in items if item['review_tier'] == 'ready'] if mode == 'ready' else []
        result[key] = {'mode': mode, 'ready_ids': ready, 'items': items}
    return result


def count_ready_findings(conn, *, status: str, job_id: str = "", case_modes=None) -> int:
    modes = case_modes if case_modes is not None else effective_case_modes(conn, status=status, job_id=job_id, compact=True)
    return sum(len(case['ready_ids']) for case in modes.values())
