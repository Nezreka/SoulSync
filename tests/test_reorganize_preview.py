"""The reorganize preview — what the apply will do, computed from the library.

This file is what remains of `test_library_reorganize_orchestrator.py`, which
tested `reorganize_album`: the executor that copied each file into a staging
folder and pushed it through the download post-process. That executor is gone
(see the module docstring of `core/library_reorganize.py`), and with it the
35 tests that pinned staging, per-track post-processing, quarantine handling on
a COPY, and source resolution against a provider.

What survives is the part a reorganize still does: work out where each file
belongs and say so before touching anything.
"""

import sqlite3
import sys
import types

import pytest


# --- module stubs (same shape used elsewhere in the test suite) -----------
if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")

    class _DummySpotify:
        def __init__(self, *args, **kwargs):
            pass

    oauth2 = types.ModuleType("spotipy.oauth2")

    class _DummyOAuth:
        def __init__(self, *args, **kwargs):
            pass

    spotipy.Spotify = _DummySpotify
    oauth2.SpotifyOAuth = _DummyOAuth
    oauth2.SpotifyClientCredentials = _DummyOAuth
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2

if "core.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("core.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["core.settings"] = settings_mod


from core import library_reorganize  # noqa: E402


# --- helpers --------------------------------------------------------------

class _FakeDB:
    """Wraps a sqlite3 in-memory connection that survives `close()` calls
    so the tests can reuse it for assertions afterwards."""

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row

    def _get_connection(self):
        return _NonClosingConnWrapper(self._conn)


class _NonClosingConnWrapper:
    def __init__(self, real):
        self._real = real

    def cursor(self):
        return self._real.cursor()

    def execute(self, *args, **kwargs):
        return self._real.execute(*args, **kwargs)

    def commit(self):
        return self._real.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _setup_album(db, *, album_id='alb-1', album_title='Aerosmith (1973)',
                 spotify_id='', deezer_id='', itunes_id='', discogs_id='',
                 soul_id='', tracks=()):
    """Minimal artists/albums/tracks schema seeded with one album.

    `tracks` items are `(track_id, track_number, title, file_path)` or
    `(track_id, track_number, disc_number, title, file_path)`.
    """
    cur = db._conn.cursor()
    cur.execute("CREATE TABLE artists (id TEXT PRIMARY KEY, name TEXT)")
    cur.execute("""
        CREATE TABLE albums (
            id TEXT PRIMARY KEY, artist_id TEXT, title TEXT,
            release_date TEXT, track_count INTEGER,
            spotify_album_id TEXT, deezer_id TEXT, itunes_album_id TEXT,
            discogs_id TEXT, soul_id TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE tracks (
            id TEXT PRIMARY KEY, album_id TEXT, artist_id TEXT, title TEXT,
            track_number INTEGER, disc_number INTEGER DEFAULT 1,
            file_path TEXT, updated_at TEXT
        )
    """)
    cur.execute("INSERT INTO artists VALUES (?, ?)", ('artist-1', 'Aerosmith'))
    cur.execute(
        "INSERT INTO albums (id, artist_id, title, release_date, track_count, "
        "spotify_album_id, deezer_id, itunes_album_id, discogs_id, soul_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (album_id, 'artist-1', album_title, '1973-01-05', len(tracks),
         spotify_id, deezer_id, itunes_id, discogs_id, soul_id),
    )
    for row in tracks:
        tid, tn, disc, title, fp = row if len(row) == 5 else (row[0], row[1], 1, row[2], row[3])
        cur.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, track_number, "
            "disc_number, file_path) VALUES (?,?,?,?,?,?,?)",
            (tid, album_id, 'artist-1', title, tn, disc, fp),
        )
    db._conn.commit()


@pytest.fixture
def tmpdirs(tmp_path):
    library = tmp_path / "library"
    transfer = tmp_path / "transfer"
    library.mkdir()
    transfer.mkdir()
    return library, transfer


def _make_audio_file(library_dir, name='song.flac', content=b'fakeflacdata'):
    p = library_dir / name
    p.write_bytes(content)
    return str(p)


def _fake_path_builder(context, spotify_artist, _album_info, file_ext, **_kw):
    """Stand-in for `build_final_path_for_track`. Inserts Disc N/ when
    total_discs > 1 — same convention the real builder uses."""
    album = context['spotify_album']['name']
    artist = spotify_artist['name']
    track_info = context['track_info']
    title = track_info['name']
    tn = track_info['track_number']
    dn = track_info['disc_number']
    total = context['spotify_album']['total_discs']
    parts = ['/transfer', artist, album]
    if total > 1:
        parts.append(f'Disc {dn}')
    parts.append(f"{tn:02d} - {title}{file_ext}")
    return '/'.join(parts), True


def _path_builder_album_vs_single(context, spotify_artist, album_info, file_ext, **_kw):
    """Stand-in that emulates the real builder's branch on
    `album_info.get('is_album')`. SINGLE mode produces a per-track folder
    named after the title — the bug output."""
    artist = spotify_artist['name']
    if album_info and album_info.get('is_album'):
        album = album_info['album_name']
        title = album_info['clean_track_name']
        tn = album_info['track_number']
        dn = album_info['disc_number']
        total = context['spotify_album']['total_discs']
        if total > 1:
            return (f'/transfer/{artist}/{artist} - {album}/Disc {dn}/{tn:02d} - {title}{file_ext}', True)
        return (f'/transfer/{artist}/{artist} - {album}/{tn:02d} - {title}{file_ext}', True)
    title = context['track_info']['name']
    return (f'/transfer/{artist}/{artist} - {title}/{title}{file_ext}', True)


def _preview(db, transfer_dir='/transfer', build=_fake_path_builder):
    return library_reorganize.preview_album_reorganize(
        album_id='alb-1', db=db, transfer_dir=str(transfer_dir),
        resolve_file_path_fn=lambda p: p,
        build_final_path_fn=build,
    )


# --- the plan comes from the library ---------------------------------------

def test_an_album_with_no_source_id_gets_a_plan(tmpdirs):
    """The old preview refused it outright (`status: 'no_source_id'`, "run
    enrichment first") because it needed a provider to ask for a tracklist.
    Moving a file needs no provider."""
    library, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, tracks=[
        ('t1', 1, 'Same Old Song And Dance', _make_audio_file(library, 't1.flac')),
    ])

    result = _preview(db)

    assert result['success'] is True
    assert result['status'] == 'planned'
    assert result['source'] == 'catalogue'
    (track,) = result['tracks']
    assert track['matched'] is True
    assert track['new_path']


def test_preview_uses_album_mode_not_single_mode(tmpdirs):
    """Regression for the bug where every track ended up in its own
    track-named folder (SINGLE MODE) because we passed None for album_info to
    the path builder."""
    library, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, album_title='good kid, m.A.A.d city', tracks=[
        ('t1', 1, 'Sherane', _make_audio_file(library, 't1.flac')),
        ('t2', 2, 'Bitch Dont Kill My Vibe', _make_audio_file(library, 't2.flac')),
    ])

    result = _preview(db, build=_path_builder_album_vs_single)

    paths = [it['new_path'] for it in result['tracks']]
    assert all('good kid, m.A.A.d city' in p for p in paths)
    assert any('01 - Sherane' in p for p in paths)
    assert any('02 - Bitch Dont Kill My Vibe' in p for p in paths)
    assert not any(p.endswith('/Sherane.flac') for p in paths)


def test_preview_emits_disc_subfolders_for_multi_disc_albums(tmpdirs):
    """The bug winecountrygames hit: preview showed all tracks at the album
    root with no Disc N/ subfolders. The disc layout is the library's own now,
    so it cannot change between two previews of the same album."""
    library, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, album_title='good kid, m.A.A.d city (Deluxe)', tracks=[
        ('t1d1', 1, 1, 'Sherane', _make_audio_file(library, 'd1t1.flac')),
        ('t1d2', 1, 2, 'The Recipe', _make_audio_file(library, 'd2t1.flac')),
    ])

    result = _preview(db)

    assert result['success'] is True
    by_title = {it['title']: it for it in result['tracks']}
    assert 'Disc 1' in by_title['Sherane']['new_path']
    assert 'Disc 2' in by_title['The Recipe']['new_path']
    assert by_title['Sherane']['disc_number'] == 1
    assert by_title['The Recipe']['disc_number'] == 2


def test_preview_marks_a_track_the_library_cannot_name(tmpdirs):
    """A track with no title has no filename to build. It is surfaced with a
    reason rather than dropped or given a guess."""
    library, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, tracks=[
        ('t1', 1, 'A Real Track', _make_audio_file(library, 't1.flac')),
        ('t99', 99, '', _make_audio_file(library, 't99.flac')),
    ])

    result = _preview(db)

    by_id = {it['track_id']: it for it in result['tracks']}
    assert by_id['t1']['matched'] is True
    assert by_id['t1']['new_path']
    assert by_id['t99']['matched'] is False
    assert by_id['t99']['reason']
    assert by_id['t99']['new_path'] == ''


def test_preview_skips_track_in_deleted_quarantine(tmpdirs):
    """A track whose file lives in <transfer>/deleted (duplicate-cleaner
    quarantine) is surfaced as a non-matched skip — Reorganize must not offer
    to move it back out of /deleted (#746)."""
    library, transfer = tmpdirs
    db = _FakeDB()
    quarantine = transfer / 'deleted' / 'Aerosmith'
    quarantine.mkdir(parents=True)
    deleted_file = quarantine / 'dream.flac'
    deleted_file.write_bytes(b'dupe')
    _setup_album(db, tracks=[
        ('t1', 1, 'Same Old Song And Dance', _make_audio_file(library, 't1.flac')),
        ('t2', 2, 'Dream On', str(deleted_file)),
    ])

    result = _preview(db, transfer_dir=transfer)

    by_title = {it['title']: it for it in result['tracks']}
    assert by_title['Same Old Song And Dance']['matched'] is True
    assert by_title['Same Old Song And Dance']['new_path']
    assert by_title['Dream On']['matched'] is False
    assert 'quarantine' in (by_title['Dream On']['reason'] or '').lower()
    assert by_title['Dream On']['new_path'] == ''


def test_preview_plans_move_out_of_old_template_folder(monkeypatch, tmpdirs):
    """TheHomeGuy's report: after changing the album template, both the
    Tools-page job and Reorganize All did nothing — every track came back
    `unchanged`, because #829's existing-folder reuse resolved the folder the
    album was being moved OUT of. Wires the REAL builder with a poisoned
    resolver and pins that a reorganize context never consults it."""
    import core.imports.paths as import_paths
    import core.library.existing_album_folder as eaf
    import database.music_database as mdb

    _library, transfer = tmpdirs

    class _Cfg:
        def __init__(self, values):
            self._values = values

        def get(self, key, default=None):
            return self._values.get(key, default)

        def get_active_media_server(self):
            return None

    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _Cfg({
        "soulseek.transfer_path": str(transfer),
        "file_organization.enabled": True,
        "file_organization.templates": {
            "album_path": "$albumartist/$album/$track - $title",
            "single_path": "$artist/$title",
        },
        "file_organization.collab_artist_mode": "first",
        "file_organization.disc_label": "Disc",
    }))
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)

    old_home = transfer / "Aerosmith" / "Aerosmith - Rocks"
    old_home.mkdir(parents=True)
    current = old_home / "01 - Back in the Saddle.flac"
    current.write_bytes(b"fakeflacdata")

    monkeypatch.setattr(mdb, "get_database", lambda: object(), raising=False)
    resolver_calls = []
    monkeypatch.setattr(eaf, "resolve_existing_album_folder",
                        lambda **kw: resolver_calls.append(kw) or str(old_home))

    db = _FakeDB()
    _setup_album(db, album_title='Rocks', tracks=[
        ('t1', 1, 'Back in the Saddle', str(current)),
    ])

    result = _preview(db, transfer_dir=transfer,
                      build=import_paths.build_final_path_for_track)

    assert result['success'] is True
    (track,) = result['tracks']
    assert track['matched'] is True
    assert resolver_calls == []          # reuse never consulted for reorganize
    assert track['unchanged'] is False   # the bug reported True here
    assert track['new_path_abs'] == str(
        transfer / "Aerosmith" / "Rocks" / "01 - Back in the Saddle.flac")


# --- the context handed to the shared path builder -------------------------

def test_reorganize_context_disables_folder_reuse():
    """Every reorganize context carries the no-reuse flag: the destination
    comes from the CURRENT template alone, never from where the album already
    sits (#829)."""
    context = library_reorganize._build_post_process_context(
        {'id': 'dz-1', 'name': 'Rocks'},
        {'id': 'a1', 'name': 'Back in the Saddle', 'track_number': 1},
        'Aerosmith', 'Rocks', 1,
    )
    assert context['_no_album_folder_reuse'] is True


def test_reorganize_context_no_longer_opts_out_of_an_acceptance_check():
    """`is_local_import` (#804) and `_skip_quarantine_check` (#1182) were
    opt-outs FROM the download post-process. A reorganize does not run it any
    more, so a flag that says "skip this check" would describe a check that no
    longer happens."""
    context = library_reorganize._build_post_process_context(
        {'id': 'dz-1', 'name': 'Rocks'},
        {'id': 'a1', 'name': 'Back in the Saddle', 'track_number': 1},
        'Aerosmith', 'Rocks', 1,
    )
    assert 'is_local_import' not in context
    assert '_skip_quarantine_check' not in context


# --- source listing (still used by the bulk paths) -------------------------

def test_available_sources_only_lists_authed_sources_with_stored_ids(monkeypatch):
    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority',
                        lambda p: [p, 'spotify', 'itunes', 'discogs', 'hydrabase'])
    auth = {'deezer': object(), 'spotify': object()}
    monkeypatch.setattr(library_reorganize, 'get_client_for_source',
                        lambda src: auth.get(src))

    album = {
        'spotify_album_id': 'sp-1',
        'deezer_id': 'dz-1',
        'itunes_album_id': 'it-1',  # has ID but user not authed
        'discogs_id': '',
        'soul_id': '',
    }

    sources = library_reorganize.available_sources_for_album(album)
    assert [s['source'] for s in sources] == ['deezer', 'spotify']
    assert all('label' in s for s in sources)


def test_authed_sources_lists_all_authed_regardless_of_album_ids(monkeypatch):
    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(library_reorganize, 'get_source_priority',
                        lambda p: [p, 'deezer', 'itunes', 'discogs', 'hydrabase'])
    auth = {'spotify': object(), 'deezer': object(), 'itunes': object()}
    monkeypatch.setattr(library_reorganize, 'get_client_for_source',
                        lambda src: auth.get(src))

    sources = library_reorganize.authed_sources()
    assert [s['source'] for s in sources] == ['spotify', 'deezer', 'itunes']
    assert all('label' in s for s in sources)
