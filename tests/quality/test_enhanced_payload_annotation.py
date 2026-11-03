"""The enhanced artist view's upgrade badges: one read for every track's file,
one per distinct profile (it ran three queries per track on every visit)."""

from contextlib import closing

from core.quality import upgrades
from database.music_database import MusicDatabase
from tests import lib2_seed


def test_badges_land_on_the_tracks_below_their_profile(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with closing(db._get_connection()) as conn:
        low = lib2_seed.track(conn, "Artist", "Album", "Low", path="/m/low.mp3")
        good = lib2_seed.track(conn, "Artist", "Album", "Good", path="/m/good.flac")
        fileless = lib2_seed.track(conn, "Artist", "Album", "Wanted", owned=False)
        conn.execute("UPDATE lib2_track_files SET format='mp3', bitrate=128 WHERE path='/m/low.mp3'")
        conn.execute("UPDATE lib2_track_files SET format='flac' WHERE path='/m/good.flac'")
        conn.commit()
    monkeypatch.setattr("core.library2.quality_eval.quality_issue",
                        lambda file, profile: "below_cutoff" if file["format"] == "mp3" else None)
    payload = {"albums": [{"tracks": [{"id": low}, {"id": good}, {"id": fileless}]}]}

    upgrades.annotate_enhanced_payload(db, payload)

    album = payload["albums"][0]
    assert album["upgradable_count"] == 1
    flagged = [t["id"] for t in album["tracks"] if "quality_upgrade" in t]
    assert flagged == [low]
    assert album["tracks"][0]["quality_upgrade"]["lib2_track_id"] == low


def test_a_database_error_leaves_the_view_without_badges_not_a_500(tmp_path, monkeypatch):
    """the badges are decoration: 3.6.1 never failed the enhanced artist view
    over them. a locked or broken read leaves the view whole, unbadged, and
    no stale badge from the payload survives."""
    import sqlite3
    db = MusicDatabase(str(tmp_path / "m.db"))
    with closing(db._get_connection()) as conn:
        low = lib2_seed.track(conn, "Artist", "Album", "Low", path="/m/low.mp3")
        conn.commit()

    def locked(conn, ids):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr("core.library2.track_files.primary_file_rows", locked)
    payload = {"albums": [{"title": "Album", "tracks": [
        {"id": low, "title": "Low", "quality_upgrade": {"stale": True}}]}]}

    assert upgrades.annotate_enhanced_payload(db, payload) is payload

    album = payload["albums"][0]
    assert album["title"] == "Album" and album["tracks"][0]["title"] == "Low"
    assert album["upgradable_count"] == 0
    assert "quality_upgrade" not in album["tracks"][0]


def test_a_failure_halfway_never_badges_half_an_artist(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with closing(db._get_connection()) as conn:
        first = lib2_seed.track(conn, "Artist", "One", "A", path="/m/a.mp3")
        second = lib2_seed.track(conn, "Artist", "Two", "B", path="/m/b.mp3")
        conn.execute("UPDATE lib2_track_files SET format='mp3', bitrate=128")
        conn.commit()
    calls = []

    def issue(file, profile):
        calls.append(file)
        if len(calls) > 1:
            raise RuntimeError("disk I/O error")
        return "below_cutoff"
    monkeypatch.setattr("core.library2.quality_eval.quality_issue", issue)
    payload = {"albums": [{"tracks": [{"id": first}]}, {"tracks": [{"id": second}]}]}

    upgrades.annotate_enhanced_payload(db, payload)

    assert [a["upgradable_count"] for a in payload["albums"]] == [0, 0]
    assert not any("quality_upgrade" in t for a in payload["albums"] for t in a["tracks"])
