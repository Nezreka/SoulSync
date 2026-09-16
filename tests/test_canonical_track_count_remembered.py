"""the pinned release's track count is fetched once and remembered.

the artist page's completion check resolved every canonical pin against the
metadata source live, every time. with musicbrainz answering 503 that was
one album waiting through 2 s + 4 s of retries on the request thread before
falling back to the local count (weird al, sept 15). a release's tracklist
does not change, so the count now lives on the albums row after the first
fetch: same number, no network. a re-pin clears it; a failed fetch leaves
it unremembered so the next check tries again exactly as before.
"""

import sqlite3

import pytest

import core.metadata.completion as completion
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    c = sqlite3.connect(str(d.database_path))
    c.execute("INSERT INTO artists (id, name, server_source) VALUES (1, 'Weird Al', 'plex')")
    c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (10, 1, 'Mandatory Fun', 'plex')")
    for i in range(1, 13):
        c.execute("INSERT INTO tracks (id, album_id, artist_id, title, server_source, file_path, track_number) VALUES (?, 10, 1, ?, 'plex', ?, ?)", (100 + i, f"Track {i}", f"/m/{i}.flac", i))
    c.commit()
    c.close()
    return d


class _Album:
    id = 10


def _resolve(db, cache=None):
    return completion._resolve_canonical_album_completion(db, _Album(), canonical_cache=None, completeness_cache=None, pin_tracks_cache=cache)


def test_column_exists_and_pin_read_carries_it(db):
    assert db.set_album_canonical(10, "musicbrainz", "rel-1", 0.9, locked=True)
    pin = db.get_album_canonical(10)
    assert pin["track_count"] is None
    assert db.set_album_canonical_track_count(10, "musicbrainz", "rel-1", 12)
    assert db.get_album_canonical(10)["track_count"] == 12


def test_first_check_fetches_live_and_remembers(db, monkeypatch):
    db.set_album_canonical(10, "musicbrainz", "rel-1", 0.9, locked=True)
    calls = []
    monkeypatch.setattr(completion, "get_album_tracks_for_source",
                        lambda src, aid: calls.append((src, aid)) or {"items": [{}] * 12})
    result = _resolve(db)
    assert calls == [("musicbrainz", "rel-1")]
    assert result["canonical_track_count"] == 12
    assert result["expected_tracks"] == 12
    assert db.get_album_canonical(10)["track_count"] == 12


def test_second_check_makes_no_network_call(db, monkeypatch):
    db.set_album_canonical(10, "musicbrainz", "rel-1", 0.9, locked=True)
    db.set_album_canonical_track_count(10, "musicbrainz", "rel-1", 12)

    def boom(src, aid):
        raise AssertionError("live fetch for a remembered pin")

    monkeypatch.setattr(completion, "get_album_tracks_for_source", boom)
    result = _resolve(db)
    assert result["canonical_track_count"] == 12
    assert result["is_complete"] is True


def test_remembered_count_is_the_same_answer_as_the_live_one(db, monkeypatch):
    db.set_album_canonical(10, "musicbrainz", "rel-1", 0.9, locked=True)
    monkeypatch.setattr(completion, "get_album_tracks_for_source", lambda src, aid: {"items": [{}] * 14})
    live = _resolve(db)
    monkeypatch.setattr(completion, "get_album_tracks_for_source", lambda src, aid: (_ for _ in ()).throw(AssertionError("no")))
    remembered = _resolve(db)
    assert live == remembered
    assert remembered["is_complete"] is False   # 12 of 14


def test_a_repin_forgets_the_count_and_fetches_the_new_release_once(db, monkeypatch):
    db.set_album_canonical(10, "musicbrainz", "rel-1", 0.9, locked=True)
    db.set_album_canonical_track_count(10, "musicbrainz", "rel-1", 12)
    assert db.set_album_canonical(10, "musicbrainz", "rel-2", 0.95, locked=True)
    assert db.get_album_canonical(10)["track_count"] is None
    calls = []
    monkeypatch.setattr(completion, "get_album_tracks_for_source",
                        lambda src, aid: calls.append(aid) or {"items": [{}] * 15})
    assert _resolve(db)["canonical_track_count"] == 15
    assert calls == ["rel-2"]
    assert db.get_album_canonical(10)["track_count"] == 15


def test_a_failed_fetch_is_not_remembered_and_the_fallback_is_unchanged(db, monkeypatch):
    db.set_album_canonical(10, "musicbrainz", "rel-1", 0.9, locked=True)
    monkeypatch.setattr(completion, "get_album_tracks_for_source", lambda src, aid: None)
    result = _resolve(db)
    # the pre-existing behaviour: local stored count only
    assert result["canonical_track_count"] == 0
    assert result["owned_tracks"] == 12
    assert db.get_album_canonical(10)["track_count"] is None
    # and the next check tries the network again
    calls = []
    monkeypatch.setattr(completion, "get_album_tracks_for_source", lambda src, aid: calls.append(1) or {"items": [{}] * 12})
    _resolve(db)
    assert calls == [1]


def test_the_count_cannot_land_on_a_pin_that_changed_underneath(db):
    db.set_album_canonical(10, "musicbrainz", "rel-1", 0.9, locked=True)
    # the fetch was for rel-1, the pin moved to rel-2 in between
    db.set_album_canonical(10, "musicbrainz", "rel-2", 0.95, locked=True)
    assert db.set_album_canonical_track_count(10, "musicbrainz", "rel-1", 12) is False
    assert db.get_album_canonical(10)["track_count"] is None


def test_bad_counts_are_refused(db):
    db.set_album_canonical(10, "musicbrainz", "rel-1", 0.9, locked=True)
    assert db.set_album_canonical_track_count(10, "musicbrainz", "rel-1", 0) is False
    assert db.set_album_canonical_track_count(10, "musicbrainz", "rel-1", "x") is False
    assert db.get_album_canonical(10)["track_count"] is None


def test_per_run_cache_still_short_circuits(db, monkeypatch):
    db.set_album_canonical(10, "musicbrainz", "rel-1", 0.9, locked=True)
    calls = []
    monkeypatch.setattr(completion, "get_album_tracks_for_source", lambda src, aid: calls.append(1) or {"items": [{}] * 12})
    cache = {}
    _resolve(db, cache)
    _resolve(db, cache)
    assert calls == [1]
    assert cache[("musicbrainz", "rel-1")] == 12
