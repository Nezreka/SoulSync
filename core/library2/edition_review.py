"""Native edition review adapters around the existing canonical release scorer."""

from contextlib import closing

from core.library2.editions import album_release_ids, pin_album_release
from core.library2.maintenance_subjects import active_file_subjects, subject_details
from core.metadata.canonical_resolver import (
    VALID_MODES, default_fetch_alternates, provider_artist_id,
    resolve_canonical_for_album,
)
from core.repair_jobs.base import hand_tagged_path_keys, is_hand_tagged_path


def fetch_release_tracklist(source, release_id):
    """Exact release lookup: no title search or cross-provider ID substitution."""
    from core.library2.provider_adapters import fetch_matched_album_tracklists
    results = fetch_matched_album_tracklists({source: release_id}, source_order=(source,))
    for result in results:
        if result.provider == source and result.provider_entity_id == release_id and result.is_complete:
            return [dict(track.to_payload(), duration_ms=track.duration_ms)
                    for track in result.tracks]
    return None


def fetch_alternative_releases(source, release_id, **context):
    return default_fetch_alternates(source, release_id, **context)


def propose_album_edition(database, config_manager, album_id, *, mode='active_preferred',
                          min_score=0.5, source_order=None, fetch_tracklist=None,
                          fetch_alternates=None, file_subjects=None):
    """Compare owned tracks, without writing a canonical selection or pin."""
    from core.library2.provider_adapters import configured_entity_source_order
    from core.library2.completeness import _album_tracklist_context, _snapshot_tracks
    from core.library2.provider_snapshots import get_provider_snapshot

    subjects = file_subjects if file_subjects is not None else active_file_subjects(database, config_manager)
    subjects = [s for s in subjects if int(s['album_id']) == int(album_id)
                and s.get('import_status', 'imported') == 'imported']
    if not subjects:
        return None
    keys = hand_tagged_path_keys(database)
    if any(is_hand_tagged_path(s.get('path'), keys) for s in subjects):
        return None
    # Additional formats and multiple libraries are files of the same track,
    # not extra release positions for the count-fit signal.
    by_track = {int(s['track_id']): s for s in subjects}
    file_tracks = [{'title': s.get('title') or '', 'duration_ms': s.get('duration')}
                   for s in by_track.values()]
    with closing(database._get_connection()) as conn:
        album = conn.execute('SELECT * FROM lib2_albums WHERE id=?', (int(album_id),)).fetchone()
        if album is None or album['canonical_locked']:
            return None
        source_ids = album_release_ids(album)
        reference_context = _album_tracklist_context(conn, album_id)
        cached = {}
        if reference_context:
            for source, release_id in source_ids.items():
                snapshot = get_provider_snapshot(conn, provider=source, entity_type='album',
                                                 entity_id=album_id, scope='tracklist')
                tracks = _snapshot_tracks(snapshot, reference_context[1])
                if tracks and snapshot.is_complete and snapshot.provider_entity_id == release_id:
                    cached[(source, release_id)] = [
                        dict(t, duration_ms=t.get('duration_ms', t.get('duration'))) for t in tracks]
        stored_alternatives = {}
        for edition in conn.execute('SELECT * FROM lib2_release_editions WHERE release_group_id=? AND is_default=0',
                                    (album_id,)):
            from core.library2.provider_ids import source_ids_from_values
            ids = source_ids_from_values(spotify_id=edition['spotify_id'],
                                        musicbrainz_id=edition['musicbrainz_id'], external_ids=edition['external_ids'])
            tracks = [dict(row) for row in conn.execute(
                'SELECT COALESCE(rt.title_override,r.title) AS title, '
                'COALESCE(rt.duration,r.duration) AS duration_ms FROM lib2_release_tracks rt '
                'JOIN lib2_recordings r ON r.id=rt.recording_id WHERE rt.release_edition_id=? '
                'ORDER BY COALESCE(rt.disc_number,1),rt.track_number', (edition['id'],))]
            for source, release_id in ids.items():
                stored_alternatives.setdefault(source, []).append({'album_id': release_id,
                    'tracks': tracks if edition['track_count'] and len(tracks) == edition['track_count'] else None})
        title = album['title']
        previous_pin = [album['canonical_source'], album['canonical_album_id']]

    order = list(source_order if source_order is not None else configured_entity_source_order('album', source_ids))
    fetch = fetch_tracklist or fetch_release_tracklist
    alternate_fetch = fetch_alternates or fetch_alternative_releases
    subject = subjects[0]

    def tracklist(source, release_id):
        return cached.get((source, release_id)) or fetch(source, release_id)

    def alternatives(source, linked_id):
        context = {f'artist_{s}_id': value for s, value in (subject.get('artist_source_ids') or {}).items()}
        stored = list(stored_alternatives.get(source, []))
        remote = alternate_fetch(source, linked_id, artist_id=provider_artist_id(context, source),
                                 artist_name=subject.get('artist_name') or '', album_title=title) or []
        return stored + remote

    resolved = resolve_canonical_for_album(
        album_source_ids=source_ids, file_tracks=file_tracks, fetch_tracklist=tracklist,
        source_priority=order, min_score=max(0.0, min(1.0, float(min_score))),
        mode=mode if mode in VALID_MODES else 'active_preferred',
        fetch_alternates=alternatives,
        candidate_editions=[dict(edition, source=source)
                            for source, editions in stored_alternatives.items() for edition in editions],
    )
    # The release already pinned (automatically) is no suggestion to review.
    if not resolved or (str(resolved['source']).lower(), str(resolved['album_id'])) == (
            str(previous_pin[0] or '').lower(), str(previous_pin[1] or '')):
        return None
    return {
        **resolved, **subject_details(subject), 'lib2_album_id': int(album_id),
        'album_title': title, 'artist_name': subject.get('artist_name'),
        'observed_release_ids': source_ids, 'observed_pin': previous_pin,
        'owned_track_ids': sorted(by_track),
        'review_scope': _review_scope(),
    }


def _review_scope():
    from core.library2.sql_util import ambient_scope, ANY_OWNER
    scope = ambient_scope()
    return 'all' if scope is ANY_OWNER or scope is None else scope


def apply_edition_proposal(database, entity_id, proposal):
    """An approved native review uses the same manual pin as an album choice."""
    if not str(entity_id).startswith('lib2:') or not proposal.get('library_v2_native'):
        return {'success': False, 'review_required': True,
                'error': 'Historical edition finding needs a native review; re-run Album Edition Review.'}
    try:
        album_id = int(str(entity_id).split(':', 1)[1])
        if album_id != int(proposal['lib2_album_id']):
            raise ValueError('Album identity changed')
        source, release_id = str(proposal['source']).lower(), str(proposal['album_id'])
        if (source, release_id) not in {(c['source'], str(c['album_id'])) for c in proposal.get('candidates', [])}:
            raise ValueError('Selected release was not part of the reviewed candidates')
        keys = hand_tagged_path_keys(database)
        with closing(database._get_connection()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            album = conn.execute('SELECT * FROM lib2_albums WHERE id=?', (album_id,)).fetchone()
            if album is None or album['canonical_locked']:
                raise ValueError('Album no longer exists or has a manual edition pin')
            if album_release_ids(album) != proposal.get('observed_release_ids'):
                raise ValueError('Release identity changed; review again')
            if [album['canonical_source'], album['canonical_album_id']] != proposal.get('observed_pin'):
                raise ValueError('Canonical selection changed; review again')
            rows = conn.execute('SELECT f.path FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id '
                                "WHERE t.album_id=? AND COALESCE(f.file_state,'active')='active'", (album_id,))
            if any(is_hand_tagged_path(row[0], keys) for row in rows):
                raise ValueError('Hand-tagged album is protected')
            from core.library2.sql_util import owner_clause, ANY_OWNER
            scope = proposal.get('review_scope', _review_scope())
            owner = owner_clause(column='f.owner_profile_id', scope=ANY_OWNER if scope == 'all' else scope)
            current_track_ids = sorted({int(row[0]) for row in conn.execute(
                'SELECT t.id FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id '
                "WHERE t.album_id=? AND COALESCE(f.file_state,'active')='active' "
                "AND COALESCE(f.import_status,'imported')='imported' "
                f"AND TRIM(COALESCE(f.path,''))<>''{owner}", (album_id,))})
            if current_track_ids != proposal.get('owned_track_ids'):
                raise ValueError('Owned tracks changed; review this edition again')
            pin_album_release(conn.cursor(), album_id, source, release_id)
            conn.commit()
        return {'success': True, 'action': 'pinned_canonical',
                'message': f'Pinned reviewed {source} release {release_id}'}
    except (KeyError, TypeError, ValueError) as exc:
        return {'success': False, 'error': str(exc)}
