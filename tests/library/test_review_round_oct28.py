import json
import sqlite3
from types import SimpleNamespace

from core import repair_review_search as search
from core.library.reconciliation_evidence import file_identity
from core.library.reconciliation_fulfillment import record_request, published_wishlist_track, recover_published_requests
from core.repair_review_identity import review_case_key
from core.repair_worker import RepairWorker


def test_search_documents_reuse_names_and_invalidate_only_changed_findings(monkeypatch):
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE repair_findings(id INTEGER PRIMARY KEY,title TEXT,details_json TEXT,status TEXT,job_id TEXT,review_case_key TEXT)')
    conn.executemany('INSERT INTO repair_findings VALUES(?,?,?,?,?,?)', [
        (1, 'TRON: Legacy', json.dumps({'artist': 'Daft Punk', 'acoustid': {'recordings': [{'title': 'Excluded'}]}}), 'pending', 'job', 'a'),
        (2, 'Reflections', '{}', 'pending', 'job', 'a'),
        (3, 'Other', '{}', 'resolved', 'job', 'b')])
    original, calls = search.names, []
    monkeypatch.setattr(search, 'names', lambda *args: calls.append(args) or original(*args))
    search.prepare(conn, status='pending', job_id='job')
    assert len(calls) == 2
    assert search.matching_cases(conn, status='pending', job_id='job', query='tron refl') == ['a']
    assert search.matching_cases(conn, status='pending', job_id='job', query='excluded') == []
    search.prepare(conn, status='pending', job_id='job')
    assert len(calls) == 2
    conn.execute("UPDATE repair_findings SET title='New name' WHERE id=1")
    search.prepare(conn, status='pending', job_id='job')
    assert len(calls) == 3
    assert search.matching_cases(conn, status='pending', job_id='job', query='tron') == []
    conn.execute('DELETE FROM repair_findings WHERE id=2')
    assert conn.execute('SELECT COUNT(*) FROM repair_review_search_documents').fetchone()[0] == 1


def test_stale_apply_names_file_and_changed_attribute(tmp_path):
    path = tmp_path / 'Song.flac'
    path.write_bytes(b'audio')
    stat = path.stat()
    details = {'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'file_identity': file_identity(stat)}
    worker = RepairWorker.__new__(RepairWorker)
    assert worker._reconciliation_file_guard(str(path), details) is None
    path.write_bytes(b'changed audio')
    error = worker._reconciliation_file_guard(str(path), details)
    assert 'Song.flac changed after the scan' in error
    assert 'file size' in error and 'Recheck this album' in error


def test_cross_provider_publication_keeps_original_review_request(tmp_path):
    database = str(tmp_path / 'state.db')
    db = SimpleNamespace(_get_connection=lambda: sqlite3.connect(database))
    details = {'album_id': 1, 'album_folder': str(tmp_path), 'profile_id': 1, 'source': 'musicbrainz', 'release_id': 'mb-release'}
    slot = {'source_track_id': 'mb-recording'}
    record_request(db, details, slot)
    path = tmp_path / 'Stolen.flac'
    path.write_bytes(b'audio')
    identity = {'case_id': review_case_key(details, None), 'source': 'musicbrainz', 'release_id': 'mb-release', 'source_track_id': 'mb-recording'}
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE wishlist_tracks(profile_id INTEGER,spotify_track_id TEXT,spotify_data TEXT,source_info TEXT)')
        conn.execute('INSERT INTO wishlist_tracks VALUES(1,?,?,?)', ('itunes-track', json.dumps({'provider': 'itunes', 'id': 'itunes-track', 'album': {'id': 'itunes-album'}}), json.dumps({'review_request': identity})))
    published_wishlist_track(db, 'itunes-track', [2], str(path))
    with db._get_connection() as conn:
        assert conn.execute('SELECT state FROM reconcile_fulfillment').fetchone()[0] == 'requested'
    published_wishlist_track(db, 'itunes-track', [1], str(path))
    with db._get_connection() as conn:
        assert conn.execute('SELECT state,published_path FROM reconcile_fulfillment').fetchone() == ('published', str(path))


def test_legacy_cross_provider_recovery_needs_exact_verified_assignment(tmp_path, monkeypatch):
    import mutagen
    db = SimpleNamespace(_get_connection=lambda: sqlite3.connect(str(tmp_path / 'state.db')))
    path = tmp_path / 'Stolen.flac'
    path.write_bytes(b'audio')
    details = {'album_id': 1, 'album_folder': str(tmp_path), 'profile_id': 1, 'source': 'musicbrainz', 'release_id': 'release'}
    record_request(db, details, {'source_track_id': 'recording'})
    case = review_case_key(details, None)
    monkeypatch.setattr(mutagen, 'File', lambda *a, **kw: {'musicbrainz_trackid': ['recording'], 'musicbrainz_albumid': ['release']})
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE reconcile_album_plans(case_id TEXT,plan_json TEXT)')
        plan = {'source': 'musicbrainz', 'release_id': 'release', 'assignments': [{'source_track_id': 'recording', 'file_path': str(path), 'status': 'uncertain'}]}
        conn.execute('INSERT INTO reconcile_album_plans VALUES(?,?)', (case, json.dumps(plan)))
        conn.execute("INSERT INTO reconcile_fulfillment(case_id,profile_id,album_id,album_folder,source,release_id,source_track_id,state,published_path) VALUES(?,1,'1',?,'itunes','album','track','published',?)", (case, str(tmp_path), str(path)))
        recover_published_requests(conn)
        assert conn.execute("SELECT state FROM reconcile_fulfillment WHERE source='musicbrainz'").fetchone()[0] == 'requested'
        plan['assignments'][0]['status'] = 'confirmed'
        conn.execute('UPDATE reconcile_album_plans SET plan_json=?', (json.dumps(plan),))
        recover_published_requests(conn)
        assert conn.execute("SELECT state,published_path FROM reconcile_fulfillment WHERE source='musicbrainz'").fetchone() == ('published', str(path))


def test_disc_totals_write_all_containers_and_keep_matching_cache_reusable():
    from mutagen.id3 import ID3
    from core.tag_writer import _write_id3, _write_vorbis, _write_mp4
    from core.library.reconciliation_match_cache import input_key
    arguments = [None] * 8 + [1, None]
    id3 = SimpleNamespace(tags=ID3())
    vorbis, mp4 = {}, {}
    _write_id3(id3, *arguments, total_discs=1)
    _write_vorbis(vorbis, *arguments, total_discs=1)
    _write_mp4(mp4, *arguments, total_discs=1)
    assert str(id3.tags['TPOS']) == '1/1'
    assert vorbis['totaldiscs'] == vorbis['disctotal'] == ['1']
    assert mp4['disk'] == [(1, 1)]
    arguments[-2] = 2
    _write_mp4(mp4, *arguments, total_discs=2)
    assert mp4['disk'] == [(2, 2)]
    before = input_key('policy', [{'tags': {'title': 'Song'}}], [{'title': 'Song'}], {}, {})
    after = input_key('policy', [{'tags': {'title': 'Song', 'total_discs': 1}}], [{'title': 'Song', 'audio_copy_of_video': False}], {}, {})
    assert before == after


def test_audio_copy_choice_is_scoped_and_automatic_video_exclusion_remains(tmp_path, monkeypatch):
    from core.library.reconciliation_audio_copy import save_choice, load_choices
    from core.repair_jobs import library_reconciliation as job
    db = SimpleNamespace(_get_connection=lambda: sqlite3.connect(str(tmp_path / 'video.db')))
    save_choice(db, 'album', 'musicbrainz', 'video-release', True)
    choices = load_choices(db)
    assert choices == {'album': ('musicbrainz', 'video-release')}
    monkeypatch.setattr(job, 'get_album_tracks_for_source', lambda *a, **kw: [
        {'name': 'Present Tense', 'track_number': 1, 'disc_number': 1, 'video': True}])
    files = [{'path': 'song.mp3', 'tags': {'title': 'Present Tense'}, 'acoustid': {'recordings': []}}]
    assert job._score_release_candidates(files, [('musicbrainz', 'video-release')], {}) == []
    scored = job._score_release_candidates(files, [('musicbrainz', 'video-release')], {}, audio_copy_release=choices['album'])
    assert len(scored) == 1
    assert scored[0].slots[0]['audio_copy_of_video']
    assert job._score_release_candidates(files, [('musicbrainz', 'another-release')], {}, audio_copy_release=choices['album']) == []
    save_choice(db, 'album', 'musicbrainz', 'audio-release', False)
    assert load_choices(db) == {}


def test_folder_preview_uses_planned_total_and_preserves_audio_disc_two():
    from core.library.reorganize_tag_source import read_album_track_from_file
    loader = lambda _: {'available': True, 'tags': {'title': 'Song', 'artist': 'Artist', 'album': 'Album', 'discnumber': '2/2', 'totaldiscs': '2', 'tracknumber': '13'}}
    album, track, error = read_album_track_from_file('song.flac', read_embedded_tags_fn=loader,
        planned_metadata={'total_discs': 1, 'disc_number': 1})
    assert error is None
    assert album['total_discs'] == 1 and track['disc_number'] == 1
    album, track, error = read_album_track_from_file('song.flac', read_embedded_tags_fn=loader,
        planned_metadata={'total_discs': 2, 'disc_number': 2})
    assert error is None
    assert album['total_discs'] == 2 and track['disc_number'] == 2


def test_new_disc_evidence_retains_choices_only_when_the_proposed_effects_match():
    from core.library.reconciliation_choices import same_annotated_evidence
    original = {'current_tags': {'title': 'Song'}, 'changes': {'title': {'old': 'Song', 'new': 'Song!'}}}
    enriched = {**original, 'current_tags': {'title': 'Song', 'total_discs': 2}}
    assert same_annotated_evidence(original, enriched)
    corrected = {**enriched, 'changes': {**original['changes'], 'total_discs': {'old': 2, 'new': 1}}}
    assert not same_annotated_evidence(original, corrected)


def test_scan_refreshes_legacy_tag_cache_without_repeating_audio_lookup(tmp_path, monkeypatch):
    from core.library.reconciliation_evidence import ReconciliationEvidence
    from core.repair_jobs import library_reconciliation as job
    from core.library import release_identity
    db = SimpleNamespace(_get_connection=lambda: sqlite3.connect(str(tmp_path / 'evidence.db')))
    path = tmp_path / 'Song.flac'
    path.write_bytes(b'audio')
    cache = ReconciliationEvidence(db, None)
    old = {'title': 'Song', 'musicbrainz_release_id': 'release', 'artists_list': ['Artist']}
    cache.read_tags(str(path), lambda _: old)
    reads = []
    monkeypatch.setattr(job, '_read_file_tags', lambda p: reads.append(p) or {**old, 'total_discs': 2})
    monkeypatch.setattr(release_identity, 'read_release_evidence', lambda _: {})
    try:
        assert cache.read_tags(str(path), job._read_reconciliation_tags)['total_discs'] == 2
        assert cache.read_tags(str(path), job._read_reconciliation_tags)['total_discs'] == 2
        assert reads == [str(path)]
        assert cache.metrics['lookups'] == cache.metrics['fingerprints'] == 0
    finally:
        cache.close()
