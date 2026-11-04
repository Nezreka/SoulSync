"""The public API's retag queue answers from Library v2's pending Re-tag findings.

``/api/v1/retag/*`` listed the legacy ``retag_groups`` / ``retag_tracks``
tables, which nothing has filled since Library v2, so every call came back
empty. A group is now an album with pending Re-tag findings and a track one of
those findings, in the shape Support/API.md documents.
"""

from __future__ import annotations

import json

import pytest

flask = pytest.importorskip("flask")

from database.music_database import MusicDatabase  # noqa: E402
from tests import lib2_seed  # noqa: E402


@pytest.fixture
def api(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "music.db"))
    import api.retag as v1_retag
    monkeypatch.setattr(v1_retag, "require_api_key", lambda view: view)
    monkeypatch.setattr(v1_retag, "get_database", lambda: db)
    bp = flask.Blueprint("v1_retag_test", __name__)
    v1_retag.register_routes(bp)
    app = flask.Flask("v1")
    app.register_blueprint(bp, url_prefix="/api/v1")
    return db, app.test_client()


def _data(response):
    body = response.get_json()
    assert body["success"], body
    return body["data"]


def _finding(conn, track_id, path, *, job="library_retag", status="pending",
             created="2026-10-30 10:00:00"):
    return int(conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, status, entity_type, entity_id,"
        " file_path, title, details_json, created_at) VALUES (?, ?, ?, 'track', ?, ?, 'x', ?, ?)",
        (job, job, status, f"lib2:{track_id}", path, json.dumps({"track_id": track_id}), created),
    ).lastrowid)


@pytest.fixture
def seeded(api):
    db, client = api
    with db._get_connection() as conn:
        album = {"release_date": "1997-06-16", "spotify_id": "sp-okc",
                 "external_ids": json.dumps({"itunes": "it-okc"}), "track_count": 12}
        airbag = lib2_seed.track(conn, "Radiohead", "OK Computer", "Airbag", track_number=1,
                                 disc_number=1, spotify_id="sp-airbag", album_cols=album,
                                 path="/music/Radiohead/OK Computer/01 - Airbag.flac")
        android = lib2_seed.track(conn, "Radiohead", "OK Computer", "Paranoid Android",
                                  track_number=2, disc_number=1,
                                  path="/music/Radiohead/OK Computer/02 - Paranoid Android.mp3")
        nude = lib2_seed.track(conn, "Radiohead", "In Rainbows", "Nude", track_number=3,
                               path="/music/Radiohead/In Rainbows/03 - Nude.flac")
        angel = lib2_seed.track(conn, "Massive Attack", "Mezzanine", "Angel", track_number=1,
                                path="/music/Massive Attack/Mezzanine/01 - Angel.flac")
        ids = {
            "android": _finding(conn, android, "/music/Radiohead/OK Computer/02 - Paranoid Android.mp3",
                                created="2026-10-30 11:00:00"),
            "airbag": _finding(conn, airbag, "/music/Radiohead/OK Computer/01 - Airbag.flac"),
            "nude": _finding(conn, nude, "/music/Radiohead/In Rainbows/03 - Nude.flac",
                             created="2026-10-31 09:00:00"),
            "angel": _finding(conn, angel, "/music/Massive Attack/Mezzanine/01 - Angel.flac"),
        }
        # neither of these waits for a tag correction
        _finding(conn, angel, "/x.flac", status="dismissed")
        _finding(conn, angel, "/y.flac", job="missing_cover_art")
        conn.commit()
        okc = conn.execute("SELECT id FROM lib2_albums WHERE title='OK Computer'").fetchone()[0]
        rainbows = conn.execute("SELECT id FROM lib2_albums WHERE title='In Rainbows'").fetchone()[0]
    return db, client, ids, okc, rainbows


def test_groups_are_albums_with_pending_retag_findings(seeded):
    _db, client, _ids, okc, rainbows = seeded

    groups = _data(client.get("/api/v1/retag/groups"))["groups"]

    # by artist name, then newest first, like the old queue
    assert [(g["artist_name"], g["album_name"]) for g in groups] == [
        ("Massive Attack", "Mezzanine"), ("Radiohead", "In Rainbows"), ("Radiohead", "OK Computer")]
    ok = next(g for g in groups if g["id"] == okc)
    assert ok == {
        "id": okc, "group_type": "album", "artist_name": "Radiohead", "album_name": "OK Computer",
        "image_url": None, "spotify_album_id": "sp-okc", "itunes_album_id": "it-okc",
        "total_tracks": 12, "release_date": "1997-06-16", "created_at": "2026-10-30 10:00:00",
        "track_count": 2,
    }
    assert next(g for g in groups if g["id"] == rainbows)["track_count"] == 1


def test_a_group_lists_its_tracks_by_disc_and_number(seeded):
    _db, client, ids, okc, _rainbows = seeded

    data = _data(client.get(f"/api/v1/retag/groups/{okc}"))

    assert data["group"]["id"] == okc
    tracks = data["tracks"]
    assert [t["id"] for t in tracks] == [ids["airbag"], ids["android"]]
    assert tracks[0] == {
        "id": ids["airbag"], "group_id": okc, "track_number": 1, "disc_number": 1,
        "title": "Airbag", "file_path": "/music/Radiohead/OK Computer/01 - Airbag.flac",
        "file_format": "flac", "spotify_track_id": "sp-airbag", "itunes_track_id": None,
        "created_at": "2026-10-30 10:00:00",
    }
    assert tracks[1]["file_format"] == "mp3"


def test_an_album_without_pending_findings_is_not_found(seeded):
    _db, client, _ids, _okc, _rainbows = seeded
    assert client.get("/api/v1/retag/groups/999999").status_code == 404
    assert client.delete("/api/v1/retag/groups/999999").status_code == 404


def test_deleting_a_group_dismisses_its_findings(seeded):
    db, client, ids, okc, _rainbows = seeded

    assert _data(client.delete(f"/api/v1/retag/groups/{okc}"))["message"] == f"Retag group {okc} deleted."

    with db._get_connection() as conn:
        status = dict(conn.execute("SELECT id, status FROM repair_findings").fetchall())
    assert status[ids["airbag"]] == status[ids["android"]] == "dismissed"
    assert status[ids["nude"]] == status[ids["angel"]] == "pending"
    assert client.get(f"/api/v1/retag/groups/{okc}").status_code == 404


def test_clearing_dismisses_every_pending_retag_finding_and_counts_groups(seeded):
    db, client, _ids, _okc, _rainbows = seeded

    assert _data(client.delete("/api/v1/retag/groups"))["message"] == "Cleared 3 retag groups."

    assert _data(client.get("/api/v1/retag/groups"))["groups"] == []
    with db._get_connection() as conn:
        # another job's finding is not the retag queue's to clear
        assert conn.execute("SELECT status FROM repair_findings WHERE job_id='missing_cover_art'"
                            ).fetchone()[0] == "pending"


def test_stats_count_groups_tracks_and_artists(seeded):
    _db, client, _ids, _okc, _rainbows = seeded
    assert _data(client.get("/api/v1/retag/stats")) == {"groups": 3, "tracks": 4, "artists": 2}
