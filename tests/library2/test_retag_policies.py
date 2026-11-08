"""Retag policies use native temporary catalogues and synthesized audio."""
from types import SimpleNamespace
import shutil
import subprocess

import pytest
from mutagen import File

from core.library2 import retag
from core.repair_jobs.base import JobContext
from core.repair_jobs.library_retag import LibraryRetagJob
from core.repair_worker import RepairWorker
from tests.library2.test_retag import _seed_album_with_files
from tests.library2.test_api_routes import api, _wait_for_job  # noqa: F401


def test_api_preview_and_write_share_retag_policy(api, monkeypatch):
    client, _, ids = api
    options = {'depth': 'full', 'source': 'deezer', 'mode': 'fill_missing',
               'cover_art': 'skip', 'lyrics': 'fetch'}
    calls = []
    monkeypatch.setattr(retag, 'refresh_metadata', lambda *a, **kw: calls.append(('refresh', kw)) or {'refreshed': 1})
    monkeypatch.setattr(retag, 'tag_preview', lambda *a, **kw: calls.append(('preview', kw)) or [])
    monkeypatch.setattr(retag, 'write_tags', lambda *a, **kw: calls.append(('write', kw)) or {'written': 0})
    assert client.get(f"/api/library/v2/albums/{ids['views']}/tag-preview", query_string=options).status_code == 200
    response = client.post('/api/library/v2/tags/write', json={'track_ids': [ids['album_track']], **options})
    assert response.status_code == 200
    assert _wait_for_job(client, response.get_json()['job_id'])['error'] is None
    assert calls[0][1]['source'] == 'deezer'
    for name, kwargs in calls[1:]:
        assert {key: kwargs['options'][key] for key in options} == options, name


def test_interactive_retag_preserves_hand_tagged_file(api, native_audio):
    from contextlib import closing
    from database.music_database import MusicDatabase
    client, db, ids = api
    n = native_audio
    with closing(db._get_connection()) as conn:
        conn.execute('UPDATE lib2_track_files SET path=? WHERE track_id=?', (n.path, ids['album_track']))
        conn.commit()
    db.manual_path_keys = lambda: {MusicDatabase.manual_path_key(n.path)}
    preview = client.get(f"/api/library/v2/albums/{ids['views']}/tag-preview").get_json()
    assert preview['changed_count'] == 0
    assert preview['tracks'][0]['protected'] is True
    response = client.post('/api/library/v2/tags/write', json={
        'track_ids': [ids['album_track']], 'cover_art': 'skip'})
    result = _wait_for_job(client, response.get_json()['job_id'])
    assert result['result']['written'] == 0
    assert File(n.path)['title'] == ['Handwritten file title']


def test_hand_tagged_primary_does_not_hide_unprotected_sibling(native_audio, tmp_path):
    from database.music_database import MusicDatabase
    n = native_audio
    sibling = tmp_path / 'sibling.opus'
    shutil.copy(n.path, sibling)
    n.conn.execute('INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)', (n.track, str(sibling)))
    n.conn.commit()
    keys = {MusicDatabase.manual_path_key(n.path)}
    n.db.manual_path_keys = lambda: keys
    preview = retag.tag_preview(retag.track_contexts(n.conn, [n.track]), hand_tagged_keys=keys)
    assert preview[0]['has_changes'] and preview[0]['file_path'] == str(sibling)
    result = retag.write_tags(n.db, [n.track], embed_cover=False)
    assert result['written'] == result['skipped'] == 1
    assert File(n.path)['title'] == ['Handwritten file title']
    assert File(sibling)['title'] == ['One Dance']


@pytest.mark.parametrize('policy', [{'mode': 'invalid'}, {'source': 'invalid'}, {'lyrics': 'invalid'}])
def test_api_rejects_invalid_retag_policy_before_any_work(api, monkeypatch, policy):
    client, _, ids = api
    monkeypatch.setattr(retag, 'refresh_metadata', lambda *a, **kw: pytest.fail('invalid policy must not refresh'))
    assert client.get(f"/api/library/v2/albums/{ids['views']}/tag-preview", query_string=policy).status_code == 400
    assert client.post('/api/library/v2/tags/write', json={'track_ids': [ids['album_track']], **policy}).status_code == 400


@pytest.fixture
def native_audio(imported_conn, legacy_db, tmp_path, monkeypatch):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg needed for temporary audio')
    path = tmp_path / 'sample.opus'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=0.15', str(path)], check=True)
    audio = File(path)
    audio['title'], audio['album'], audio['artist'] = ['Handwritten file title'], ['Original'], ['File artist']
    audio.save()
    artist, album, track = _seed_album_with_files(imported_conn, path=str(path))
    file_id = imported_conn.execute('SELECT id FROM lib2_track_files WHERE track_id=?', (track,)).fetchone()[0]
    # Art and providers are controlled at the external boundary only.
    monkeypatch.setattr(retag, '_album_cover_data', lambda *_a: (b'new cover', 'image/jpeg'))
    cfg = SimpleNamespace(get=lambda _key, default=None: default)
    monkeypatch.setattr('core.metadata.common.get_config_manager', lambda: cfg)
    return SimpleNamespace(db=legacy_db, conn=imported_conn, path=str(path),
                           artist=artist, album=album, track=track, file_id=file_id, cfg=cfg)


def test_fill_missing_keeps_existing_file_tags_and_fills_gaps(native_audio):
    n = native_audio
    result = retag.write_tags(n.db, [n.track], options={'mode': 'fill_missing', 'cover_art': 'skip'})
    assert result['failed'] == 0
    audio = File(n.path)
    assert audio['title'] == ['Handwritten file title']
    assert audio['album'] == ['Original']
    assert audio['artist'] == ['File artist']
    assert audio['albumartist'] == ['Drake']
    assert 'metadata_block_picture' not in audio


@pytest.mark.parametrize('cover,existing,expected', [
    ('skip', False, None), ('fill_missing', False, b'new cover'),
    ('fill_missing', True, b'old cover'), ('replace', True, b'new cover')])
def test_cover_policy_only_changes_cover(native_audio, cover, existing, expected):
    import base64
    from mutagen.flac import Picture
    n = native_audio
    if existing:
        from core.tag_writer import write_tag_fields
        assert write_tag_fields(n.path, {}, cover_data=(b'old cover', 'image/jpeg'))['success']
    result = retag.write_tags(n.db, [n.track], options={'cover_art': cover, 'fields': ['cover_art']})
    assert result['failed'] == 0
    audio = File(n.path)
    assert audio['title'] == ['Handwritten file title']
    assert audio['album'] == ['Original']
    actual = Picture(base64.b64decode(audio['metadata_block_picture'][0])).data if 'metadata_block_picture' in audio else None
    assert actual == expected


@pytest.mark.parametrize('lyrics,expected', [('skip', None), ('fetch', '[00:01]Hello')])
def test_lyrics_only_uses_lrclib_and_preserves_text(native_audio, monkeypatch, lyrics, expected):
    n = native_audio
    from core.lyrics_client import lyrics_client
    monkeypatch.setattr(lyrics_client, '_fetch_remote_lyrics', lambda *_a, **_k:
                        SimpleNamespace(synced_lyrics='[00:01]Hello', plain_lyrics='Hello'))
    result = retag.write_tags(n.db, [n.track], options={'lyrics': lyrics, 'fields': ['lyrics'], 'cover_art': 'skip'})
    assert result['failed'] == 0
    audio = File(n.path)
    assert (audio.get('lyrics') or [None])[0] == expected
    assert audio['title'] == ['Handwritten file title']
    assert audio['album'] == ['Original']


def test_retag_lyrics_reuses_primary_credit_and_duration_lookup(native_audio, monkeypatch):
    n = native_audio
    from core.lyrics_client import lyrics_client
    n.conn.execute("UPDATE lib2_artists SET name='Various Artists' WHERE id=?", (n.artist,))
    guest = n.conn.execute("INSERT INTO lib2_artists(name) VALUES('Guest Singer')").lastrowid
    n.conn.execute('DELETE FROM lib2_track_artists WHERE track_id=?', (n.track,))
    n.conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id,role,position) VALUES(?,?,'primary',0)",
                   (n.track, guest))
    n.conn.execute('UPDATE lib2_tracks SET duration=180000 WHERE id=?', (n.track,))
    n.conn.commit()
    calls = []
    monkeypatch.setattr(lyrics_client, '_fetch_remote_lyrics',
                        lambda *args: calls.append(args) or SimpleNamespace(plain_lyrics='Words', synced_lyrics=None))
    options = {'lyrics': 'fetch', 'fields': ['lyrics'], 'cover_art': 'skip'}
    preview = retag.tag_preview(retag.track_contexts(n.conn, [n.track]), options=options)
    assert preview[0]['has_changes']
    assert calls == [('One Dance', 'Guest Singer', 'Views', 180)]
    calls.clear()
    result = retag.write_tags(n.db, [n.track], options=options)
    assert result['written'] == 1
    assert calls == [('One Dance', 'Guest Singer', 'Views', 180)]


def test_fill_missing_does_not_look_up_lyrics_a_file_already_has(native_audio, monkeypatch):
    n = native_audio
    from core.lyrics_client import lyrics_client
    calls = []
    monkeypatch.setattr(lyrics_client, '_fetch_remote_lyrics',
                        lambda *args: calls.append(args) or SimpleNamespace(plain_lyrics='Words', synced_lyrics=None))
    retag.write_tags(n.db, [n.track], options={'lyrics': 'fetch', 'fields': ['lyrics'], 'cover_art': 'skip'})
    calls.clear()
    options = {'lyrics': 'fetch', 'mode': 'fill_missing', 'fields': ['lyrics'], 'cover_art': 'skip'}
    assert not retag.tag_preview(retag.track_contexts(n.conn, [n.track]), options=options)[0]['has_changes']
    assert calls == []


def test_aborted_retag_is_failure_and_does_not_update_tag_cache(native_audio, monkeypatch):
    n = native_audio
    monkeypatch.setattr('core.metadata.common.save_audio_file', lambda *_a: False)
    result = retag.write_tags(n.db, [n.track], options={'cover_art': 'skip'})
    assert result['failed'] == 1
    assert result['written'] == 0
    assert File(n.path)['title'] == ['Handwritten file title']


def test_apply_rechecks_manual_and_handtag_protection(native_audio):
    from core.library2.metadata_overrides import set_field_override
    from database.music_database import MusicDatabase
    n = native_audio
    set_field_override(n.conn, entity_type='track', entity_id=n.track, field_name='title', value='Manual now', profile_id=1)
    n.conn.commit()
    result = retag.write_tags(n.db, [n.track], options={'cover_art': 'skip'})
    assert result['written'] == 1
    assert File(n.path)['title'] == ['Manual now']
    n.db.manual_path_keys = lambda: {MusicDatabase.manual_path_key(n.path)}
    n.conn.execute('UPDATE lib2_tracks SET title=? WHERE id=?', ('Provider later', n.track))
    n.conn.commit()
    result = retag.write_tags(n.db, [n.track], options={'cover_art': 'skip'}, protect_hand_tagged=True)
    assert result['written'] == 0
    assert File(n.path)['title'] == ['Manual now']


@pytest.mark.parametrize('settings,writes', [({}, False), ({'dry_run': True, 'auto_apply': True}, False),
    ({'dry_run': False}, True), ({'auto_apply': True}, True), ({'dry_run': False, 'auto_apply': False}, False)])
def test_job_defaults_to_findings_and_requires_explicit_auto_write(native_audio, settings, writes):
    n = native_audio
    findings = []
    cfg = SimpleNamespace(get=lambda key, default=None:
                          {**settings, 'cover_art': 'skip'} if key == 'repair.jobs.library_retag.settings' else default)
    ctx = JobContext(db=n.db, config_manager=cfg, transfer_folder=str(__import__('pathlib').Path(n.path).parent),
                     create_finding=lambda **kw: findings.append(kw) or 1,
                     scope={'file_ids': [n.file_id], 'file_paths': [n.path]})
    result = LibraryRetagJob().scan(ctx)
    assert result.errors == 0
    assert File(n.path)['title'] == (['One Dance'] if writes else ['Handwritten file title'])
    assert bool(findings) is not writes
    if findings:
        assert findings[0]['details']['retag_options']['cover_art'] == 'skip'


@pytest.mark.parametrize('settings,expected', [({}, {'auto_apply': False}),
    ({'dry_run': False}, {'auto_apply': True}),
    ({'dry_run': False, 'auto_apply': False}, {'auto_apply': False}),
    ({'enrichment_depth': 'full'}, {'depth': 'full'}),
    ({'source': 'invalid'}, {'source': 'invalid'})])
def test_job_settings_show_effective_retag_policy(settings, expected):
    worker = object.__new__(RepairWorker)
    worker._jobs = {'library_retag': LibraryRetagJob()}
    worker._config_manager = SimpleNamespace(get=lambda key, default=None: {'settings': settings})
    shown = worker.get_job_config('library_retag')['settings']
    assert {key: shown[key] for key in expected} == expected
    # the 3.6.1 switch is not offered next to auto_apply (it echoed back stale)
    assert 'dry_run' not in shown


def test_consistency_apply_preserves_new_manual_album_override(native_audio):
    from core.library2.metadata_overrides import set_field_override
    n = native_audio
    set_field_override(n.conn, entity_type='release_group', entity_id=n.album, field_name='title', value='Manual album', profile_id=1)
    n.conn.commit()
    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = n.db, n.cfg, None
    result = worker._fix_album_tag_inconsistency('album', f'lib2:{n.album}', None, {
        'library_v2_native': True, 'inconsistencies': [{'field': 'album', 'canonical': 'Majority'}],
        'tracks': [{'track_id': f'lib2:{n.track}', 'file_path': n.path}]})
    assert result['success'] is True
    assert File(n.path)['album'] == ['Original']


def test_job_does_not_include_unscoped_sibling_file(native_audio, tmp_path):
    n = native_audio
    other = tmp_path / 'secondary.opus'
    shutil.copy(n.path, other)
    n.conn.execute('INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)', (n.track, str(other)))
    n.conn.commit()
    findings = []
    cfg = SimpleNamespace(get=lambda key, default=None:
                          {'auto_apply': True, 'cover_art': 'skip'} if key == 'repair.jobs.library_retag.settings' else default)
    ctx = JobContext(db=n.db, config_manager=cfg, transfer_folder=str(tmp_path),
                     create_finding=lambda **kw: findings.append(kw) or 1,
                     scope={'file_ids': [n.file_id], 'file_paths': [n.path]})
    result = LibraryRetagJob().scan(ctx)
    assert result.errors == 0
    assert File(n.path)['title'] == ['One Dance']
    assert File(other)['title'] == ['Handwritten file title']


@pytest.mark.parametrize('source,expected', [('spotify', 'edition-spotify'), ('musicbrainz', 'edition-mb'), ('deezer', None)])
def test_full_refresh_uses_explicit_source_and_owning_edition(native_audio, monkeypatch, source, expected):
    from core.library2.provider_adapters import DescriptiveMetadataProviderResult
    n = native_audio
    n.conn.execute("UPDATE lib2_albums SET spotify_id='group-spotify',musicbrainz_id='group-mb' WHERE id=?", (n.album,))
    edition = n.conn.execute("INSERT INTO lib2_release_editions(release_group_id,title,spotify_id,musicbrainz_id,is_default) "
        "VALUES(?,'Edition','edition-spotify','edition-mb',1)", (n.album,)).lastrowid
    recording = n.conn.execute("INSERT INTO lib2_recordings(title) VALUES('One Dance')").lastrowid
    n.conn.execute('INSERT INTO lib2_release_tracks(release_edition_id,recording_id,track_id,track_number,disc_number) VALUES(?,?,?,1,1)', (edition, recording, n.track))
    n.conn.commit()
    calls = []
    def fetch(kind, ids, **_kw):
        calls.append((kind, dict(ids)))
        provider, provider_id = next(iter(ids.items()))
        return DescriptiveMetadataProviderResult(provider, provider_id, mood='Refreshed')
    monkeypatch.setattr('core.library2.provider_adapters.fetch_descriptive_metadata', fetch)
    monkeypatch.setattr('core.library2.match_status.configured_services', lambda: {'spotify', 'musicbrainz', 'deezer'})
    result = retag.refresh_metadata(n.db, [n.track], source=source)
    assert result['errors'] == []
    album_calls = [ids for kind, ids in calls if kind == 'album']
    assert album_calls == ([{source: expected}] if expected else [])
    assert all(set(ids) == {source} for _, ids in calls)
    assert File(n.path)['title'] == ['Handwritten file title']
    if expected:
        assert n.conn.execute('SELECT mood FROM lib2_albums WHERE id=?', (n.album,)).fetchone()[0] == 'Refreshed'


def test_approved_finding_applies_stored_fill_missing_and_cover_skip(native_audio):
    n = native_audio
    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = n.db, n.cfg, None
    result = worker._fix_library_retag('track', f'lib2:{n.track}', n.path, {
        'retag_options': {'mode': 'fill_missing', 'cover_art': 'skip'}, 'file_ids': [n.file_id]})
    assert result['success'], result
    assert File(n.path)['title'] == ['Handwritten file title']
    assert File(n.path)['albumartist'] == ['Drake']


def test_a_finding_about_a_deleted_file_is_retired_not_left_failing(native_audio):
    """#1143 for retag: the reviewed file was deleted, so no retry can succeed."""
    import os
    n = native_audio
    os.remove(n.path)
    n.conn.execute("UPDATE lib2_track_files SET file_state='deleted' WHERE id=?", (n.file_id,))
    n.conn.commit()
    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = n.db, n.cfg, None
    result = worker._fix_library_retag('track', f'lib2:{n.track}', n.path, {'validation': {'file_id': n.file_id}})
    assert result['stale'] and not result['success'], result


def test_failed_sibling_save_keeps_retag_apply_failed(native_audio, tmp_path, monkeypatch):
    n = native_audio
    other = tmp_path / 'sibling.opus'
    shutil.copy(n.path, other)
    n.conn.execute('INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)', (n.track, str(other)))
    n.conn.commit()
    from core.metadata.common import save_audio_file
    monkeypatch.setattr('core.metadata.common.save_audio_file', lambda audio, symbols:
                        False if str(audio.filename) == str(other) else save_audio_file(audio, symbols))
    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = n.db, n.cfg, None
    result = worker._fix_library_retag('track', f'lib2:{n.track}', n.path, {'retag_options': {'cover_art': 'skip'}})
    assert result['success'] is False
    assert File(other)['title'] == ['Handwritten file title']


@pytest.mark.parametrize('options', [None, {'cover_art': 'skip'}])
def test_missing_sibling_does_not_stop_later_copies(native_audio, tmp_path, options):
    n = native_audio
    later = tmp_path / 'later.opus'
    shutil.copy(n.path, later)
    n.conn.executemany('INSERT INTO lib2_track_files(track_id,path,format) VALUES(?,?,?)',
                      [(n.track, str(tmp_path / 'missing.opus'), 'opus'), (n.track, str(later), 'opus')])
    n.conn.commit()
    result = retag.write_tags(n.db, [n.track], embed_cover=False, options=options)
    assert result['failed'] == 1
    assert result['written'] == 2
    assert File(later)['title'] == ['One Dance']


@pytest.fixture
def art_sources(monkeypatch):
    """the user's cover-art sources: a configured order and one source that
    answers. lookups are recorded; nothing leaves the process."""
    from core.settings import config_manager
    from core.metadata import art_lookup, artwork as metadata_artwork
    state = SimpleNamespace(order=['deezer', 'itunes'], url='https://img.example/preferred.jpg',
                            asked=[])
    real_get = config_manager.get
    monkeypatch.setattr(config_manager, 'get', lambda key, default=None: (
        state.order if key == 'metadata_enhancement.album_art_order'
        else 0 if key == 'metadata_enhancement.min_art_size' else real_get(key, default)))

    def pick(artist, album, metadata, order, validate=None):
        state.asked.append((artist, album, list(order)))
        return state.url
    monkeypatch.setattr(art_lookup, 'select_preferred_art_url', pick)
    monkeypatch.setattr(metadata_artwork, '_fetch_art_bytes',
                        lambda url: (b'preferred cover', 'image/jpeg'))
    return state


@pytest.fixture
def flac_audio(imported_conn, legacy_db, tmp_path, monkeypatch):
    """native_audio without ffmpeg: a real minimal flac, written by mutagen."""
    from pathlib import Path
    from tests.test_album_consistency_adopt import _make_flac
    path = tmp_path / 'sample.flac'
    _make_flac(Path(path), {'title': 'Handwritten file title', 'album': 'Original',
                            'artist': 'File artist'})
    _artist, _album, track = _seed_album_with_files(imported_conn, path=str(path))
    monkeypatch.setattr(retag, '_album_cover_data', lambda *_a: (b'new cover', 'image/jpeg'))
    return SimpleNamespace(db=legacy_db, conn=imported_conn, path=str(path), track=track)


def _embedded(path):
    from mutagen.flac import FLAC
    pictures = FLAC(path).pictures
    return pictures[0].data if pictures else None


def test_replace_embeds_the_users_preferred_cover_source(flac_audio, art_sources):
    """3.6.1: Library Re-tag with cover art "replace" pulled fresh art from
    metadata_enhancement.album_art_order, not the cover the album already had."""
    from core.tag_writer import write_tag_fields
    n = flac_audio
    assert write_tag_fields(n.path, {}, cover_data=(b'old cover', 'image/jpeg'))['success']
    result = retag.write_tags(n.db, [n.track], options={'cover_art': 'replace', 'fields': ['cover_art']})
    assert result['failed'] == 0
    assert _embedded(n.path) == b'preferred cover'
    assert art_sources.asked == [('Drake', 'Views', ['deezer', 'itunes'])]
    assert File(n.path)['title'] == ['Handwritten file title']


@pytest.mark.parametrize('case', ['no_order', 'nothing_found', 'hand_picked'])
def test_replace_keeps_the_albums_own_cover_otherwise(flac_audio, art_sources, monkeypatch, case):
    n = flac_audio
    if case == 'no_order':
        art_sources.order = []
    elif case == 'nothing_found':
        art_sources.url = None
    else:
        monkeypatch.setattr('core.library2.artwork.manual_art_override_url',
                            lambda *_a: 'https://img.example/picked.jpg')
    retag.write_tags(n.db, [n.track], options={'cover_art': 'replace', 'fields': ['cover_art']})
    assert _embedded(n.path) == b'new cover'
    if case != 'nothing_found':
        assert art_sources.asked == []


def test_fill_missing_and_the_plain_write_never_ask_the_sources(flac_audio, art_sources):
    n = flac_audio
    retag.write_tags(n.db, [n.track], options={'cover_art': 'fill_missing', 'fields': ['cover_art']})
    assert _embedded(n.path) == b'new cover'
    retag.write_tags(n.db, [n.track], force_cover=True)
    assert art_sources.asked == []


class _Store:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        node = self.values
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key, value):
        node = self.values
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value


def _worker(store):
    worker = object.__new__(RepairWorker)
    worker._jobs = {'library_retag': LibraryRetagJob()}
    worker._config_manager = store
    return worker


def test_turning_auto_apply_on_works_for_a_3_6_1_save():
    """found in the second #1062 review: 3.6.1 saved dry_run: true, and with
    it stored, Auto Apply snapped back off on every save"""
    from core.library2.retag import retag_options
    store = _Store({'repair': {'jobs': {'library_retag': {'settings': {
        'dry_run': True, 'cover_art': 'skip'}}}}})
    worker = _worker(store)
    shown = worker.get_job_config('library_retag')['settings']
    assert 'dry_run' not in shown and shown['auto_apply'] is False

    worker.set_job_settings('library_retag', settings={**shown, 'auto_apply': True})

    saved = store.get('repair.jobs.library_retag.settings')
    assert 'dry_run' not in saved
    assert retag_options(saved)['auto_apply'] is True
    assert worker.get_job_config('library_retag')['settings']['auto_apply'] is True


def test_a_caller_that_sends_both_keeps_the_safe_dry_run():
    """an API caller saying dry_run: true on purpose still gets no writes"""
    from core.library2.retag import retag_options
    store = _Store({'repair': {'jobs': {'library_retag': {'settings': {}}}}})
    worker = _worker(store)
    worker.set_job_settings('library_retag', settings={'dry_run': True, 'auto_apply': True})
    assert retag_options(store.get('repair.jobs.library_retag.settings'))['auto_apply'] is False


def test_album_edition_review_no_longer_offers_a_dead_dry_run():
    from core.repair_jobs.album_edition_review import AlbumEditionReviewJob
    store = _Store({'repair': {'jobs': {'album_edition_review': {'settings': {
        'dry_run': False, 'min_score': 0.6}}}}})
    worker = object.__new__(RepairWorker)
    worker._jobs = {'album_edition_review': AlbumEditionReviewJob()}
    worker._config_manager = store
    shown = worker.get_job_config('album_edition_review')['settings']
    assert 'dry_run' not in shown and shown['min_score'] == 0.6


class _FakePlex:
    def __init__(self, ok=True):
        self.ok, self.edits = ok, []

    def update_track_metadata(self, key, metadata):
        self.edits.append((key, metadata))
        return self.ok


def test_write_tags_syncs_to_plex_when_asked(api, monkeypatch):
    """third #1062 review G15-5: 3.6.1's "Sync to Plex/Jellyfin" pushed the
    written title/artist/album/year to Plex in the same action; v2 dropped it"""
    from contextlib import closing
    from core.library2 import server_tag_sync
    client, db, ids = api
    track = ids['album_track']
    with closing(db._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_media_server_mappings(entity_type, entity_id, server_source, server_id) "
                     "VALUES('track', ?, 'plex', '4242')", (track,))
        title, album = conn.execute(
            "SELECT t.title, a.title FROM lib2_tracks t JOIN lib2_albums a ON a.id=t.album_id "
            "WHERE t.id=?", (track,)).fetchone()
        conn.commit()
    plex = _FakePlex()
    monkeypatch.setattr(server_tag_sync, '_active_server_and_client', lambda: ('plex', plex))
    monkeypatch.setattr(retag, 'write_tags', lambda *a, **kw: {'written': 1, 'skipped': 0, 'failed': 0, 'errors': []})

    preview = client.get(f"/api/library/v2/albums/{ids['views']}/tag-preview").get_json()
    assert preview['server_type'] == 'plex'

    response = client.post('/api/library/v2/tags/write', json={'track_ids': [track], 'sync_to_server': True})
    result = _wait_for_job(client, response.get_json()['job_id'])['result']
    assert result['server_sync']['synced'] == 1
    assert plex.edits[0][0] == '4242'
    assert plex.edits[0][1]['title'] == title and plex.edits[0][1]['album'] == album

    # not asked: the server is left alone
    plex.edits.clear()
    response = client.post('/api/library/v2/tags/write', json={'track_ids': [track]})
    result = _wait_for_job(client, response.get_json()['job_id'])['result']
    assert 'server_sync' not in result and plex.edits == []


def test_server_sync_skips_tracks_plex_never_saw_and_scans_jellyfin(api):
    from core.library2.server_tag_sync import sync_tracks_to_server
    _, db, ids = api
    plex = _FakePlex()
    out = sync_tracks_to_server(db, [ids['album_track']], resolve=lambda: ('plex', plex))
    assert out['skipped'] == 1 and plex.edits == []

    scans = []
    jelly = SimpleNamespace(trigger_library_scan=lambda: scans.append(1) or True)
    out = sync_tracks_to_server(db, [ids['album_track'], ids['album_track']],
                                resolve=lambda: ('jellyfin', jelly))
    assert out['synced'] == 1 and scans == [1]

    out = sync_tracks_to_server(db, [ids['album_track']], resolve=lambda: (None, None))
    assert out['skipped'] == 1 and out['errors']


def test_server_sync_never_edits_an_own_librarys_plex_key(api):
    """review 4 R4-B-10: an own library's ratingKey came from that profile's
    scan, maybe of another Plex server; on the active one it can name an
    unrelated track, whose title and artist the push rewrote"""
    from contextlib import closing
    from core.library2.server_tag_sync import sync_tracks_to_server
    _, db, ids = api
    track = ids['album_track']
    with closing(db._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_media_server_mappings(entity_type, entity_id, server_source, server_id, "
                     "server_library_id) VALUES('track', ?, 'plex', '4242', '')", (track,))
        conn.execute("INSERT INTO lib2_media_server_mappings(entity_type, entity_id, server_source, server_id, "
                     "server_library_id) VALUES('track', ?, 'plex', '9999', 'own:2')", (track,))
        conn.commit()
    plex = _FakePlex()
    out = sync_tracks_to_server(db, [track], resolve=lambda: ('plex', plex))
    assert out['synced'] == 1
    assert [key for key, _meta in plex.edits] == ['4242']
