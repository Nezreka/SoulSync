"""Follow-up to #1292 (Ktzenjammer, Sep 2026): the import page's "already in
the library" check still flagged DIFFERENT songs as owned.

Same-artist cases: the #808 artist-wide fallback (the incoming album's name
didn't resemble the library's) paired stem-sharing titles at 0.70-0.74 —
"Beyond I"/"Beyond Fate", "Beyond II"/"Beyond Fate", "Solve"/"Solace",
"Bestrafe mich"/"Heirate mich", "What Else Is New?"/"What If I Knew". The
wide pool now requires near-identical titles (0.85).

Cross-artist case: "Black Rainbows" isn't in the library at all, so
search_tracks() degraded to a whole-table word-OR ("black") and "Snowball"
matched Black Sabbath's "Snowblind". Rows not credited to the requested
artist are now excluded before matching.

Hits the real /api/library/check-tracks route with a stub db, like the
original #1292 test.
"""

from __future__ import annotations

import os
import tempfile
import types

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-1292b-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'a.db'))
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


def _row(title, artist, album, path, track_artist=None):
    return types.SimpleNamespace(
        id=f'{artist}/{title}', title=title, album_title=album,
        file_path=path, bitrate=900,
        artist_name=artist, track_artist=track_artist)


_LIBRARY_ROWS = [
    _row('What If I Knew', 'Dinosaur Jr.', 'Beyond',
         '/music/Dinosaur Jr/[2007] Beyond/11 - What If I Knew.flac'),
    _row('Beyond Fate', 'Cult Of Luna', 'Cult Of Luna',
         '/music/Cult Of Luna/[2007] Cult Of Luna/06 - Beyond Fate.flac'),
    _row('Heirate mich', 'Rammstein', 'Herzeleid (Remastered 2020)',
         '/music/Rammstein/[1995] Herzeleid (Remastered 2020)/08 - Heirate mich.flac'),
    _row('Solace', 'Zeal & Ardor', 'GREIF',
         '/music/Zeal & Ardor/[2024] GREIF/12 - Solace.flac'),
    _row('Snowblind', 'Black Sabbath', 'Vol. 4',
         '/music/Black Sabbath/[1972] Vol. 4/06 - Snowblind - 2009 Remaster.flac'),
    # positive controls: these MUST still flag as owned
    _row('Yellow', 'Coldplay', 'Parachutes',
         '/music/Coldplay/[2000] Parachutes/04 - Yellow.flac'),
    _row('Believe', 'Typo Band', 'Some Album',
         '/music/Typo Band/Some Album/01 - Believe.flac'),
]


def _client(monkeypatch, rows):
    class _DB:
        def search_tracks(self, **kwargs):
            # NB: ignores kwargs like the real search_tracks() would scope by
            # artist — the route's own artist guard is what narrows the pool.
            return rows

    monkeypatch.setattr('database.music_database.MusicDatabase', lambda *a, **k: _DB())
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: 'plex')
    client = web_server.app.test_client()

    def _post(names, artist, album):
        r = client.post('/api/library/check-tracks', json={
            'artist_name': artist, 'album_name': album,
            'tracks': [{'name': n} for n in names],
        })
        assert r.status_code == 200
        return r.get_json()['owned_tracks']

    return _post


@pytest.fixture
def check(monkeypatch):
    return _client(monkeypatch, _LIBRARY_ROWS)


def test_what_else_is_new_is_not_what_if_i_knew(check):
    owned = check(['What Else Is New?'], 'Dinosaur Jr.', 'Where You Been')
    assert owned['What Else Is New?'] == {'owned': False}


def test_beyond_i_is_not_beyond_fate(check):
    owned = check(['Beyond I'], 'Cult Of Luna', 'The Long Road North')
    assert owned['Beyond I'] == {'owned': False}


def test_beyond_ii_is_not_beyond_fate(check):
    owned = check(['Beyond II'], 'Cult Of Luna', 'The Long Road North')
    assert owned['Beyond II'] == {'owned': False}


def test_solve_is_not_solace(check):
    owned = check(['Solve'], 'Zeal & Ardor', 'Stranger Fruit')
    assert owned['Solve'] == {'owned': False}


def test_bestrafe_mich_is_not_heirate_mich(check):
    owned = check(['Bestrafe mich'], 'Rammstein', 'Sehnsucht')
    assert owned['Bestrafe mich'] == {'owned': False}


def test_snowball_is_not_snowblind_other_artist(check):
    # 'Black Rainbows' is not in the library at all: the only similar-titled
    # row is Black Sabbath's. Cross-artist rows must never match, however
    # similar the title.
    owned = check(['Snowball'], 'Black Rainbows', 'Cosmic Ritual Supertrip')
    assert owned['Snowball'] == {'owned': False}


def test_exact_title_on_another_album_still_owned(check):
    # Importing a compilation while owning the studio album: same song, exact
    # title — the #808 fallback's whole reason to exist.
    owned = check(['Yellow'], 'Coldplay', 'Greatest Hits')
    assert owned['Yellow']['owned'] is True
    assert owned['Yellow']['file_path'].endswith('04 - Yellow.flac')


def test_typo_title_on_another_album_still_owned(check):
    # Near-identical char ratio (0.857) still clears the wide-pool bar.
    owned = check(['Beleive'], 'Typo Band', 'Greatest Hits')
    assert owned['Beleive']['owned'] is True


def test_compilation_track_artist_counts_as_owned(monkeypatch):
    # The artist guard must not drop per-track-artist rows: a Zeal & Ardor
    # song filed under Various Artists is still theirs.
    rows = [
        _row('Solve', 'Various Artists', 'Metal Compilation',
             '/music/VA/Metal Compilation/01 - Solve.flac',
             track_artist='Zeal & Ardor'),
    ]
    check = _client(monkeypatch, rows)
    owned = check(['Solve'], 'Zeal & Ardor', 'Stranger Fruit')
    assert owned['Solve']['owned'] is True
