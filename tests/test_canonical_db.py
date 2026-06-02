"""DB persistence for canonical album version (#765 Stage 2)."""

from __future__ import annotations

from database.music_database import MusicDatabase


def _album(db, album_id="alb_evolve"):
    # id columns are TEXT (GUID) post-migration, so insert explicit ids and a
    # valid FK rather than relying on integer rowids.
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name) VALUES ('art_id', 'Imagine Dragons')")
    cur.execute(
        "INSERT INTO albums (id, title, artist_id) VALUES (?, 'Evolve', 'art_id')",
        (album_id,),
    )
    conn.commit()
    conn.close()
    return album_id


def test_set_then_get_roundtrip(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    album_id = _album(db)

    assert db.get_album_canonical(album_id) is None  # unresolved by default

    assert db.set_album_canonical(album_id, "spotify", "sp_evolve_123", 0.97) is True
    got = db.get_album_canonical(album_id)
    assert got["source"] == "spotify"
    assert got["album_id"] == "sp_evolve_123"
    assert abs(got["score"] - 0.97) < 1e-6
    assert got["resolved_at"]  # timestamp populated


def test_get_unresolved_returns_none(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    album_id = _album(db)
    assert db.get_album_canonical(album_id) is None


def test_set_overwrites_previous(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    album_id = _album(db)
    db.set_album_canonical(album_id, "spotify", "old", 0.6)
    db.set_album_canonical(album_id, "musicbrainz", "new", 0.95)
    got = db.get_album_canonical(album_id)
    assert got["source"] == "musicbrainz" and got["album_id"] == "new"


def test_set_on_missing_album_returns_false(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    assert db.set_album_canonical(999999, "spotify", "x", 0.9) is False
