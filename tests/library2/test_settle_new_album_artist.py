"""A new download's album artist that reads as a list goes to Split Artists
after the import (#2150 follow-up). Split Artists is the one job that splits
an artist: it asks the credit fetcher for the tracks' lists, splits what the
providers confirm and asks the user about the rest. Its own tests cover that
(tests/repair_jobs/test_comma_artist_splitter.py); here, the hand-over."""

from __future__ import annotations

import pytest

from core.library2 import native_enrich as NE
from database.music_database import MusicDatabase


def _album(db, artist, spotify_id=None):
    with db._get_connection() as conn:
        aid = conn.execute("INSERT INTO lib2_artists(name, sort_name, spotify_id) VALUES(?,?,?)",
                           (artist, artist, spotify_id)).lastrowid
        album = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
                             "VALUES(?, 'Album', 'album')", (aid,)).lastrowid
        track = conn.execute("INSERT INTO lib2_tracks(album_id, title, spotify_id) "
                             "VALUES(?, 'Song', 'sp-t')", (album,)).lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
                     "VALUES(?, ?, 'primary', 0)", (track, aid))
        conn.commit()
    return aid, album, track


@pytest.fixture
def asked(monkeypatch):
    import core.repair_worker as rw
    calls = []
    monkeypatch.setattr(rw, "request_artist_split",
                        lambda ids, reason="": calls.extend(ids) or True)
    return calls


def _primary(db, album):
    with db._get_connection() as conn:
        return conn.execute("SELECT ar.name FROM lib2_albums al JOIN lib2_artists ar "
                            "ON ar.id=al.primary_artist_id WHERE al.id=?", (album,)).fetchone()[0]


@pytest.mark.parametrize("name", ["Camellia & Toby Fox", "Camellia feat. Toby Fox",
                                  "Camellia, Toby Fox", "Above & Beyond"])
def test_an_album_artist_reading_as_a_list_goes_to_split_artists(tmp_path, asked, name):
    db = MusicDatabase(str(tmp_path / "m.db"))
    aid, album, _track = _album(db, name)
    assert NE.settle_new_album_artist(db, album) is True
    assert asked == [aid]
    # nothing is split here, a band included: Split Artists decides
    assert _primary(db, album) == name


def test_an_identified_or_plain_artist_is_left_alone(tmp_path, asked):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _aid, identified, _t = _album(db, "Hall & Oates", spotify_id="sp-ho")
    _aid, plain, _t = _album(db, "Camellia")
    assert NE.settle_new_album_artist(db, identified) is False
    assert NE.settle_new_album_artist(db, plain) is False
    assert NE.settle_new_album_artist(db, 999) is False
    assert asked == []


def test_without_a_worker_the_hand_over_is_a_no_op(tmp_path, monkeypatch):
    import core.repair_worker as rw
    monkeypatch.setattr(rw, "_ACTIVE_WORKER", None)
    db = MusicDatabase(str(tmp_path / "m.db"))
    _aid, album, _t = _album(db, "Camellia & Toby Fox")
    assert NE.settle_new_album_artist(db, album) is False
    assert _primary(db, album) == "Camellia & Toby Fox"
