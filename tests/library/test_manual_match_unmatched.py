"""Tests for mlm.list_unmatched_wanted_tracks (#1289 worklist).

The Manual Library Match modal pre-populates its source panel with every
wanted-but-unmatched track: wishlist rows and mirrored playlist tracks with
no library link and no manual match.
"""

from __future__ import annotations

import pytest

from core.library import manual_library_match as mlm
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _wishlist_track(track_id, title="Song", artist="Artist", album="Album"):
    return {
        "id": track_id,
        "name": title,
        "artists": [{"name": artist}],
        "album": {"name": album},
    }


def _add_mirrored_playlist(db, pid=10, name="Test Mix", profile_id=1, source="spotify"):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO mirrored_playlists (id, source, source_playlist_id, name, profile_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (pid, source, f"pl{pid}", name, profile_id),
        )
        conn.commit()


def _add_mirrored_track(db, playlist_id, position, name, artist, source_track_id):
    with db._get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO mirrored_playlist_tracks"
            " (playlist_id, position, track_name, artist_name, source_track_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (playlist_id, position, name, artist, source_track_id),
        )
        conn.commit()
        return cur.lastrowid


def _add_library_track(db, track_id, title, spotify_track_id):
    from tests import lib2_seed
    with db._get_connection() as conn:
        lib2_seed.track(conn, 'Lib Artist', 'Lib Album', title, spotify_id=spotify_track_id)
        conn.commit()


# ---------------------------------------------------------------------------
# wishlist rows
# ---------------------------------------------------------------------------

def test_unmatched_wishlist_track_included(db):
    db.add_to_wishlist(spotify_track_data=_wishlist_track("sp-new"), profile_id=1)
    tracks = mlm.list_unmatched_wanted_tracks(db, 1)
    assert len(tracks) == 1
    t = tracks[0]
    assert t["source_track_id"] == "sp-new"
    assert t["title"] == "Song"
    assert t["artist"] == "Artist"
    assert t["album"] == "Album"
    assert t["context"] == "Wishlist"
    assert t["source"] == "spotify"


def test_wishlist_track_with_manual_match_excluded(db):
    db.add_to_wishlist(spotify_track_data=_wishlist_track("sp-linked"), profile_id=1)
    # Manual match saved under a DIFFERENT source label still covers it:
    # coverage is server-agnostic by source_track_id.
    assert db.save_manual_library_match(
        1, "mirrored", "sp-linked", "lib-1",
        source_title="Song", source_artist="Artist",
    )
    assert mlm.list_unmatched_wanted_tracks(db, 1) == []


def test_wishlist_track_already_in_library_excluded(db):
    db.add_to_wishlist(spotify_track_data=_wishlist_track("sp-owned"), profile_id=1)
    _add_library_track(db, 1, "Song", "sp-owned")
    assert mlm.list_unmatched_wanted_tracks(db, 1) == []


def test_wishlist_scoped_to_profile(db):
    db.add_to_wishlist(spotify_track_data=_wishlist_track("sp-other"), profile_id=2)
    assert mlm.list_unmatched_wanted_tracks(db, 1) == []
    assert len(mlm.list_unmatched_wanted_tracks(db, 2)) == 1


# ---------------------------------------------------------------------------
# mirrored playlist tracks
# ---------------------------------------------------------------------------

def test_mirrored_unmatched_track_included_with_playlist_context(db):
    _add_mirrored_playlist(db, pid=10, name="Test Mix")
    _add_mirrored_track(db, 10, 0, "Mirror Song", "Mirror Artist", "sp-m1")
    tracks = mlm.list_unmatched_wanted_tracks(db, 1)
    assert len(tracks) == 1
    t = tracks[0]
    assert t["source_track_id"] == "sp-m1"
    assert t["title"] == "Mirror Song"
    assert t["artist"] == "Mirror Artist"
    assert t["context"] == "Test Mix"
    assert t["source"] == "spotify"


def test_mirrored_track_in_library_excluded(db):
    _add_mirrored_playlist(db, pid=10, name="Test Mix")
    tid = _add_mirrored_track(db, 10, 0, "Owned Song", "Artist", "sp-m2")
    # Same stored cache the sync membership recorder writes and save_match()
    # stamps via refresh_mirrored_library_flags.
    assert db.update_mirrored_track_extra_data(tid, {"in_library": True})
    assert mlm.list_unmatched_wanted_tracks(db, 1) == []


def test_mirrored_track_with_manual_match_excluded(db):
    _add_mirrored_playlist(db, pid=10, name="Test Mix")
    _add_mirrored_track(db, 10, 0, "Linked Song", "Artist", "sp-m3")
    assert db.save_manual_library_match(
        1, "spotify", "sp-m3", "lib-9",
        source_title="Linked Song", source_artist="Artist",
    )
    assert mlm.list_unmatched_wanted_tracks(db, 1) == []


def test_track_in_wishlist_and_playlist_appears_once(db):
    db.add_to_wishlist(spotify_track_data=_wishlist_track("sp-both"), profile_id=1)
    _add_mirrored_playlist(db, pid=10, name="Test Mix")
    _add_mirrored_track(db, 10, 0, "Both Song", "Artist", "sp-both")
    tracks = mlm.list_unmatched_wanted_tracks(db, 1)
    assert len(tracks) == 1
    # Wishlist wins the dedupe, matching search_source_candidates' order.
    assert tracks[0]["context"] == "Wishlist"


# ---------------------------------------------------------------------------
# behavior
# ---------------------------------------------------------------------------

def test_limit_respected(db):
    for i in range(5):
        db.add_to_wishlist(
            spotify_track_data=_wishlist_track(f"sp-lim-{i}", title=f"Song {i}"),
            profile_id=1,
        )
    tracks = mlm.list_unmatched_wanted_tracks(db, 1, limit=2)
    assert len(tracks) == 2


def test_most_recent_first(db):
    db.add_to_wishlist(spotify_track_data=_wishlist_track("sp-old", title="Old"), profile_id=1)
    db.add_to_wishlist(spotify_track_data=_wishlist_track("sp-new", title="New"), profile_id=1)
    tracks = mlm.list_unmatched_wanted_tracks(db, 1)
    assert [t["source_track_id"] for t in tracks] == ["sp-new", "sp-old"]


def test_empty_query_search_source_candidates_unchanged(db):
    # The worklist is additive: the old search behavior must not change.
    db.add_to_wishlist(spotify_track_data=_wishlist_track("sp-q"), profile_id=1)
    assert mlm.search_source_candidates(db, "", 1) == []
    assert mlm.search_source_candidates(db, "   ", 1) == []


def test_result_shape_matches_search_candidates(db):
    db.add_to_wishlist(spotify_track_data=_wishlist_track("sp-shape"), profile_id=1)
    (t,) = mlm.list_unmatched_wanted_tracks(db, 1)
    assert set(t) == {
        "source", "source_track_id", "title", "artist", "album",
        "context", "added_at",
    }


def test_wishlist_row_of_one_release_is_listed_by_its_track_id(db):
    """A wish for one release is keyed ``<track>::<album>``; the worklist
    offers the source track, and an owned one stays out."""
    track = _wishlist_track("sp-rel")
    track["album"]["id"] = "al-1"
    db.add_to_wishlist(spotify_track_data=track, profile_id=1)
    owned = _wishlist_track("sp-owned-rel")
    owned["album"]["id"] = "al-2"
    db.add_to_wishlist(spotify_track_data=owned, profile_id=1)
    _add_library_track(db, 1, "Song", "sp-owned-rel")

    rows = mlm.list_unmatched_wanted_tracks(db, profile_id=1)

    assert [r["source_track_id"] for r in rows] == ["sp-rel"]
