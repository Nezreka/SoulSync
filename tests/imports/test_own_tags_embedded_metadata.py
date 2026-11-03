"""Own-tags import must read the selected file, not stale inbox metadata."""

import logging
import shutil
import sqlite3
from types import SimpleNamespace

import pytest
from mutagen.aiff import AIFF
from mutagen.id3 import APIC, TALB, TCOM, TDRC, TIT2, TPE1, TPE2, TPE4, TPOS, TRCK, TXXX

from core.imports import paths, resolution, routes, side_effects
from core.imports.inbox import _file_row
from core.imports.staging import read_staging_file_metadata
from core.library2.schema import ensure_library_v2_schema
from core.metadata.enrichment import enhance_file_metadata
from core.repair_jobs.orphan_file_detector import is_tracked_path, known_file_suffixes
from tests.metadata.test_aiff_tags_1919 import _aiff


@pytest.fixture(params=[
    ('Howard Shore', 'Flaming Red Hair (Gypsyndicate LOTRemix)', '', 'Howard Shore', 1),
    ('Shankara NZ', 'Dusk', 'Spiral Roots [compiled by Indra]', 'Various Artists', 5),
])
def own_track(tmp_path, request):
    artist, title, album, albumartist, number = request.param
    staging = tmp_path / 'Staging'
    staging.mkdir()
    path = staging / f'Unknown Artist - {artist} - {title}.aiff'
    _aiff(path)
    audio = AIFF(path)
    audio.add_tags()
    for frame in (
        TIT2(text=[title]), TPE1(text=[artist]), TPE2(text=[albumartist]),
        TALB(text=[album]), TRCK(text=[f'{number}/12']), TPOS(text=['2/2']),
        TDRC(text=['2019']), TCOM(text=[artist]), TPE4(text=['Gypsyndicate']),
        APIC(mime='image/png', type=3, data=b'original-cover'),
        TXXX(desc='REKORDBOX_CUSTOM', text=['keep-me']),
    ):
        audio.tags.add(frame)
    audio.save()
    return path, artist, title, album, albumartist, number


@pytest.mark.parametrize('request_artist', [None, '', 'Unknown Artist', 'Stale Artist'])
def test_own_tags_reads_current_file_and_registers_final_path(own_track, tmp_path, monkeypatch, request_artist):
    path, artist, title, album, albumartist, number = own_track
    original = path.read_bytes()
    db_path = tmp_path / 'catalogue.db'
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.commit()
    conn.close()

    def connect():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    db = SimpleNamespace(_get_connection=connect)
    cfg = SimpleNamespace(
        get_active_media_server=lambda: 'soulsync',
        get=lambda key, default=None: str(tmp_path / 'Transfer') if key == 'soulseek.transfer_path' else default,
    )
    monkeypatch.setattr(paths, '_get_config_manager', lambda: cfg)
    monkeypatch.setattr(paths, 'transfer_root_for_context', lambda _ctx: str(tmp_path / 'Transfer'))
    monkeypatch.setattr(side_effects, '_get_config_manager', lambda: cfg)
    monkeypatch.setattr(side_effects, 'get_database', lambda: db)
    import core.sample.worker as sample_worker
    monkeypatch.setattr(sample_worker, 'enqueue_analysis', lambda *_args: None)
    monkeypatch.setattr(resolution, '_get_source_chain_for_lookup', lambda **_kw: ['spotify'])

    def forbidden(*args, **kwargs):
        pytest.fail('Own-tags import requested an external catalogue client')

    monkeypatch.setattr(resolution.metadata_registry, 'get_client_for_source', forbidden)
    monkeypatch.setattr(routes, 'finalize_manual_rematch_replace', lambda *_args: None)
    captured = {}

    def post_process(_key, context, source):
        captured.update(context)
        assert context['artist']['name'] == artist
        assert context['track_info']['name'] == title
        assert context['track_info']['track_number'] == number
        assert context['track_info']['disc_number'] == 2
        assert context['album']['name'] == album
        assert context['album']['artists'] == [{'name': albumartist}]
        assert context['_manual_metadata'] is True
        assert enhance_file_metadata(source, context, context['artist'], {}) is True
        assert path.read_bytes() == original  # artwork, composer, remixer, custom tags AND audio
        final_path, _ = paths.build_final_path_for_track(context, context['artist'], None, '.aiff')
        assert 'Unknown Artist' not in final_path
        assert title in final_path
        shutil.move(source, final_path)
        context['_final_processed_path'] = final_path
        side_effects.record_soulsync_library_entry(context, context['artist'], {})
        captured['final_path'] = final_path

    runtime = routes.ImportRouteRuntime(
        get_allowed_import_roots=lambda: [str(path.parent)],
        post_process_matched_download=post_process,
        logger=logging.getLogger(__name__),
    )
    request = {
        'full_path': str(path), 'filename': path.name, 'title': path.stem,
        'album': 'Stale Album', 'track_number': 99, 'disc_number': 1,
        'from_own_tags': True,
    }
    if request_artist is not None:
        request['artist'] = request_artist
    assert routes.process_single_import_file(runtime, request) == ('ok', title)
    assert not path.exists()
    assert is_tracked_path(captured['final_path'], known_file_suffixes(db))
    # An inbox refresh cannot rediscover the moved file in staging.
    assert list(path.parent.glob('*.aiff')) == []
    conn = connect()
    row = conn.execute('SELECT title, track_artist, track_number FROM lib2_tracks').fetchone()
    assert row is not None
    names = {row[0] for row in conn.execute('SELECT name FROM lib2_artists')}
    # A same-artist track inherits its album artist; a VA release stores the
    # performing credit on the track rather than creating a second album.
    assert (row['title'], row['track_artist'] or albumartist, row['track_number']) == (title, artist, number)
    assert albumartist in names
    assert 'Unknown Artist' not in names
    conn.close()


def test_inbox_keeps_performing_artist_for_compilation(own_track):
    path, artist, title, album, albumartist, number = own_track
    record = read_staging_file_metadata(str(path))
    row = _file_row(str(path), record)
    assert (row['artist'], row['title'], row['album'], row['track_number']) == (artist, title, album, number)


@pytest.mark.parametrize('broken', [False, True])
def test_untagged_or_unreadable_file_falls_back_without_lookup(tmp_path, monkeypatch, broken):
    path = tmp_path / 'Artist - Title.aiff'
    path.write_bytes(b'not audio') if broken else _aiff(path)
    monkeypatch.setattr(resolution, '_get_source_chain_for_lookup', lambda **kw: [])
    monkeypatch.setattr(routes, 'finalize_manual_rematch_replace', lambda *_args: None)
    contexts = []
    runtime = routes.ImportRouteRuntime(
        get_allowed_import_roots=lambda: [str(tmp_path)],
        post_process_matched_download=lambda _key, ctx, _path: contexts.append(ctx),
        logger=logging.getLogger(__name__),
    )
    assert routes.process_single_import_file(runtime, {
        'full_path': str(path), 'filename': path.name, 'from_own_tags': True,
    }) == ('ok', 'Title')
    assert contexts[0]['artist']['name'] == 'Artist'


def test_explicit_catalogue_match_wins_and_does_not_read_own_tags(own_track, monkeypatch):
    path, *_ = own_track

    def forbidden(*args, **kwargs):
        pytest.fail('An explicit match must not be replaced with embedded metadata')

    monkeypatch.setattr(routes, '_read_staging_file_metadata', forbidden)
    monkeypatch.setattr(routes, 'finalize_manual_rematch_replace', lambda *_args: None)
    captured = []

    def resolve(title, artist, **kwargs):
        assert kwargs['override_id'] == 'chosen-id'
        assert kwargs['override_source'] == 'spotify'
        assert kwargs['from_own_tags'] is False
        return {'source': 'spotify', 'context': {
            'artist': {'name': 'Chosen Artist'}, 'track_info': {'name': 'Chosen Title'},
        }}

    runtime = routes.ImportRouteRuntime(
        get_allowed_import_roots=lambda: [str(path.parent)],
        get_single_track_import_context=resolve,
        post_process_matched_download=lambda _key, ctx, _path: captured.append(ctx),
        logger=logging.getLogger(__name__),
    )
    assert routes.process_single_import_file(runtime, {
        'full_path': str(path), 'filename': path.name, 'from_own_tags': True,
        'manual_match': {'id': 'chosen-id', 'source': 'spotify'},
    }) == ('ok', 'Chosen Title')
    assert not captured[0].get('_preserve_embedded_tags')
