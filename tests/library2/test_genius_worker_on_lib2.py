"""The Genius worker writes Library v2 (docs §32.3.1 stage 2, second worker).

Genius is the case that showed the shared write helper was incomplete: lyrics go
into a real column and must be *replaced* on a fresh fetch, not merely
backfilled. It is also artist+track only — Genius has no album endpoint — so the
queue must skip albums rather than attempt and mark them.
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
    @staticmethod
    def extract_description(raw):
        return raw if isinstance(raw, str) else None


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

    from core.genius_worker import GeniusWorker

    instance = GeniusWorker.__new__(GeniusWorker)
    instance.db = _Db(path)
    instance.client = _Client()
    instance.retry_days = 30
    return instance


def _row(worker, table, entity_id=1):
    conn = worker.db._get_connection()
    try:
        return conn.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
    finally:
        conn.close()


def test_albums_are_never_offered(worker):
    """Genius has no album endpoint. Offering albums would mark every one of
    them 'not_found' and call it progress."""
    worker._mark_status("artist", 1, "matched")

    item = worker._get_next_item()

    assert item["type"] == "track"


def test_artist_description_and_alt_names_land_in_enrichment(worker):
    worker._update_artist(
        1,
        {"id": 77, "url": "https://genius.com/artists/MA"},
        {"id": 77, "description": "A Bristol collective.",
         "alternate_names": ["MA"], "image_url": "http://img/genius",
         "url": "https://genius.com/artists/MA"},
    )

    row = _row(worker, "lib2_artists")
    payload = json.loads(row["enrichment"])["genius"]
    assert payload["description"] == "A Bristol collective."
    assert payload["alt_names"] == ["MA"]
    assert json.loads(row["external_ids"])["genius"] == "77"
    assert row["image_url"] == "http://img/genius", "empty column is backfilled"


def test_lyrics_replace_what_is_already_stored(worker):
    conn = worker.db._get_connection()
    conn.execute("UPDATE lib2_tracks SET genius_lyrics='old words' WHERE id=1")
    conn.commit()
    conn.close()

    worker._update_track(1, {"id": 5}, {"id": 5, "url": "https://genius.com/x"},
                         "new words")

    assert _row(worker, "lib2_tracks")["genius_lyrics"] == "new words"


def test_a_failed_lyrics_fetch_does_not_erase_them(worker):
    worker._update_track(1, {"id": 5}, {"id": 5}, "the words")
    worker._update_track(1, {"id": 5}, {"id": 5}, None)

    assert _row(worker, "lib2_tracks")["genius_lyrics"] == "the words"


def test_the_attempt_is_recorded(worker):
    worker._update_track(1, {"id": 5}, {"id": 5}, "words")

    conn = worker.db._get_connection()
    try:
        state = attempt_state(conn, entity_type="track", entity_id=1)
    finally:
        conn.close()
    assert state["genius"]["status"] == "matched"


def test_progress_covers_artists_and_tracks_only(worker):
    breakdown = worker._get_progress_breakdown()

    assert set(breakdown) == {"artists", "tracks"}
    assert worker._count_pending_items() == 2


def test_the_worker_holds_no_legacy_sql_at_all():
    import pathlib

    from tests.library2.legacy_usage import count_legacy_usage

    usage = count_legacy_usage(pathlib.Path("core/genius_worker.py").read_text())

    assert (usage.reads, usage.writes) == (0, 0)
