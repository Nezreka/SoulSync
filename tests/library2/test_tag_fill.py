"""keep file tags filled from the library.

an import fills the blanks the library knows (#2217). what the library learns
after the import (a genre from enrichment, a year, a track total) used to be
a Library Re-tag finding per file for someone to apply. with tag writing on,
SoulSync fills those blanks itself, never over a tag the file has, keeps
what it wrote, and can put it back.

real files in all four formats the writer supports, made by libsndfile (and
ffmpeg for m4a, skipped where it is missing)."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
from contextlib import closing
from types import SimpleNamespace

import pytest

from core.library2 import tag_fill
from core.library2.schema import ensure_library_v2_schema
from core.library2.tag_cache import persist_tag_cache, read_tag_snapshot
from core.tag_writer import write_tag_fields
from tests.lib2_seed import RowDb

FORMATS = ('flac', 'mp3', 'm4a', 'ogg')
OLD = time.time() - 3600
_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class Db(RowDb):
    def manual_path_keys(self):
        with closing(self._get_connection()) as conn:
            return {r[0] for r in conn.execute('SELECT path_key FROM manual_metadata_files')}


def _audio(path):
    ext = path.suffix[1:]
    if ext == 'm4a':
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            pytest.skip('no ffmpeg to make an m4a')
        subprocess.run([ffmpeg, '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=1', '-c:a', 'aac', str(path)], check=True)
        return path
    np = pytest.importorskip('numpy')
    sf = pytest.importorskip('soundfile')
    tone = (np.sin(np.linspace(0, 440 * 2 * np.pi, 8000)) * 0.1).astype('float32')
    fmt, subtype = {'flac': ('FLAC', 'PCM_16'), 'ogg': ('OGG', 'VORBIS'),
                    'mp3': ('MP3', 'MPEG_LAYER_III')}[ext]
    try:
        sf.write(str(path), tone, 8000, format=fmt, subtype=subtype)
    except Exception as exc:  # noqa: BLE001 - an old libsndfile without mp3
        pytest.skip(f'libsndfile cannot write {ext}: {exc}')
    return path


def _age(path):
    os.utime(path, (OLD, OLD))


def _settings(**over):
    base = {'metadata_enhancement.embed_album_art': False,
            'metadata_enhancement.cover_art_download': False}
    base.update(over)
    return base


def _build(tmp_path, monkeypatch, ext='flac', settings=None):
    folder = tmp_path / 'Artist' / 'Album'
    folder.mkdir(parents=True)
    path = _audio(folder / f'07 - Track 7.{ext}')
    # what an import left: no genre, no year, no track total
    assert write_tag_fields(str(path), {'title': 'Track 7', 'artist': 'Artist', 'album_artist': 'Artist',
                                        'album': 'Album', 'track_number': 7, 'disc_number': 1})['success']
    _age(path)
    values = settings if settings is not None else _settings()
    config = SimpleNamespace(get=lambda key, default=None: values.get(key, default), values=values)
    monkeypatch.setattr('core.metadata.common.get_config_manager', lambda: config)
    db = Db(str(tmp_path / 'library.db'))
    with closing(db._get_connection()) as conn, conn:
        ensure_library_v2_schema(conn)
        conn.execute("INSERT INTO lib2_artists(id,name) VALUES(1,'Artist')")
        conn.execute("INSERT INTO lib2_albums(id,primary_artist_id,title,year,genres,image_url,expected_track_count) "
                     "VALUES(1,1,'Album',2026,'[\"Rock\"]','https://example.test/cover',13)")
        conn.executemany("INSERT INTO lib2_tracks(id,album_id,title,track_number,disc_number) VALUES(?,1,?,?,1)",
                         [(i, f'Track {i}', i) for i in range(1, 14)])
        conn.execute("INSERT INTO lib2_track_files(id,track_id,path,format) VALUES(1,7,?,?)", (str(path), ext))
        conn.execute("INSERT INTO lib2_release_editions(id,release_group_id,track_count,disc_count) VALUES(1,1,13,1)")
        conn.executemany('INSERT INTO lib2_recordings(id,title) VALUES(?,?)', [(i, f'Track {i}') for i in range(1, 14)])
        conn.executemany('INSERT INTO lib2_release_tracks(release_edition_id,recording_id,track_id,track_number,disc_number) '
                         'VALUES(1,?,?,?,1)', [(i, i, i) for i in range(1, 14)])
        conn.execute("CREATE TABLE repair_findings(id INTEGER PRIMARY KEY, job_id TEXT, finding_type TEXT, severity TEXT, "
                     "status TEXT DEFAULT 'pending', entity_type TEXT, entity_id TEXT, file_path TEXT, title TEXT, "
                     "description TEXT, details_json TEXT, user_action TEXT, resolved_at TEXT, "
                     "updated_at TEXT DEFAULT CURRENT_TIMESTAMP, last_error TEXT, fix_claimed_at TEXT)")
        conn.execute("CREATE TABLE manual_metadata_files(path_key TEXT PRIMARY KEY, file_path TEXT, "
                     "album_title TEXT, album_artist TEXT)")
    return SimpleNamespace(db=db, path=path, config=config)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    return _build(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _no_locks_left_over():
    from core.library2 import locks
    locks.reset_for_tests()
    yield
    locks.reset_for_tests()


def observe(lib):
    """the tag check a library scan runs: files the finding for the blanks"""
    with closing(lib.db._get_connection()) as conn, conn:
        persist_tag_cache(conn, 1, read_tag_snapshot(str(lib.path)), lib.config)


def tags(lib):
    return read_tag_snapshot(str(lib.path))


def findings(lib):
    with closing(lib.db._get_connection()) as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM repair_findings ORDER BY id')]


def fills(lib):
    with closing(lib.db._get_connection()) as conn:
        tag_fill.ensure_schema(conn)
        return [tag_fill._row_dict(r) for r in conn.execute('SELECT * FROM lib2_tag_fills ORDER BY id')]


def drain(lib, **kw):
    return tag_fill.drain(lib.db, lib.config, pause=0, **kw)


# ── the setting ──────────────────────────────────────────────────────────────

def _cfg(**values):
    return SimpleNamespace(get=lambda key, default=None: values.get(key, default))


def test_unset_fills_blanks_while_post_processing_writes_tags():
    assert tag_fill.fill_mode(_cfg()) == 'fill'
    assert tag_fill.fill_mode(_cfg(**{'metadata_enhancement.enabled': True})) == 'fill'


def test_post_processing_off_writes_nothing_whatever_is_stored():
    for stored in (None, 'fill', 'update'):
        assert tag_fill.fill_mode(_cfg(**{'metadata_enhancement.enabled': False,
                                          tag_fill.SETTING: stored})) == 'off'


def test_an_explicit_choice_is_kept_and_junk_is_the_default():
    for stored in ('off', 'fill', 'update', ' Update '):
        assert tag_fill.fill_mode(_cfg(**{tag_fill.SETTING: stored})) == stored.strip().lower()
    assert tag_fill.fill_mode(_cfg(**{tag_fill.SETTING: 'always'})) == 'fill'
    assert tag_fill.fill_mode(_cfg(**{tag_fill.SETTING: True})) == 'fill'


def test_an_install_with_re_tag_auto_apply_on_is_not_moved_to_update():
    """3.6.x's Re-tag auto apply writes inside its own runs, as before. the
    queue only ever replaces a tag after someone picks Fill and update."""
    legacy = _cfg(**{'repair.jobs.library_retag.settings': {'auto_apply': True}})
    assert tag_fill.fill_mode(legacy) == 'fill'


def test_an_unreadable_setting_writes_nothing():
    broken = SimpleNamespace(get=lambda key, default=None: 1 / 0)
    assert tag_fill.fill_mode(broken) == 'off'


# ── filling ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('ext', FORMATS)
def test_the_blanks_the_library_knows_are_filled_in_every_format(tmp_path, monkeypatch, ext):
    lib = _build(tmp_path, monkeypatch, ext)
    observe(lib)
    (finding,) = findings(lib)
    assert finding['status'] == 'pending'

    stats = drain(lib)

    assert stats['files'] == 1 and stats['fields'] == 3, stats
    after = tags(lib)
    assert after['genre'] == 'Rock' and str(after['year']).startswith('2026')
    assert after['total_tracks'] == 13
    # untouched: what the file had
    assert after['title'] == 'Track 7' and after['track_number'] == 7
    # the write's own tag check closed the finding
    assert findings(lib)[0]['status'] == 'resolved'
    (row,) = fills(lib)
    assert row['status'] == 'filled' and row['source'] == 'queue' and row['mode'] == 'fill'
    assert {f['key']: f['before'] for f in row['fields']} == {'genre': '', 'year': '', 'total_tracks': ''}
    assert row['cover'] is None


def test_a_tag_the_file_has_is_never_replaced(lib):
    write_tag_fields(str(lib.path), {'genre': 'Krautrock'})
    _age(lib.path)
    observe(lib)

    drain(lib)

    after = tags(lib)
    assert after['genre'] == 'Krautrock'
    assert str(after['year']).startswith('2026')
    assert {f['key'] for f in fills(lib)[0]['fields']} == {'year', 'total_tracks'}


def test_a_flagged_difference_stays_a_finding_in_fill_mode(lib):
    lib.config.values['repair.jobs.library_retag.settings'] = {'flag_genre_differences': True}
    write_tag_fields(str(lib.path), {'genre': 'Krautrock'})
    _age(lib.path)
    observe(lib)

    drain(lib)

    assert tags(lib)['genre'] == 'Krautrock'
    (finding,) = findings(lib)
    assert finding['status'] == 'pending'
    assert json.loads(finding['details_json'])['validation']['checks']['genre'] == 'mismatch'


def test_update_replaces_a_flagged_difference(lib):
    lib.config.values.update({tag_fill.SETTING: 'update',
                              'repair.jobs.library_retag.settings': {'flag_genre_differences': True}})
    write_tag_fields(str(lib.path), {'genre': 'Krautrock'})
    _age(lib.path)
    observe(lib)

    drain(lib)

    assert tags(lib)['genre'] == 'Rock'
    (row,) = fills(lib)
    assert row['mode'] == 'update'
    assert {f['key']: f['before'] for f in row['fields']}['genre'] == 'Krautrock'
    assert findings(lib)[0]['status'] == 'resolved'


def test_update_leaves_a_quiet_difference_alone(lib):
    """a genre the Re-tag settings do not flag is taste, not a fault: even
    Fill and update keeps the file's"""
    lib.config.values[tag_fill.SETTING] = 'update'
    write_tag_fields(str(lib.path), {'genre': 'Krautrock'})
    _age(lib.path)
    observe(lib)

    drain(lib)

    assert tags(lib)['genre'] == 'Krautrock'


def test_off_writes_nothing_and_the_finding_stays(lib):
    lib.config.values[tag_fill.SETTING] = 'off'
    observe(lib)

    assert drain(lib)['files'] == 0

    assert not tags(lib).get('genre')
    assert findings(lib)[0]['status'] == 'pending'
    assert fills(lib) == []


def test_post_processing_off_writes_nothing(lib):
    lib.config.values['metadata_enhancement.enabled'] = False
    observe(lib)
    drain(lib)
    assert not tags(lib).get('genre') and fills(lib) == []


# ── no loops, no fights ──────────────────────────────────────────────────────

def test_a_second_pass_writes_nothing(lib):
    observe(lib)
    drain(lib)
    _age(lib.path)
    observe(lib)

    stats = drain(lib)

    assert stats['files'] == 0
    assert len(fills(lib)) == 1


def test_a_tag_someone_cleared_after_a_fill_is_not_filled_again(lib):
    """the user (or another tagger) took the genre out: it is theirs to keep
    out. the finding comes back for a person instead"""
    observe(lib)
    drain(lib)
    from mutagen import File
    audio = File(str(lib.path))
    del audio['genre']
    audio.save()
    _age(lib.path)
    observe(lib)

    stats = drain(lib)

    assert stats['files'] == 0 and stats['skipped'].get('held') == 1, stats
    assert not tags(lib).get('genre')
    assert findings(lib)[-1]['status'] == 'pending'


def test_a_tag_someone_set_after_a_fill_is_left_alone(lib):
    observe(lib)
    drain(lib)
    write_tag_fields(str(lib.path), {'genre': 'Jazz'})
    _age(lib.path)
    observe(lib)
    drain(lib)
    assert tags(lib)['genre'] == 'Jazz'


def test_a_write_that_does_not_read_back_is_held(lib, monkeypatch):
    """a field the writer cannot make stick would be written on every pass"""
    observe(lib)
    from core.library2 import retag
    real = retag.write_tags
    monkeypatch.setattr(retag, 'write_tags', lambda *a, **k: {**real(*a, **{**k, 'options': {
        **k['options'], 'fields': [f for f in k['options']['fields'] if f != 'genre']}})})

    drain(lib)

    (row,) = fills(lib)
    assert row['hold'] == ['genre'] and not tags(lib).get('genre')
    monkeypatch.setattr(retag, 'write_tags', real)
    _age(lib.path)
    assert drain(lib)['skipped'].get('held') == 1


# ── what it leaves alone ─────────────────────────────────────────────────────

def _left_alone(lib, reason, *, observed=False):
    if not observed:
        observe(lib)
    stats = drain(lib)
    assert stats['files'] == 0 and stats['skipped'].get(reason) == 1, stats
    assert not tags(lib).get('genre')
    assert fills(lib) == []
    assert findings(lib)[0]['status'] == 'pending'


def test_a_hand_tagged_file_is_left_alone(lib):
    # tagged by hand after its finding was filed: the scan files none for it
    observe(lib)
    from database.music_database import MusicDatabase
    with closing(lib.db._get_connection()) as conn, conn:
        conn.execute('INSERT INTO manual_metadata_files(path_key) VALUES(?)',
                     (MusicDatabase.manual_path_key(str(lib.path)),))
    _left_alone(lib, 'hand_tagged', observed=True)


def test_a_file_on_a_hand_tagged_album_is_left_alone(lib):
    observe(lib)
    with closing(lib.db._get_connection()) as conn, conn:
        conn.execute("INSERT INTO lib2_track_files(id,track_id,path,format) VALUES(2,8,?,'flac')",
                     (str(lib.path.parent / '08 - Track 8.flac'),))
        conn.execute('INSERT INTO manual_metadata_files(path_key) VALUES(?)', ('artist/album/08 - track 8.flac',))
    _left_alone(lib, 'hand_tagged', observed=True)


def test_the_hand_tag_check_of_the_file_itself(lib, monkeypatch):
    """the file's own key, apart from the album-wide check"""
    observe(lib)
    monkeypatch.setattr('core.library2.validation.album_is_hand_tagged', lambda conn, track_id: False)
    from database.music_database import MusicDatabase
    with closing(lib.db._get_connection()) as conn, conn:
        conn.execute('INSERT INTO manual_metadata_files(path_key) VALUES(?)',
                     (MusicDatabase.manual_path_key(str(lib.path)),))
    _left_alone(lib, 'hand_tagged', observed=True)


@pytest.mark.parametrize('entity,entity_id', [('album', 1), ('artist', 1)])
def test_a_locked_album_or_artist_is_left_alone(lib, entity, entity_id):
    from core.library2 import locks
    with closing(lib.db._get_connection()) as conn, conn:
        locks.set_lock(conn, entity, entity_id, ['tags'])
    _left_alone(lib, 'locked')


def test_a_lock_on_something_else_does_not_stop_it(lib):
    from core.library2 import locks
    with closing(lib.db._get_connection()) as conn, conn:
        locks.set_lock(conn, 'album', 1, ['artwork'])
    observe(lib)
    assert drain(lib)['files'] == 1


def test_a_file_that_just_changed_is_left_alone(lib):
    os.utime(lib.path, None)
    _left_alone(lib, 'in_progress')


def test_a_file_another_writer_holds_is_left_alone(lib):
    from core.metadata.common import get_file_lock
    lock = get_file_lock(str(lib.path))
    with lock:
        _left_alone(lib, 'in_progress')


@pytest.mark.parametrize('key', ['soulseek.download_path', 'import.staging_path',
                                 'download_source.torrent_download_path'])
def test_a_file_in_a_download_or_seeding_folder_is_left_alone(lib, key):
    lib.config.values[key] = str(lib.path.parent.parent.parent)
    _left_alone(lib, 'in_progress')


def test_a_download_folder_that_holds_the_library_is_not_a_reason(lib):
    """downloads and library in one folder: nothing is in flight there"""
    root = str(lib.path.parent.parent.parent)
    lib.config.values.update({'soulseek.download_path': root, 'soulseek.transfer_path': root})
    observe(lib)
    assert drain(lib)['files'] == 1


def test_a_file_that_cannot_be_written_is_left_alone(lib, monkeypatch):
    monkeypatch.setattr(tag_fill, '_writable', lambda path: False)
    _left_alone(lib, 'read_only')


def test_writable_reads_the_file_and_its_folder(tmp_path):
    path = tmp_path / 'a.flac'
    path.write_bytes(b'x')
    assert tag_fill._writable(str(path)) is True
    os.chmod(path, 0o444)
    try:
        if os.access(path, os.W_OK):
            pytest.skip('this filesystem ignores the read-only bit')
        assert tag_fill._writable(str(path)) is False
    finally:
        os.chmod(path, 0o644)


def test_a_dismissed_finding_is_left_alone(lib):
    observe(lib)
    with closing(lib.db._get_connection()) as conn, conn:
        conn.execute("UPDATE repair_findings SET status='dismissed'")
    assert tag_fill.fill_file(lib.db, 1, mode='fill', config=lib.config) == {'skip': 'dismissed'}
    assert not tags(lib).get('genre')


def test_a_write_the_audio_check_refuses_changes_nothing(lib, monkeypatch):
    before = lib.path.read_bytes()
    monkeypatch.setattr('core.metadata.common._flac_audio_identical', lambda a, b: False)
    observe(lib)

    stats = drain(lib)

    assert stats['errors'] == 1 and stats['files'] == 0
    assert lib.path.read_bytes() == before
    assert fills(lib) == [] and findings(lib)[0]['status'] == 'pending'


def test_an_unknown_edition_holds_the_numbers(lib):
    with closing(lib.db._get_connection()) as conn, conn:
        conn.execute("INSERT INTO lib2_release_editions(id,release_group_id,track_count,disc_count) VALUES(2,1,14,1)")
        conn.execute("INSERT INTO lib2_release_tracks(release_edition_id,recording_id,track_id,track_number,disc_number) "
                     "VALUES(2,7,7,9,1)")
    observe(lib)
    drain(lib)
    assert tags(lib)['genre'] == 'Rock' and not tags(lib).get('total_tracks')


# ── cover ────────────────────────────────────────────────────────────────────

@pytest.fixture
def covered(tmp_path, monkeypatch):
    lib = _build(tmp_path, monkeypatch, settings=_settings(**{'metadata_enhancement.embed_album_art': True}))
    monkeypatch.setattr('core.library2.retag._album_cover_data', lambda db, album_id: (_PNG, 'image/png'))
    return lib


def test_a_missing_cover_is_added_when_the_library_has_one(covered):
    observe(covered)
    stats = drain(covered)
    assert stats['covers'] == 1
    assert tags(covered)['has_cover_art'] is True
    assert fills(covered)[0]['cover'] == 'added'


def test_no_cover_is_added_when_the_library_has_none(covered):
    with closing(covered.db._get_connection()) as conn, conn:
        conn.execute('UPDATE lib2_albums SET image_url=NULL')
    observe(covered)
    drain(covered)
    assert tags(covered)['has_cover_art'] is False


def test_a_cover_the_file_has_is_never_replaced(covered, monkeypatch):
    other = _PNG + b'\x00'
    from core.tag_writer import _embed_cover_art_data
    from mutagen import File
    audio = File(str(covered.path))
    _embed_cover_art_data(audio, other, 'image/png')
    audio.save()
    _age(covered.path)
    observe(covered)
    drain(covered)
    assert File(str(covered.path)).pictures[0].data == other


def test_undo_takes_an_added_cover_out_again(covered):
    observe(covered)
    drain(covered)
    result = tag_fill.undo_fill(covered.db, fills(covered)[0]['id'], covered.config)
    assert result['success'] is True, result
    assert tags(covered)['has_cover_art'] is False


# ── undo ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('ext', FORMATS)
def test_undo_puts_the_blanks_back_in_every_format(tmp_path, monkeypatch, ext):
    lib = _build(tmp_path, monkeypatch, ext)
    observe(lib)
    drain(lib)

    result = tag_fill.undo_fill(lib.db, fills(lib)[0]['id'], lib.config)

    assert result['success'] is True, result
    assert set(result['restored']) == {'genre', 'year', 'total_tracks'}
    after = tags(lib)
    assert not after.get('genre') and not after.get('year') and not after.get('total_tracks')
    assert after['title'] == 'Track 7' and after['track_number'] == 7
    (row,) = fills(lib)
    assert row['status'] == 'undone' and set(row['hold']) == {'genre', 'year', 'total_tracks'}
    # the blanks are a finding again, for a person: never filled again
    assert findings(lib)[-1]['status'] == 'pending'
    _age(lib.path)
    assert drain(lib)['files'] == 0 and not tags(lib).get('genre')


def test_undo_keeps_a_tag_someone_set_since(lib):
    observe(lib)
    drain(lib)
    write_tag_fields(str(lib.path), {'genre': 'Jazz'})

    result = tag_fill.undo_fill(lib.db, fills(lib)[0]['id'], lib.config)

    assert result['kept'] == ['genre']
    assert tags(lib)['genre'] == 'Jazz' and not tags(lib).get('year')


def test_undo_twice_is_refused(lib):
    observe(lib)
    drain(lib)
    fill_id = fills(lib)[0]['id']
    assert tag_fill.undo_fill(lib.db, fill_id, lib.config)['success'] is True
    assert tag_fill.undo_fill(lib.db, fill_id, lib.config)['success'] is False
    assert tag_fill.undo_fill(lib.db, 999, lib.config)['success'] is False


def test_undo_leaves_a_file_that_is_hand_tagged_now(lib):
    observe(lib)
    drain(lib)
    from database.music_database import MusicDatabase
    with closing(lib.db._get_connection()) as conn, conn:
        conn.execute('INSERT INTO manual_metadata_files(path_key) VALUES(?)',
                     (MusicDatabase.manual_path_key(str(lib.path)),))
    assert tag_fill.undo_fill(lib.db, fills(lib)[0]['id'], lib.config)['success'] is False
    assert tags(lib)['genre'] == 'Rock'


def test_a_run_is_listed_and_undone_as_one(lib):
    observe(lib)
    drain(lib, batch_id='queue:test-run')

    listed = tag_fill.summary(lib.db)
    assert listed['files'] == 1 and listed['fields'] == 3
    (run,) = listed['runs']
    assert run['batch_id'] == 'queue:test-run' and run['files'] == 1 and run['undone'] == 0
    (file,) = tag_fill.batch_files(lib.db, 'queue:test-run')
    assert file['file_id'] == 1 and len(file['fields']) == 3

    out = tag_fill.undo_batch(lib.db, 'queue:test-run', lib.config)

    assert out['files'] == 1 and out['failed'] == 0
    assert not tags(lib).get('genre')
    assert tag_fill.summary(lib.db)['runs'][0]['undone'] == 1
    # undone fills no longer count as filled this week
    assert tag_fill.summary(lib.db)['files'] == 0
    assert tag_fill.undo_batch(lib.db, 'queue:test-run', lib.config)['success'] is False


def test_a_run_undo_in_the_background_reports_its_progress(lib):
    observe(lib)
    drain(lib, batch_id='queue:bg')
    started = tag_fill.start_undo_batch(lib.db, 'queue:bg', lib.config)
    assert started == {'success': True, 'started': True, 'total': 1}
    for _ in range(200):
        if not tag_fill.undo_state().get('running'):
            break
        time.sleep(0.02)
    state = tag_fill.undo_state()
    assert state['running'] is False and state['result']['files'] == 1
    assert not tags(lib).get('genre')


# ── the queue ────────────────────────────────────────────────────────────────

def test_the_queue_stops_when_asked(lib):
    observe(lib)
    assert drain(lib, should_stop=lambda: True)['files'] == 0
    assert not tags(lib).get('genre')


def test_the_queue_does_no_more_than_its_limit(tmp_path, monkeypatch):
    lib = _build(tmp_path, monkeypatch)
    observe(lib)
    assert drain(lib, limit=0)['files'] == 0


def test_the_repair_worker_runs_a_pass_only_when_idle_and_on(lib):
    import threading
    from core.repair_worker import RepairWorker
    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager = lib.db, lib.config
    worker.enabled, worker._held = True, False
    worker._force_run_lock, worker._force_run_queue = threading.Lock(), []
    worker._stop_event = threading.Event()
    observe(lib)

    # not due yet: the first pass waits out the start
    assert worker._maybe_fill_tags() is None
    worker._held = True
    worker.request_tag_fill()
    assert worker._maybe_fill_tags() is None and not tags(lib).get('genre')
    worker._held, worker.enabled = False, False
    worker.request_tag_fill()
    assert worker._maybe_fill_tags() is None and not tags(lib).get('genre')
    worker.enabled = True
    worker.request_tag_fill()
    stats = worker._maybe_fill_tags()
    assert stats['files'] == 1 and tags(lib)['genre'] == 'Rock'


def test_the_queue_gives_way_to_a_queued_job(lib):
    import threading
    from core.repair_worker import RepairWorker
    worker = object.__new__(RepairWorker)
    worker.db, worker._config_manager = lib.db, lib.config
    worker.enabled, worker._held = True, False
    worker._force_run_lock, worker._force_run_queue = threading.Lock(), ['library_retag']
    worker._stop_event = threading.Event()
    observe(lib)
    worker.request_tag_fill()
    assert worker._maybe_fill_tags()['files'] == 0


# ── the import's own fill honours Off ────────────────────────────────────────

def test_the_import_fill_honours_off(lib):
    from core.library2.validation import fill_known_blanks
    lib.config.values[tag_fill.SETTING] = 'off'
    assert fill_known_blanks(lib.db, [1], lib.config) == 0
    assert not tags(lib).get('genre')
    lib.config.values[tag_fill.SETTING] = 'fill'
    assert fill_known_blanks(lib.db, [1], lib.config) == 1
    assert tags(lib)['genre'] == 'Rock'


# ── the Library Re-tag job ───────────────────────────────────────────────────

def _job_run(lib, monkeypatch, settings=None):
    # the job's own preview offers a cover whatever embed_album_art says;
    # these tests are about tags
    settings = {'cover_art': 'skip', **(settings or {})}
    from core.repair_jobs.base import JobContext
    from core.repair_jobs.library_retag import LibraryRetagJob
    monkeypatch.setattr('core.library2.maintenance_subjects.active_file_subjects', lambda *a, **k: [
        {'track_id': 7, 'file_id': 1, 'path': str(lib.path), 'album_id': 1, 'title': 'Track 7',
         'artist_name': 'Artist', 'album_title': 'Album'}])
    lib.config.values['repair.jobs.library_retag.settings'] = settings
    filed, changes = [], []
    ctx = JobContext(db=lib.db, transfer_folder=None, config_manager=lib.config,
                     create_finding=lambda **kw: (filed.append(kw) or True),
                     report_change=lambda **kw: changes.append(kw))
    result = LibraryRetagJob().scan(ctx)
    return result, filed, changes


def test_the_job_fills_blanks_instead_of_filing_them(lib, monkeypatch):
    result, filed, changes = _job_run(lib, monkeypatch)
    assert result.auto_fixed == 1 and filed == [], filed
    assert tags(lib)['genre'] == 'Rock'
    (row,) = fills(lib)
    assert row['source'] == 'job' and row['batch_id'].startswith('job:')
    assert changes[0]['action'] == 'filled_tags'


def test_the_job_files_what_differs_after_the_fill(lib, monkeypatch):
    write_tag_fields(str(lib.path), {'title': 'Track Seven'})
    _age(lib.path)
    result, filed, _ = _job_run(lib, monkeypatch)
    assert result.auto_fixed == 1 and tags(lib)['genre'] == 'Rock'
    (finding,) = filed
    assert [d['file_key'] for d in finding['details']['diff']] == ['title']
    assert tags(lib)['title'] == 'Track Seven'


def test_the_job_with_off_files_the_blanks_as_before(lib, monkeypatch):
    lib.config.values[tag_fill.SETTING] = 'off'
    result, filed, _ = _job_run(lib, monkeypatch)
    assert result.auto_fixed == 0 and len(filed) == 1
    assert not tags(lib).get('genre') and fills(lib) == []


def test_the_jobs_own_auto_apply_is_kept_and_can_be_undone(lib, monkeypatch):
    lib.config.values[tag_fill.SETTING] = 'off'
    write_tag_fields(str(lib.path), {'title': 'Track Seven'})
    _age(lib.path)
    result, filed, _ = _job_run(lib, monkeypatch, {'auto_apply': True, 'cover_art': 'skip'})
    assert result.auto_fixed == 1 and filed == []
    assert tags(lib)['title'] == 'Track 7'
    (row,) = fills(lib)
    assert row['mode'] == 'apply'
    assert tag_fill.undo_fill(lib.db, row['id'], lib.config)['success'] is True
    assert tags(lib)['title'] == 'Track Seven' and not tags(lib).get('genre')


def test_a_tag_written_between_the_plan_and_the_write_wins(lib, monkeypatch):
    """an external tagger got there first: fill mode re-checks at write time"""
    observe(lib)
    real = tag_fill.plan_file

    def racing(*a, **k):
        plan = real(*a, **k)
        write_tag_fields(str(lib.path), {'genre': 'Krautrock'})
        return plan

    monkeypatch.setattr(tag_fill, 'plan_file', racing)
    drain(lib)
    assert tags(lib)['genre'] == 'Krautrock'
    assert str(tags(lib)['year']).startswith('2026')


def test_an_unknown_edition_writes_no_number():
    checks = {'track_number': 'missing', 'total_tracks': 'missing', 'disc_number': 'missing',
              'genre': 'missing', 'edition': 'unknown'}
    assert tag_fill._check_fields(checks, 'fill') == ['genre']
    assert 'track_number' in tag_fill._check_fields({**checks, 'edition': 'correct'}, 'fill')
