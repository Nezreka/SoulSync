"""Top library genres come from Library v2 (docs §50.4.4.17).

``get_top_genres_from_library`` feeds the Daily Mix generator: whatever it
returns becomes the mix categories, so an empty answer is a discover page with
no mixes on it. It had no test at all, and the port changed two things worth
pinning.

**Genres live on the release.** Legacy carried a ``genres`` column on ``tracks``;
lib2 keeps the list on ``lib2_albums`` and a track inherits its album's, which is
where the importer and every provider worker write it.

**The fallback trigger became honest.** It used to be "the schema has no genres
column" — a ``PRAGMA`` probe for a column lib2 always has, so the branch was
unreachable by construction. The situation it covered is real, though: a library
nothing has enriched yet still needs categories. It now triggers on there being
no genres, which is what it always meant.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

import pytest

from core.personalized_playlists import PersonalizedPlaylistsService


class _Db:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        from core.library2.schema import ensure_library_v2_schema
        ensure_library_v2_schema(self._conn)
        self._conn.commit()

    @contextmanager
    def _get_connection(self):
        yield self._conn

    def album(self, artist, title, genres=None, origin='library', tracks=1):
        from core.library2.importer import normalize_name

        artist_id = self._conn.execute(
            "INSERT INTO lib2_artists(name, name_key, sort_name) VALUES(?,?,?)",
            (artist, normalize_name(artist), artist)).lastrowid
        album_id = self._conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, genres, origin) "
            "VALUES(?,?,?,?)",
            (artist_id, title, json.dumps(genres) if genres is not None else '[]',
             origin)).lastrowid
        for n in range(tracks):
            track_id = self._conn.execute(
                "INSERT INTO lib2_tracks(album_id, title) VALUES(?,?)",
                (album_id, f"{title} {n}")).lastrowid
            if origin == 'library':
                self._conn.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)",
                                   (track_id, f'/music/{track_id}.flac'))
        self._conn.commit()


@pytest.fixture
def service():
    db = _Db()
    return PersonalizedPlaylistsService(db), db


class TestGenres:
    def test_genres_are_counted_off_the_releases(self, service):
        svc, db = service
        db.album('A', 'One', ['house', 'techno'])
        db.album('B', 'Two', ['house'])

        assert svc.get_top_genres_from_library(limit=5) == [('house', 2), ('techno', 1)]

    def test_a_comma_separated_value_is_still_understood(self, service):
        """Legacy stored the list either way and the importer carries it over."""
        svc, db = service
        db._conn.execute(
            "INSERT INTO lib2_artists(name, name_key, sort_name) VALUES('C','c','C')")
        db._conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, genres, origin) "
            "VALUES(1, 'Three', 'jazz, soul', 'library')")
        track_id = db._conn.execute(
            "INSERT INTO lib2_tracks(album_id,title) VALUES(1,'Three 1')").lastrowid
        db._conn.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)",
                         (track_id, '/music/three.flac'))
        db._conn.commit()

        assert dict(svc.get_top_genres_from_library(limit=5)) == {'jazz': 1, 'soul': 1}

    def test_a_provider_only_release_does_not_vote(self, service):
        """A discography row describes a release we do not have; its genre is
        not evidence about what the user listens to."""
        svc, db = service
        db.album('A', 'Owned', ['house'])
        db.album('B', 'Merely Listed', ['polka'], origin='discography')

        assert svc.get_top_genres_from_library(limit=5) == [('house', 1)]

    def test_the_limit_is_honored(self, service):
        svc, db = service
        db.album('A', 'One', ['house', 'techno', 'jazz'])

        assert len(svc.get_top_genres_from_library(limit=2)) == 2


class TestTheFallback:
    def test_an_unenriched_library_falls_back_to_its_artists(self, service):
        """No genres anywhere: the mixes have to be built from something, and
        the top artists are the categories that survived the old dead branch."""
        svc, db = service
        db.album('Prolific', 'One', tracks=3)
        db.album('Occasional', 'Two', tracks=1)

        assert svc.get_top_genres_from_library(limit=5) == [
            ('Prolific', 3), ('Occasional', 1)]

    def test_an_empty_library_returns_nothing_rather_than_failing(self, service):
        svc, _db = service
        assert svc.get_top_genres_from_library(limit=5) == []
