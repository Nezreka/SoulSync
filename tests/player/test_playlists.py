"""the player's music playlists: yours and mirrored, each song with the
library track it plays, or none when this profile can't play it"""

import pytest

from core.player import playlists as P
from core.playlists.user_playlists import add_tracks, create_playlist, liked_playlist
from core.subsonic import library as L


@pytest.fixture
def mdb(tmp_path, monkeypatch):
    from core.library_scope import invalidate_library_scope_cache
    from database.music_database import MusicDatabase
    d = MusicDatabase(str(tmp_path / "library.db"))
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: d)
    invalidate_library_scope_cache()
    L.forget_snapshots()
    P._forget_matches()
    yield d
    P._forget_matches()
    L.forget_snapshots()
    invalidate_library_scope_cache()


def _x(db, sql, params=()):
    with db._get_connection() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


@pytest.fixture
def lib(mdb):
    ar = _x(mdb, "INSERT INTO lib2_artists (name) VALUES ('Mitski')")
    al = _x(mdb, "INSERT INTO lib2_albums (primary_artist_id, title, year) VALUES (?, 'Be the Cowboy', 2018)", (ar,))
    out = {}
    for n, title, explicit in ((1, "Geyser", 0), (2, "Nobody", 1)):
        tid = _x(mdb, "INSERT INTO lib2_tracks (album_id, title, track_number, duration, explicit) "
                      "VALUES (?, ?, ?, 180000, ?)", (al, title, n, explicit))
        _x(mdb, "INSERT INTO lib2_track_files (track_id, path, format, is_primary, file_state) "
                "VALUES (?, ?, 'flac', 1, 'active')", (tid, f"/m/{tid}.flac"))
        out[title] = tid
    # a song the catalogue knows with no file: not playable
    out["Gone"] = _x(mdb, "INSERT INTO lib2_tracks (album_id, title, track_number, duration) "
                          "VALUES (?, 'Gone', 3, 1000)", (al,))
    out["album"] = al
    return out


def test_lists_yours_with_liked_first_and_mirrored_apart(mdb):
    mine = create_playlist(mdb, 1, "Road trip")
    liked_playlist(mdb, 1, create=True)
    mdb.mirror_playlist(source="spotify", source_playlist_id="sp1", name="Discover Weekly", tracks=[],
                        profile_id=1)
    create_playlist(mdb, 2, "Someone else's")
    shelf = P.list_for(mdb, 1)
    assert [p["liked"] for p in shelf["yours"]] == [True, False]
    assert shelf["yours"][1]["id"] == mine and shelf["yours"][1]["kind"] == "yours"
    assert [p["name"] for p in shelf["mirrored"]] == ["Discover Weekly"]
    assert all(p["name"] != "Someone else's" for p in shelf["yours"])


def test_songs_resolve_to_library_tracks_by_stamp_or_name(mdb, lib):
    pid = create_playlist(mdb, 1, "Mix")
    playlist = mdb.get_mirrored_playlist(pid, profile_id=1)
    add_tracks(mdb, playlist, [
        {"title": "Geyser", "artist": "Mitski", "lib2_track_id": lib["Geyser"]},   # stamped
        {"title": "Nobody", "artist": "Mitski", "album": "Be the Cowboy"},         # by name
        {"title": "Gone", "artist": "Mitski", "lib2_track_id": lib["Gone"]},       # no file
        {"title": "Not Here", "artist": "Nobody Knows"},                           # not in the library
    ])
    out = P.detail(mdb, 1, pid, scope="shared", hide_explicit=False)
    ids = [s["track_id"] for s in out["songs"]]
    assert ids == [lib["Geyser"], lib["Nobody"], None, None]
    assert out["playable_count"] == 2
    assert out["songs"][0]["album_id"] == lib["album"]
    assert out["songs"][3]["title"] == "Not Here"
    assert [x["unplayable"] for x in out["songs"]] == [None, None, "missing", "missing"]


def test_hidden_explicit_songs_do_not_play(mdb, lib):
    pid = create_playlist(mdb, 1, "Mix")
    add_tracks(mdb, mdb.get_mirrored_playlist(pid, profile_id=1),
               [{"title": "Nobody", "artist": "Mitski", "lib2_track_id": lib["Nobody"]}])
    out = P.detail(mdb, 1, pid, scope="shared", hide_explicit=True)
    assert out["songs"][0]["track_id"] is None
    # and it says why: hidden, not missing
    assert out["songs"][0]["unplayable"] == "explicit"
    assert out["hides_explicit"] is True


def test_another_profiles_playlist_is_not_found(mdb):
    pid = create_playlist(mdb, 2, "Private")
    assert P.detail(mdb, 1, pid, scope="shared", hide_explicit=False) is None


def test_an_artists_top_songs_are_the_most_played_they_can_play(mdb, lib):
    with mdb._get_connection() as conn:
        conn.execute("UPDATE lib2_tracks SET play_count = 9 WHERE id = ?", (lib["Nobody"],))
        conn.execute("UPDATE lib2_tracks SET play_count = 3 WHERE id = ?", (lib["Geyser"],))
        conn.commit()
        ar = conn.execute("SELECT primary_artist_id FROM lib2_albums WHERE id = ?", (lib["album"],)).fetchone()[0]
        top = P.top_tracks(conn, ar, scope="shared", hide_explicit=False)
        assert [t["title"] for t in top] == ["Nobody", "Geyser"]  # "Gone" has no file
        hidden = P.top_tracks(conn, ar, scope="shared", hide_explicit=True)
        assert [t["title"] for t in hidden] == ["Geyser"]


def test_a_song_on_two_releases_is_popular_once(mdb, lib):
    with mdb._get_connection() as conn:
        ar = conn.execute("SELECT primary_artist_id FROM lib2_albums WHERE id = ?", (lib["album"],)).fetchone()[0]
        deluxe = conn.execute("INSERT INTO lib2_albums (primary_artist_id, title) VALUES (?, 'Deluxe')", (ar,)).lastrowid
        again = conn.execute("INSERT INTO lib2_tracks (album_id, title, duration, play_count) VALUES (?, 'Geyser', 1, 50)",
                             (deluxe,)).lastrowid
        conn.execute("INSERT INTO lib2_track_files (track_id, path, format, is_primary, file_state) "
                     "VALUES (?, '/m/x.flac', 'flac', 1, 'active')", (again,))
        conn.commit()
        top = P.top_tracks(conn, ar, scope="shared", hide_explicit=False)
        assert [(t["title"], t["track_id"]) for t in top if t["title"] == "Geyser"] == [("Geyser", again)]


def test_a_playlist_is_matched_once_until_it_or_the_library_changes(mdb, lib, monkeypatch):
    pid = create_playlist(mdb, 1, "Mix")
    add_tracks(mdb, mdb.get_mirrored_playlist(pid, profile_id=1),
               [{"title": "Geyser", "artist": "Mitski"}, {"title": "Later", "artist": "Mitski"}])
    calls = []
    real = L.Library.find_tracks
    monkeypatch.setattr(L.Library, "find_tracks", lambda self, w: calls.append(len(w)) or real(self, w))
    first = P.detail(mdb, 1, pid, scope="shared", hide_explicit=False)
    again = P.detail(mdb, 1, pid, scope="shared", hide_explicit=False)
    assert len(calls) == 1 and first["songs"] == again["songs"]
    assert [x["track_id"] for x in first["songs"]] == [lib["Geyser"], None]
    # "Later" lands in the library: the next open sees it
    tid = _x(mdb, "INSERT INTO lib2_tracks (album_id, title, track_number, duration) VALUES (?, 'Later', 4, 1000)",
             (lib["album"],))
    _x(mdb, "INSERT INTO lib2_track_files (track_id, path, format, is_primary, file_state) "
            "VALUES (?, '/m/later.flac', 'flac', 1, 'active')", (tid,))
    after = P.detail(mdb, 1, pid, scope="shared", hide_explicit=False)
    assert len(calls) == 2
    assert [x["track_id"] for x in after["songs"]] == [lib["Geyser"], tid]


def _stamp(mdb, pid, position, tid, kind):
    import json as _json
    with mdb._get_connection() as conn:
        row = conn.execute("SELECT id, extra_data FROM mirrored_playlist_tracks WHERE playlist_id=? AND position=?",
                           (pid, position)).fetchone()
        extra = _json.loads(row[1] or "{}")
        extra.update({"library_track_id": tid, "library_track_id_kind": kind})
        conn.execute("UPDATE mirrored_playlist_tracks SET extra_data=? WHERE id=?", (_json.dumps(extra), row[0]))
        conn.commit()


def test_a_stamp_from_another_id_space_falls_back_to_the_name(mdb, lib):
    """boulder's vibes, nov 21 2026: rows stamped with ids from another
    numbering and no kind. one hit a real lib2 id (another song), the rest
    nothing, and the player never tried the names"""
    def stamped(name, stamps):
        pid = create_playlist(mdb, 1, name)
        add_tracks(mdb, mdb.get_mirrored_playlist(pid, profile_id=1),
                   [{"title": "Geyser", "artist": "Mitski"}, {"title": "Nobody", "artist": "Mitski"}])
        rows = mdb.get_mirrored_playlist_tracks(pid, profile_id=1)
        for r, (tid, kind) in zip(rows, stamps, strict=True):
            _stamp(mdb, pid, r["position"], tid, kind)
        return [x["track_id"] for x in P.detail(mdb, 1, pid, scope="shared", hide_explicit=False)["songs"]]

    want = [lib["Geyser"], lib["Nobody"]]
    # nothing there, and another song's id: both by name
    assert stamped("Vibes", [(999999, None), (lib["Geyser"], None)]) == want
    # its own title: kept; a media server's id: by name
    assert stamped("Vibes 2", [(lib["Geyser"], None), (424242, "server")]) == want


def test_a_manual_match_is_trusted_whatever_its_title(mdb, lib):
    pid = create_playlist(mdb, 1, "Mix")
    add_tracks(mdb, mdb.get_mirrored_playlist(pid, profile_id=1), [{"title": "Geyser (Live)", "artist": "Mitski"}])
    position = mdb.get_mirrored_playlist_tracks(pid, profile_id=1)[0]["position"]
    _stamp(mdb, pid, position, lib["Geyser"], "lib2")
    out = P.detail(mdb, 1, pid, scope="shared", hide_explicit=False)
    assert out["songs"][0]["track_id"] == lib["Geyser"]


def test_a_featured_credit_counts_and_a_name_inside_another_does_not(mdb, lib):
    """credits come from lib2_track_artists (indexed). the old LIKE on the
    track artist string scanned every track and matched "U2" inside other
    names"""
    with mdb._get_connection() as conn:
        u2 = conn.execute("INSERT INTO lib2_artists (name) VALUES ('U2')").lastrowid
        other = conn.execute("INSERT INTO lib2_artists (name) VALUES ('Someone')").lastrowid
        al = conn.execute("INSERT INTO lib2_albums (primary_artist_id, title) VALUES (?, 'Other')",
                          (other,)).lastrowid
        feat = conn.execute("INSERT INTO lib2_tracks (album_id, title, duration, track_artist) "
                            "VALUES (?, 'With U2', 1, 'Someone feat. U2')", (al,)).lastrowid
        fake = conn.execute("INSERT INTO lib2_tracks (album_id, title, duration, track_artist) "
                            "VALUES (?, 'Not them', 1, 'MU2SIC')", (al,)).lastrowid
        for tid in (feat, fake):
            conn.execute("INSERT INTO lib2_track_files (track_id, path, format, is_primary, file_state) "
                         "VALUES (?, ?, 'flac', 1, 'active')", (tid, f"/m/{tid}.flac"))
        conn.execute("INSERT INTO lib2_track_artists (track_id, artist_id, role) VALUES (?, ?, 'featured')",
                     (feat, u2))
        conn.commit()
        top = P.top_tracks(conn, u2, scope="shared", hide_explicit=False)
    assert [t["title"] for t in top] == ["With U2"]


def test_the_lobby_picks_an_owned_track_without_sorting_the_library(mdb, lib, monkeypatch):
    import api.player as player_api
    seen = set()
    for _ in range(40):
        pick = player_api._pick_default_lobby_track()
        assert pick is not None
        seen.add(int(pick["track_id"]))
    # only tracks with a file; "Gone" has none
    assert seen <= {lib["Geyser"], lib["Nobody"]}
    assert lib["Gone"] not in seen
