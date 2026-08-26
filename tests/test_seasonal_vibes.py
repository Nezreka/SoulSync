"""taste-aware seasonal sourcing - the fix for 'every summer playlist is
just albums named beach' (aug 25 user report)."""

import contextlib
import json
import sqlite3

import pytest

from core import seasonal_vibes as sv


def _enrichment(tags=None, playcount=None):
    """A lib2 ``enrichment`` blob carrying the Last.fm payload.

    v2 folds every provider field into this one JSON column (see
    core/library2/enrich._ENRICHMENT_PAYLOAD), where legacy had a
    ``lastfm_tags`` / ``lastfm_playcount`` column per field.
    """
    lastfm = {}
    if tags is not None:
        lastfm["tags"] = tags
    if playcount is not None:
        lastfm["playcount"] = playcount
    return json.dumps({"lastfm": lastfm}) if lastfm else None


class FakeDb:
    """the one seam seasonal code uses: _get_connection."""

    def __init__(self, path):
        self.path = str(path)
        with self._get_connection() as conn:
            c = conn.cursor()
            c.executescript("""
                CREATE TABLE listening_history (
                    id INTEGER PRIMARY KEY, artist TEXT, title TEXT,
                    album TEXT, played_at TEXT);
                CREATE TABLE lib2_artists (
                    id INTEGER PRIMARY KEY, name TEXT, name_key TEXT,
                    image_url TEXT, spotify_id TEXT, musicbrainz_id TEXT,
                    external_ids TEXT, enrichment TEXT,
                    genres TEXT, mood TEXT, style TEXT);
                CREATE TABLE lib2_albums (
                    id INTEGER PRIMARY KEY, primary_artist_id INTEGER, title TEXT,
                    image_url TEXT, release_date TEXT, enrichment TEXT,
                    genres TEXT, mood TEXT, style TEXT,
                    spotify_id TEXT, musicbrainz_id TEXT, external_ids TEXT);
                CREATE TABLE lib2_tracks (
                    id INTEGER PRIMARY KEY, album_id INTEGER,
                    title TEXT, track_number INTEGER, duration INTEGER,
                    track_artist TEXT);
                CREATE TABLE lib2_track_files (
                    id INTEGER PRIMARY KEY, track_id INTEGER, path TEXT,
                    is_primary INTEGER DEFAULT 1, file_state TEXT DEFAULT 'active');
                CREATE TABLE discovery_pool (
                    id INTEGER PRIMARY KEY, source TEXT, spotify_track_id TEXT,
                    itunes_track_id TEXT, track_name TEXT, artist_name TEXT,
                    album_name TEXT, album_cover_url TEXT, duration_ms INTEGER,
                    popularity INTEGER, track_data_json TEXT);
            """)
            conn.commit()

    @contextlib.contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


@pytest.fixture
def db(tmp_path):
    return FakeDb(tmp_path / "vibes.db")


def _seed_artist(db, aid, name, **tags):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO lib2_artists (id, name, name_key, enrichment,"
            " genres, mood, style) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (aid, name, name.lower(), _enrichment(tags.get('lastfm_tags')),
             tags.get('genres'), tags.get('mood'), tags.get('style')))
        conn.commit()


def _own(conn, track_id, path=None):
    """Give a catalogued recording the live file row that makes it OWNED.

    v2 keeps discography and wishlist rows beside the owned ones, where the
    legacy tables this replaces WERE the owned library by construction.
    """
    conn.execute(
        "INSERT INTO lib2_track_files (track_id, path, is_primary, file_state)"
        " VALUES (?, ?, 1, 'active')", (track_id, path or f"/m/{track_id}.flac"))


def _seed_plays(db, artist, title, album, month, count, year=2025):
    with db._get_connection() as conn:
        for _ in range(count):
            conn.execute(
                "INSERT INTO listening_history (artist, title, album, played_at)"
                " VALUES (?, ?, ?, ?)",
                (artist, title, album, f"{year}-{month:02d}-15T12:00:00"))
        conn.commit()


class TestRealMonths:
    def test_southern_hemisphere_shifts_true_seasons(self):
        assert sv.real_months_for([6, 7, 8], 'southern', holiday=False) == [12, 1, 2]

    def test_holidays_stay_calendar_fixed(self):
        assert sv.real_months_for([2], 'southern', holiday=True) == [2]

    def test_northern_passes_through(self):
        assert sv.real_months_for([6, 7, 8], 'northern', holiday=False) == [6, 7, 8]


class TestRewind:
    def test_only_the_window_months_count(self, db):
        _seed_plays(db, 'Kesha', 'Praying', 'Rainbow', month=7, count=5)
        _seed_plays(db, 'Winter Act', 'Snow', 'Cold', month=12, count=50)
        rows = sv.rewind_tracks(db, [6, 7, 8])
        names = [r['track_name'] for r in rows]
        assert names == ['Praying']
        assert rows[0]['popularity'] == 65  # 60 + 5 plays

    def test_diversifies_past_heavy_rotation(self, db):
        # ten tracks by one artist, one track by another: the cap keeps
        # three of the first so the second still makes the list
        for i in range(10):
            _seed_plays(db, 'Big Artist', f'Hit {i}', 'Album', month=6, count=20 - i)
        _seed_plays(db, 'Small Artist', 'Gem', 'EP', month=6, count=1)
        rows = sv.rewind_tracks(db, [6, 7, 8])
        by_artist = [r['artist_name'] for r in rows]
        assert by_artist.count('Big Artist') == 3
        assert 'Small Artist' in by_artist

    def test_owned_art_and_duration_come_from_the_library(self, db, monkeypatch):
        monkeypatch.setattr(sv, '_normalize_art', lambda u: f"norm:{u}" if u else None)
        _seed_artist(db, 1, 'Kesha')
        with db._get_connection() as conn:
            conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title,"
                         " image_url) VALUES (10, 1, 'Rainbow',"
                         " '/library/metadata/1/thumb')")
            # v2 stores duration in MILLIseconds; legacy stored seconds.
            conn.execute("INSERT INTO lib2_tracks (id, album_id, title, duration)"
                         " VALUES (100, 10, 'Praying', 240000)")
            _own(conn, 100)
            conn.commit()
        _seed_plays(db, 'Kesha', 'Praying', 'Rainbow', month=7, count=3)
        row = sv.rewind_tracks(db, [7])[0]
        assert row['album_cover_url'] == 'norm:/library/metadata/1/thumb'
        assert row['duration_ms'] == 240000

    def test_joined_credits_fall_back_to_the_primary_artist(self, db, monkeypatch):
        monkeypatch.setattr(sv, '_normalize_art', lambda u: u)
        _seed_artist(db, 1, 'Bad Bunny')
        with db._get_connection() as conn:
            conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title,"
                         " image_url) VALUES (10, 1, 'Un Verano Sin Ti',"
                         " 'https://art/verano.jpg')")
            conn.execute("INSERT INTO lib2_tracks (id, album_id, title, duration)"
                         " VALUES (100, 10, 'Moscow Mule', 200000)")
            _own(conn, 100)
            conn.commit()
        _seed_plays(db, 'Bad Bunny, Jhayco', 'DAKITI', 'DAKITI', month=6, count=4)
        row = sv.rewind_tracks(db, [6])[0]
        assert row['album_cover_url'] == 'https://art/verano.jpg'


class TestVibeArtistNames:
    def test_matches_via_artist_tags_and_album_tags(self, db):
        _seed_artist(db, 1, 'Surf Rockers', lastfm_tags='["surf", "rock"]')
        _seed_artist(db, 2, 'Album Tagged')
        _seed_artist(db, 3, 'Nothing Seasonal', lastfm_tags='["metal"]')
        with db._get_connection() as conn:
            conn.execute("INSERT INTO lib2_albums (primary_artist_id, title,"
                         " enrichment) VALUES (2, 'Beach Days', ?)",
                         (_enrichment(["tropical"]),))
            conn.commit()
        names = sv._vibe_artist_names(db, 'summer')
        assert names == {'surf rockers', 'album tagged'}


class TestVibePoolTracks:
    def _seed_pool(self, db, artist, track, pop, track_id='t1'):
        with db._get_connection() as conn:
            conn.execute(
                "INSERT INTO discovery_pool (source, spotify_track_id, track_name,"
                " artist_name, album_name, album_cover_url, duration_ms, popularity,"
                " track_data_json) VALUES ('spotify', ?, ?, ?, 'Alb',"
                " 'https://art.jpg', 1000, ?, '{}')",
                (track_id, track, artist, pop))
            conn.commit()

    def test_only_vibe_tagged_artists_and_capped(self, db):
        _seed_artist(db, 1, 'Surfy', lastfm_tags='["surf"]')
        for i in range(4):
            self._seed_pool(db, 'Surfy', f'Wave {i}', 90 - i, track_id=f's{i}')
        self._seed_pool(db, 'Metalhead', 'Doom', 99, track_id='m1')
        rows = sv.vibe_pool_tracks(db, 'summer', 'spotify', limit=40)
        assert [r['artist_name'] for r in rows] == ['Surfy', 'Surfy']
        # the real source id survives - pool rows are the download leg
        assert rows[0]['spotify_track_id'] == 's0'

    def test_no_tagged_artists_means_no_rows(self, db):
        self._seed_pool(db, 'Anyone', 'Song', 50)
        assert sv.vibe_pool_tracks(db, 'summer', 'spotify') == []


class TestVibeOwnedTracks:
    def test_tagged_albums_give_capped_tracks(self, db, monkeypatch):
        monkeypatch.setattr(sv, '_normalize_art', lambda u: u)
        _seed_artist(db, 1, 'Chill Act')
        with db._get_connection() as conn:
            conn.execute(
                "INSERT INTO lib2_albums (id, primary_artist_id, title, image_url,"
                " enrichment) VALUES (10, 1, 'Chill Album', 'https://a.jpg', ?)",
                (_enrichment(["feel good"], playcount=500),))
            for i in range(5):
                conn.execute("INSERT INTO lib2_tracks (id, album_id, title,"
                             " track_number, duration) VALUES (?, 10, ?, ?, 100000)",
                             (100 + i, f'Track {i}', i + 1))
                _own(conn, 100 + i)
            conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title,"
                         " enrichment) VALUES (11, 1, 'Metal Album', ?)",
                         (_enrichment(["metal"]),))
            conn.execute("INSERT INTO lib2_tracks (id, album_id, title)"
                         " VALUES (200, 11, 'Doom')")
            _own(conn, 200)
            conn.commit()
        rows = sv.vibe_owned_tracks(db, 'summer')
        assert [r['track_name'] for r in rows] == ['Track 0', 'Track 1']
        assert all(r['popularity'] == 50 for r in rows)
        assert all(r['album_cover_url'] == 'https://a.jpg' for r in rows)


class TestVibeLibraryAlbums:
    def test_played_and_tagged_albums_need_a_source_id(self, db, monkeypatch):
        monkeypatch.setattr(sv, '_normalize_art', lambda u: u)
        _seed_artist(db, 1, 'Kesha')
        _seed_artist(db, 2, 'Surfy')
        with db._get_connection() as conn:
            # played this window, has a spotify id -> leg a
            conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title,"
                         " image_url, spotify_id)"
                         " VALUES (10, 1, 'Rainbow', 'https://r.jpg', 'sp10')")
            conn.execute("INSERT INTO lib2_tracks (id, album_id, title)"
                         " VALUES (100, 10, 'Praying')")
            _own(conn, 100)
            # vibe tagged, has an id -> leg b
            conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title,"
                         " enrichment, spotify_id) VALUES (11, 2, 'Waves', ?, 'sp11')",
                         (_enrichment(["surf"], playcount=5),))
            conn.execute("INSERT INTO lib2_tracks (id, album_id, title)"
                         " VALUES (110, 11, 'Wave One')")
            _own(conn, 110)
            # vibe tagged, NO id -> excluded, the shelf click fetches by id
            conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title,"
                         " enrichment) VALUES (12, 2, 'No Id', ?)",
                         (_enrichment(["surf"]),))
            conn.execute("INSERT INTO lib2_tracks (id, album_id, title)"
                         " VALUES (120, 12, 'Nameless')")
            _own(conn, 120)
            conn.commit()
        _seed_plays(db, 'Kesha', 'Praying', 'Rainbow', month=7, count=3)
        albums = sv.vibe_library_albums(db, 'summer', [6, 7, 8], 'spotify')
        ids = {a['spotify_album_id'] for a in albums}
        assert ids == {'sp10', 'sp11'}
        # the played album maps plays into the popular tier
        played = next(a for a in albums if a['spotify_album_id'] == 'sp10')
        assert played['popularity'] == 63

    def test_itunes_source_uses_the_itunes_id(self, db, monkeypatch):
        monkeypatch.setattr(sv, '_normalize_art', lambda u: u)
        _seed_artist(db, 1, 'Surfy')
        with db._get_connection() as conn:
            conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title,"
                         " enrichment, external_ids)"
                         " VALUES (10, 1, 'Waves', ?, '{\"itunes\": \"it10\"}')",
                         (_enrichment(["surf"]),))
            conn.execute("INSERT INTO lib2_tracks (id, album_id, title)"
                         " VALUES (100, 10, 'Wave One')")
            _own(conn, 100)
            conn.commit()
        albums = sv.vibe_library_albums(db, 'summer', [6], 'itunes')
        assert [a['spotify_album_id'] for a in albums] == ['it10']


class TestLastfmTagChain:
    class FakeLastfm:
        def get_tag_top_artists(self, tag, limit=12):
            return [{'name': 'Known Artist'}, {'name': 'Fresh Face'},
                    {'name': 'Artless Fresh'}]

        def get_artist_top_tracks(self, artist, limit=2):
            return [{'name': f'{artist} Song'}]

    def test_known_and_artless_artists_are_dropped(self, db):
        _seed_artist(db, 1, 'Known Artist')
        with db._get_connection() as conn:
            conn.execute("INSERT INTO discovery_pool (source, artist_name,"
                         " album_cover_url) VALUES ('spotify', 'Fresh Face', 'https://f.jpg')")
            conn.commit()
        rows = sv.lastfm_tag_tracks(db, self.FakeLastfm(), 'summer')
        names = {r['artist_name'] for r in rows}
        # known stays out (the user has them), artless stays out (grey circles)
        assert names == {'Fresh Face'}
        assert all(r['album_cover_url'] == 'https://f.jpg' for r in rows)

    def test_no_client_no_rows(self, db):
        assert sv.lastfm_tag_tracks(db, None, 'summer') == []


class TestServiceWiring:
    """the routing guards: vibe seasons take the taste path, holidays keep
    keywords, and curation backfills thin tiers."""

    def _service(self, db, monkeypatch):
        from core.seasonal_discovery import SeasonalDiscoveryService
        svc = SeasonalDiscoveryService.__new__(SeasonalDiscoveryService)
        svc.spotify_client = None
        svc.database = db
        svc._ensure_database_schema()
        monkeypatch.setattr(svc, '_get_source', lambda: 'spotify')
        monkeypatch.setattr(svc, '_get_lastfm_client', lambda: None)
        return svc

    def test_summer_routes_to_the_vibe_path_never_keyword_search(self, db, monkeypatch):
        svc = self._service(db, monkeypatch)

        def boom(*a, **k):
            raise AssertionError('keyword flow must not run for a vibe season')
        monkeypatch.setattr(svc, '_search_seasonal_albums', boom)
        monkeypatch.setattr(svc, '_search_watchlist_seasonal_albums', boom)
        monkeypatch.setattr(svc, '_search_discovery_pool_seasonal', boom)

        _seed_plays(db, 'Kesha', 'Praying', 'Rainbow', month=7, count=3)
        svc.populate_seasonal_content('summer')

        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT track_name FROM seasonal_tracks WHERE season_key='summer'"
            ).fetchall()
        assert [r['track_name'] for r in rows] == ['Praying']

    def test_christmas_keeps_the_keyword_flow(self, db, monkeypatch):
        svc = self._service(db, monkeypatch)
        calls = []
        monkeypatch.setattr(svc, '_search_discovery_pool_seasonal',
                            lambda *a, **k: calls.append('pool') or [])
        monkeypatch.setattr(svc, '_search_watchlist_seasonal_albums',
                            lambda *a, **k: calls.append('watchlist') or [])
        monkeypatch.setattr(svc, '_search_seasonal_albums',
                            lambda *a, **k: calls.append('search') or [])
        monkeypatch.setattr(
            svc, '_populate_vibe_season',
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError('vibe path must not run for christmas')))
        svc.populate_seasonal_content('christmas')
        assert calls == ['pool', 'watchlist', 'search']

    def test_vibe_curation_skips_album_expansion_and_fills_the_playlist(self, db, monkeypatch):
        svc = self._service(db, monkeypatch)
        monkeypatch.setattr(
            svc, 'get_seasonal_albums',
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError('vibe curation must not expand albums')))
        # 60 popular-only tracks over 30 artists: without the backfill the
        # 60/30/10 tier mix strands this at 30 tracks
        for i in range(30):
            for j in range(2):
                svc._add_seasonal_track('summer', {
                    'spotify_track_id': f'id_{i}_{j}',
                    'track_name': f'Song {i}-{j}',
                    'artist_name': f'Artist {i}',
                    'album_name': 'Alb',
                    'popularity': 90,
                }, 'spotify')
        svc.curate_seasonal_playlist('summer')
        ids = svc.get_curated_seasonal_playlist('summer', source='spotify')
        assert len(ids) == 50
