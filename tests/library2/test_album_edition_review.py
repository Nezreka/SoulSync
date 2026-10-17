"""Edition review scores owned files and only pins after explicit approval."""

import json

import pytest

from core.repair_jobs.base import JobContext


class Config:
    def __init__(self, **settings):
        self.settings = settings

    def get(self, key, default=None):
        return self.settings if key.endswith('.settings') else default


def album_and_files(conn):
    album = conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0]
    conn.execute('UPDATE lib2_albums SET spotify_id=?,external_ids=? WHERE id=?',
                 ('deluxe', json.dumps({'deezer': 'standard'}), album))
    missing = conn.execute("SELECT id FROM lib2_tracks WHERE album_id=? AND title='Hotline Bling'",
                           (album,)).fetchone()[0]
    conn.execute('INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)', (missing, '/m/02.flac'))
    conn.commit()
    return album


def catalogue(source, release_id):
    tracks = [
        {'title': 'One Dance', 'duration_ms': 200000},
        {'title': 'Hotline Bling', 'duration_ms': 180000},
    ]
    if release_id == 'deluxe':
        tracks += [{'title': 'Bonus', 'duration_ms': 240000}]
    return tracks


def test_native_edition_review_scores_owned_tracks_not_whole_catalogue(imported_conn, legacy_db):
    from core.library2.edition_review import propose_album_edition

    album = album_and_files(imported_conn)
    imported_conn.execute('INSERT INTO lib2_tracks(album_id,title) VALUES(?,?)', (album, 'Missing extra'))
    imported_conn.commit()

    proposal = propose_album_edition(legacy_db, Config(), album, mode='best_fit',
                                    source_order=('spotify', 'deezer'), fetch_tracklist=catalogue,
                                    fetch_alternates=lambda *a, **kw: [])

    assert proposal['source'] == 'deezer'
    assert proposal['album_id'] == 'standard'
    assert proposal['file_track_count'] == 2
    assert proposal['lib2_album_id'] == album
    assert imported_conn.execute('SELECT canonical_album_id FROM lib2_albums WHERE id=?', (album,)).fetchone()[0] is None


def test_pinned_album_is_not_proposed_again(imported_conn, legacy_db):
    from core.library2.edition_review import propose_album_edition
    from core.library2.editions import pin_album_release

    album = album_and_files(imported_conn)
    pin_album_release(imported_conn.cursor(), album, 'spotify', 'manual')
    imported_conn.commit()
    assert propose_album_edition(legacy_db, Config(), album, fetch_tracklist=catalogue) is None


def test_the_automatic_pin_already_in_place_is_no_suggestion(imported_conn, legacy_db):
    from core.library2.edition_review import propose_album_edition

    album = album_and_files(imported_conn)
    imported_conn.execute("UPDATE lib2_albums SET canonical_source='deezer',canonical_album_id='standard',"
                          "canonical_locked=0 WHERE id=?", (album,))
    imported_conn.commit()
    assert propose_album_edition(legacy_db, Config(), album, mode='best_fit', source_order=('spotify', 'deezer'),
                                 fetch_tracklist=catalogue, fetch_alternates=lambda *a, **kw: []) is None


def test_artist_scoped_review_weighs_every_owned_track_of_the_release(imported_conn, legacy_db, monkeypatch):
    from core.repair_jobs import get_all_jobs
    from core.repair_jobs.base import build_artist_file_scope

    album = album_and_files(imported_conn)
    guest = imported_conn.execute("INSERT INTO lib2_artists(name) VALUES('Guest')").lastrowid
    hotline = imported_conn.execute("SELECT id FROM lib2_tracks WHERE album_id=? AND title='Hotline Bling'",
                                    (album,)).fetchone()[0]
    imported_conn.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (hotline,))
    imported_conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id,role,position) VALUES(?,?,'primary',0)",
                          (hotline, guest))
    imported_conn.commit()
    artist = imported_conn.execute('SELECT primary_artist_id FROM lib2_albums WHERE id=?', (album,)).fetchone()[0]
    monkeypatch.setattr('core.library2.edition_review.fetch_release_tracklist', catalogue)
    monkeypatch.setattr('core.library2.edition_review.fetch_alternative_releases', lambda *a, **kw: [])
    findings = []
    get_all_jobs()['album_edition_review']().scan(JobContext(
        legacy_db, '/m', Config(source_selection='best_fit'), scope=build_artist_file_scope(legacy_db, artist),
        create_finding=lambda **kw: findings.append(kw) or True))
    proposal = next(f['details'] for f in findings if f['entity_id'] == f'lib2:{album}')
    assert proposal['file_track_count'] == 2 and len(proposal['owned_track_ids']) == 2


def test_editions_job_is_findings_only_even_with_old_auto_apply_settings(
        imported_conn, legacy_db, monkeypatch):
    from core.repair_jobs import get_all_jobs

    album = album_and_files(imported_conn)
    monkeypatch.setattr('core.library2.edition_review.fetch_release_tracklist', catalogue)
    monkeypatch.setattr('core.library2.match_status.configured_services', lambda: {'spotify', 'deezer'})
    monkeypatch.setattr('core.library2.edition_review.fetch_alternative_releases', lambda *a, **kw: [])
    findings = []
    cfg = Config(dry_run=False, auto_apply=True, source_selection='best_fit')
    result = get_all_jobs()['album_edition_review']().scan(JobContext(
        legacy_db, '/m', cfg, create_finding=lambda **kw: findings.append(kw) or True))

    assert result.auto_fixed == 0
    assert any(f['entity_id'] == f'lib2:{album}' for f in findings)
    assert imported_conn.execute('SELECT canonical_album_id FROM lib2_albums WHERE id=?', (album,)).fetchone()[0] is None


def test_approved_proposal_uses_manual_pin_contract_and_rejects_stale_pin(imported_conn, legacy_db):
    from core.library2.edition_review import propose_album_edition, apply_edition_proposal

    album = album_and_files(imported_conn)
    proposal = propose_album_edition(legacy_db, Config(), album, mode='best_fit',
                                    source_order=('spotify', 'deezer'), fetch_tracklist=catalogue,
                                    fetch_alternates=lambda *a, **kw: [])
    result = apply_edition_proposal(legacy_db, f'lib2:{album}', proposal)
    assert result['success']
    pinned = imported_conn.execute(
        'SELECT canonical_source,canonical_album_id,canonical_locked FROM lib2_albums WHERE id=?', (album,)
    ).fetchone()
    assert tuple(pinned) == ('deezer', 'standard', 1)
    assert not apply_edition_proposal(legacy_db, f'lib2:{album}', {**proposal, 'album_id': 'other'})['success']


def test_legacy_numeric_subject_is_never_applied_as_native_album(imported_conn, legacy_db):
    from core.library2.edition_review import apply_edition_proposal

    album = album_and_files(imported_conn)
    result = apply_edition_proposal(legacy_db, str(album), {'source': 'spotify', 'album_id': 'other'})
    assert not result['success']
    assert imported_conn.execute('SELECT canonical_album_id FROM lib2_albums WHERE id=?', (album,)).fetchone()[0] is None


def test_best_fit_compares_known_alternative_even_when_linked_edition_clears_floor(imported_conn, legacy_db):
    from core.library2.edition_review import propose_album_edition
    from core.library2.editions import record_alternative_edition

    album = album_and_files(imported_conn)
    imported_conn.execute("UPDATE lib2_albums SET external_ids='{}' WHERE id=?", (album,))
    record_alternative_edition(imported_conn.cursor(), album, source='spotify', provider_id='standard',
                               title='Views', track_count=2)
    imported_conn.commit()
    result = propose_album_edition(legacy_db, Config(), album, mode='best_fit',
                                   source_order=('spotify',), fetch_tracklist=catalogue,
                                   fetch_alternates=lambda *a, **kw: [])
    assert result['album_id'] == 'standard'
    assert result['score'] == 1.0


def test_changed_owned_tracks_require_new_edition_review(imported_conn, legacy_db):
    from core.library2.edition_review import propose_album_edition, apply_edition_proposal

    album = album_and_files(imported_conn)
    proposal = propose_album_edition(legacy_db, Config(), album, mode='best_fit',
                                    source_order=('spotify', 'deezer'), fetch_tracklist=catalogue,
                                    fetch_alternates=lambda *a, **kw: [])
    imported_conn.execute("UPDATE lib2_track_files SET file_state='deleted' WHERE path='/m/02.flac'")
    imported_conn.commit()
    result = apply_edition_proposal(legacy_db, f'lib2:{album}', proposal)
    assert not result['success']
    assert imported_conn.execute('SELECT canonical_album_id FROM lib2_albums WHERE id=?', (album,)).fetchone()[0] is None


def test_confirming_edition_invalidates_previous_ready_catalogue(imported_conn, legacy_db):
    from core.library2.edition_review import propose_album_edition, apply_edition_proposal

    album = album_and_files(imported_conn)
    imported_conn.execute("UPDATE lib2_albums SET tracklist_status='ready',tracklist_json=? WHERE id=?",
                          (json.dumps(catalogue('spotify', 'deluxe')), album))
    imported_conn.commit()
    proposal = propose_album_edition(legacy_db, Config(), album, mode='best_fit',
                                    source_order=('spotify', 'deezer'), fetch_tracklist=catalogue,
                                    fetch_alternates=lambda *a, **kw: [])
    assert apply_edition_proposal(legacy_db, f'lib2:{album}', proposal)['success']
    row = imported_conn.execute('SELECT tracklist_status,tracklist_json FROM lib2_albums WHERE id=?', (album,)).fetchone()
    assert tuple(row) == ('idle', None)


def test_confirmed_source_cannot_reuse_other_providers_durable_snapshot(
        imported_conn, legacy_db, monkeypatch):
    from core.library2.completeness import load_album_catalogue
    from core.library2.provider_adapters import TracklistProviderResult, TracklistTrack
    from core.library2.editions import pin_album_release
    from core.library2.match_status import configured_services

    album = album_and_files(imported_conn)
    monkeypatch.setattr('core.library2.match_status.configured_services', lambda: set())
    monkeypatch.setattr('core.library2.track_reconcile_trigger.schedule_album_track_reconcile', lambda *a, **kw: None)
    deluxe = TracklistProviderResult('spotify', 'deluxe', tuple(
        TracklistTrack(t['title'], i, 1, t['duration_ms'], 'spotify', f's{i}')
        for i, t in enumerate(catalogue('spotify', 'deluxe'), 1)))
    load_album_catalogue(legacy_db, Config(), imported_conn, album, enrich=False, provider_result=deluxe)
    pin_album_release(imported_conn.cursor(), album, 'deezer', 'standard')
    imported_conn.commit()
    requests = []
    def exact(ids, **kwargs):
        requests.append(ids)
        return (TracklistProviderResult('deezer', 'standard', tuple(
            TracklistTrack(t['title'], i, 1, t['duration_ms'], 'deezer', f'd{i}')
            for i, t in enumerate(catalogue('deezer', 'standard'), 1))),)
    monkeypatch.setattr('core.library2.provider_adapters.fetch_matched_album_tracklists', exact)

    tracks = load_album_catalogue(legacy_db, Config(), imported_conn, album)

    assert requests == [{'deezer': 'standard'}]
    assert [t['title'] for t in tracks] == ['One Dance', 'Hotline Bling']
