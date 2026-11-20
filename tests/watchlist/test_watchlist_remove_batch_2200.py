"""#2200: a bulk watchlist removal is one pass, with the same end state.

Removing 57 artists took 70 s (46 s of cpu) and the page reported a timeout
although every artist was gone: each artist re-read and re-parsed the whole
lib2 artist table, recomputed its tracks, mirrored them and drained the
outbox on its own. The batch now does that once for the lot.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

pytest.importorskip("flask")

import web_server  # noqa: E402
from database.music_database import MusicDatabase  # noqa: E402

ARTISTS = 6


class _Holder:
    db = None


@pytest.fixture()
def client(monkeypatch):
    holder = _Holder()
    import database.music_database as music_database
    from api import artist_watchlist

    def current(*_a, **_k):
        return holder.db

    monkeypatch.setattr(music_database, "get_database", current)
    monkeypatch.setattr(web_server, "get_database", current)
    monkeypatch.setattr(artist_watchlist, "get_database", current)
    monkeypatch.setattr(artist_watchlist, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(artist_watchlist, "_get_metadata_fallback_source", lambda: "deezer")

    def _no_network(*_a, **_k):
        raise OSError("no network in tests")

    monkeypatch.setattr(artist_watchlist.requests, "get", _no_network)
    monkeypatch.setattr(web_server, "spotify_client", None)
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as test_client:
        yield test_client, holder


def _seed(db):
    """six watched artists, each two albums of four tracks, half owned. one
    album is monitored on purpose, so it stays wanted after its artist
    goes; one artist has a namesake that must never be touched."""
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    from core.library2.monitor_sync import reconcile_track_wishlist, sync_watchlist_addition
    from core.library2.wanted import recompute_wanted

    with closing(db._get_connection()) as conn:
        for a in range(1, ARTISTS + 1):
            artist_id = conn.execute(
                "INSERT INTO lib2_artists(name, spotify_id, external_ids) VALUES(?,?,?)",
                (f"Artist {a}", f"sp-ar-{a}", '{"spotify":"sp-ar-%d"}' % a)).lastrowid
            for b in range(2):
                album_id = conn.execute(
                    "INSERT INTO lib2_albums(primary_artist_id, title, spotify_id) VALUES(?,?,?)",
                    (artist_id, f"Album {a}-{b}", f"sp-al-{a}-{b}")).lastrowid
                conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                             (album_id, artist_id))
                if a == 2 and b == 0:
                    record_rule(conn, "album", album_id, True, PROVENANCE_USER)
                for k in range(4):
                    track_id = conn.execute(
                        "INSERT INTO lib2_tracks(album_id, title, spotify_id, track_number) "
                        "VALUES(?,?,?,?)",
                        (album_id, f"Track {a}-{b}-{k}", f"sp-tr-{a}-{b}-{k}", k + 1)).lastrowid
                    conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?,?)",
                                 (track_id, artist_id))
                    if k % 2 == 0:
                        conn.execute(
                            "INSERT INTO lib2_track_files(track_id, path, format) VALUES(?,?, 'flac')",
                            (track_id, f"/m/{track_id}.flac"))
        # the namesake of Artist 3, under another id: it stays monitored
        conn.execute(
            "INSERT INTO lib2_artists(name, monitored, external_ids) VALUES('Artist 3', 1, ?)",
            ('{"musicbrainz":"mb-other-3"}',))
        recompute_wanted(conn, profile_id=1)
        conn.commit()
    for a in range(1, ARTISTS + 1):
        assert db.add_artist_to_watchlist(f"sp-ar-{a}", f"Artist {a}", profile_id=1, source="spotify")
        sync_watchlist_addition(db, name=f"Artist {a}", provider_id=f"sp-ar-{a}",
                                source="spotify", profile_id=1)
    reconcile_track_wishlist(db)


def _snapshot(db):
    with closing(sqlite3.connect(str(db.database_path))) as conn:
        return {
            "monitored": conn.execute(
                "SELECT name, external_ids, monitored FROM lib2_artists ORDER BY id").fetchall(),
            "rules": conn.execute(
                "SELECT entity_type, entity_id, profile_id, monitored, provenance "
                "FROM lib2_monitor_rules ORDER BY 1, 2, 3").fetchall(),
            "wanted": conn.execute(
                "SELECT profile_id, track_id, wanted, reason, effective_profile_id "
                "FROM lib2_wanted_tracks ORDER BY 1, 2").fetchall(),
            "wishlist": conn.execute(
                "SELECT profile_id, spotify_track_id FROM wishlist_tracks ORDER BY 1, 2").fetchall(),
            "watchlist": conn.execute(
                "SELECT profile_id, artist_name FROM watchlist_artists ORDER BY 1, 2").fetchall(),
            "pending": conn.execute(
                "SELECT COUNT(*) FROM lib2_mirror_outbox WHERE status='pending'").fetchone()[0],
        }


REMOVED = [f"sp-ar-{a}" for a in range(1, ARTISTS)]  # all but the last


def test_the_batch_ends_where_one_by_one_ends(client, tmp_path):
    test_client, holder = client

    holder.db = one_by_one = MusicDatabase(str(tmp_path / "single.db"))
    _seed(one_by_one)
    start = _snapshot(one_by_one)
    for artist_id in REMOVED:
        response = test_client.post("/api/watchlist/remove", json={"artist_id": artist_id})
        assert response.status_code == 200, response.get_json()

    holder.db = batch = MusicDatabase(str(tmp_path / "batch.db"))
    _seed(batch)
    assert _snapshot(batch) == start
    response = test_client.post("/api/watchlist/remove-batch", json={"artist_ids": REMOVED})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["removed"] == len(REMOVED)

    end = _snapshot(batch)
    assert end == _snapshot(one_by_one)
    # and the removal did something: the seed is not a no-op case
    assert end["watchlist"] == [(1, f"Artist {ARTISTS}")]
    assert len(end["wishlist"]) < len(start["wishlist"])
    assert ("Artist 3", '{"musicbrainz":"mb-other-3"}', 1) in end["monitored"]
    # the album monitored on purpose is still wished for
    assert any(row[1].startswith("sp-tr-2-0-") for row in end["wishlist"])


def test_the_batch_reads_recomputes_and_drains_once(client, tmp_path, monkeypatch):
    test_client, holder = client
    holder.db = db = MusicDatabase(str(tmp_path / "m.db"))
    _seed(db)

    import core.library2.mirror_outbox as mirror_outbox
    import core.library2.monitor_sync as monitor_sync
    import core.library2.wanted as wanted

    calls = {"recompute": 0, "drain": 0, "index": 0}
    real_recompute, real_drain = wanted.recompute_wanted, mirror_outbox.drain
    real_index = monitor_sync._Lib2ArtistIndex.__init__

    def recompute(*a, **k):
        calls["recompute"] += 1
        return real_recompute(*a, **k)

    def drain(*a, **k):
        calls["drain"] += 1
        return real_drain(*a, **k)

    def index(self, *a, **k):
        calls["index"] += 1
        return real_index(self, *a, **k)

    monkeypatch.setattr(wanted, "recompute_wanted", recompute)
    monkeypatch.setattr(mirror_outbox, "drain", drain)
    monkeypatch.setattr(monitor_sync._Lib2ArtistIndex, "__init__", index)

    response = test_client.post("/api/watchlist/remove-batch", json={"artist_ids": REMOVED})

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["removed"] == len(REMOVED)
    assert calls == {"recompute": 1, "drain": 1, "index": 1}


def test_the_route_hands_every_removed_row_to_one_sync(client, tmp_path, monkeypatch):
    test_client, holder = client
    holder.db = db = MusicDatabase(str(tmp_path / "m.db"))
    for a in range(1, 4):
        assert db.add_artist_to_watchlist(f"sp-ar-{a}", f"Artist {a}", profile_id=1, source="spotify")

    import core.library2.monitor_sync as monitor_sync
    seen = []
    monkeypatch.setattr(monitor_sync, "sync_watchlist_removals",
                        lambda _db, descriptors, profile_id=1: seen.append(
                            (sorted(d["name"] for d in descriptors), profile_id)))
    monkeypatch.setattr(monitor_sync, "sync_watchlist_removal",
                        lambda *a, **k: pytest.fail("one sync per artist"))

    response = test_client.post("/api/watchlist/remove-batch",
                                json={"artist_ids": ["sp-ar-1", "sp-ar-3", "not-watched"]})

    assert response.get_json()["removed"] == 2
    assert seen == [(["Artist 1", "Artist 3"], 1)]
