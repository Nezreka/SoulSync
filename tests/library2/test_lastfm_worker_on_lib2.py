"""The Last.fm worker writes Library v2 (docs §32.3.1 stage 2, first worker).

Not a unit test of the pieces — those have their own. This drives the worker's
own methods with a stubbed Last.fm client and asserts on the lib2 rows, because
the thing that went wrong before was never the helper: it was that nothing called
it (iss32-E01).

The worker must hold no legacy SQL at all afterwards, which the legacy-usage
ratchet pins for the whole tree.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.provider_attempts import (
    attempt_state, ensure_provider_attempt_schema,
)
from core.library2.schema import ensure_library_v2_schema

from .conftest import own_every_track


class _Db:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class _Client:
    """Only the two helpers the worker asks of its Last.fm client."""

    @staticmethod
    def extract_tags(raw):
        if not raw:
            return []
        items = raw.get("tag", []) if isinstance(raw, dict) else raw
        return [t.get("name") for t in items if isinstance(t, dict) and t.get("name")]

    @staticmethod
    def get_best_image(images):
        return images[-1].get("#text") if images else None


@pytest.fixture
def worker(tmp_path, monkeypatch):
    path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    own_every_track(conn)
    ensure_provider_attempt_schema(conn.cursor())
    artist = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Massive Attack','Massive Attack')"
    ).lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
        "VALUES(?,'Mezzanine','album')", (artist,)).lastrowid
    conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Angel')", (album,))
    conn.commit()
    conn.close()

    import database.music_database as music_database
    monkeypatch.setattr(music_database, "MusicDatabase", lambda: _Db(path))

    from core.lastfm_worker import LastFMWorker

    instance = LastFMWorker(_Db(path))
    instance.client = _Client()
    return instance


def _row(worker, table, entity_id=1):
    conn = worker.db._get_connection()
    try:
        return conn.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
    finally:
        conn.close()


def _state(worker, entity_type, entity_id=1):
    conn = worker.db._get_connection()
    try:
        return attempt_state(conn, entity_type=entity_type, entity_id=entity_id)
    finally:
        conn.close()


class TestPicking:
    def test_it_starts_with_the_artist(self, worker):
        assert worker._get_next_item() == {
            "type": "artist", "id": 1, "name": "Massive Attack"}

    def test_it_moves_on_once_the_artist_is_recorded(self, worker):
        worker._mark_status("artist", 1, "matched")

        item = worker._get_next_item()

        assert item["type"] == "album"
        assert item["artist"] == "Massive Attack"


class TestArtistWrite:
    def test_stats_bio_tags_and_similar_land_in_enrichment(self, worker):
        worker._update_artist(1, {
            "stats": {"listeners": "90210", "playcount": "4242"},
            "bio": {"summary": 'A Bristol act. <a href="http://x">Read more</a>.'},
            "tags": {"tag": [{"name": "trip hop"}, {"name": "downtempo"}]},
            "similar": {"artist": [{"name": "Portishead", "match": "0.9"}]},
            "image": [{"#text": "http://img/small"}, {"#text": "http://img/large"}],
            "url": "https://last.fm/music/Massive+Attack",
        })

        payload = json.loads(_row(worker, "lib2_artists")["enrichment"])["lastfm"]
        assert payload["listeners"] == 90210
        assert payload["playcount"] == 4242
        assert payload["bio"] == "A Bristol act."
        assert payload["tags"] == ["trip hop", "downtempo"]
        assert payload["similar"] == [{"name": "Portishead", "match": "0.9"}]

    def test_the_url_becomes_the_provider_identity(self, worker):
        worker._update_artist(1, {"url": "https://last.fm/music/Massive+Attack"})

        ids = json.loads(_row(worker, "lib2_artists")["external_ids"])
        assert ids["lastfm"] == "https://last.fm/music/Massive+Attack"

    def test_artwork_and_style_are_backfilled_only_when_empty(self, worker):
        conn = worker.db._get_connection()
        conn.execute("UPDATE lib2_artists SET image_url='http://chosen' WHERE id=1")
        conn.commit()
        conn.close()

        worker._update_artist(1, {
            "image": [{"#text": "http://img/large"}],
            "tags": {"tag": [{"name": "trip hop"}]},
        })

        row = _row(worker, "lib2_artists")
        assert row["image_url"] == "http://chosen", "Last.fm art is a fallback"
        assert row["style"] == "trip hop", "an empty column is filled"

    def test_the_attempt_is_recorded_as_matched(self, worker):
        worker._update_artist(1, {"url": "https://last.fm/x"})

        assert _state(worker, "artist")["lastfm"]["status"] == "matched"


class TestAlbumAndTrackWrites:
    def test_the_album_wiki_and_genres_arrive(self, worker):
        worker._update_album(1, {
            "listeners": "500", "playcount": "900",
            "tags": {"tag": [{"name": "trip hop"}]},
            "wiki": {"summary": "A landmark record."},
            "url": "https://last.fm/music/MA/Mezzanine",
        })

        row = _row(worker, "lib2_albums")
        payload = json.loads(row["enrichment"])["lastfm"]
        assert payload["wiki"] == "A landmark record."
        assert payload["listeners"] == 500
        assert json.loads(row["genres"]) == ["trip hop"]
        assert _state(worker, "album")["lastfm"]["status"] == "matched"

    def test_the_track_stats_arrive(self, worker):
        worker._update_track(1, {
            "listeners": "7", "playcount": "8",
            "toptags": {"tag": [{"name": "downtempo"}]},
            "url": "https://last.fm/music/MA/_/Angel",
        })

        payload = json.loads(_row(worker, "lib2_tracks")["enrichment"])["lastfm"]
        assert payload == {"listeners": 7, "playcount": 8,
                           "tags": ["downtempo"],
                           "url": "https://last.fm/music/MA/_/Angel"}


class TestOutcomes:
    def test_a_miss_is_recorded_and_retried_later(self, worker):
        worker._mark_status("artist", 1, "not_found")

        assert _state(worker, "artist")["lastfm"]["status"] == "not_found"
        assert worker._get_next_item()["type"] == "album", "not offered again today"

    def test_an_existing_url_is_found_again(self, worker):
        worker._update_artist(1, {"url": "https://last.fm/music/Massive+Attack"})

        assert worker._get_existing_id("artist", 1) == (
            "https://last.fm/music/Massive+Attack")

    def test_progress_counts_every_attempt(self, worker):
        worker._mark_status("artist", 1, "not_found")

        breakdown = worker._get_progress_breakdown()

        assert breakdown["artists"] == {"matched": 1, "total": 1, "percent": 100}
        assert worker._count_pending_items() == 2


def test_the_worker_holds_no_legacy_sql_at_all():
    """The point of the exercise: this file is done with the legacy tables."""
    import pathlib

    from tests.library2.legacy_usage import count_legacy_usage

    source = pathlib.Path("core/lastfm_worker.py").read_text()

    usage = count_legacy_usage(source)
    assert (usage.reads, usage.writes) == (0, 0)
