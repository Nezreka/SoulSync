"""One provider release is one Library-v2 entity.

The 2026-08-22 production report found 20 album provider-id groups and 8 track
groups where the SAME external id sat on several distinct Library-v2 rows —
"Memory Reboot" and "Memory Reboot (Slowed)" sharing one Spotify album id,
"EVA 2"/"EVA 3"/"EVA 4" sharing one iTunes id, and in one case two unrelated
albums by two different artists. Two causes, both covered here:

1. the automated matcher folded away exactly the words that distinguish a
   slowed / sped-up / sequel release before scoring the title, and
2. nothing checked whether another row already claimed the id.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.match_status import (
    ProviderIdentityConflict,
    provider_id_owner,
    set_library_v2_match,
)
from core.library2.native_enrich import titles_are_same_release


@pytest.fixture()
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE lib2_albums (
            id INTEGER PRIMARY KEY, title TEXT, spotify_id TEXT,
            musicbrainz_id TEXT, external_ids TEXT, updated_at TEXT);
        CREATE TABLE lib2_artists (
            id INTEGER PRIMARY KEY, name TEXT, spotify_id TEXT,
            musicbrainz_id TEXT, external_ids TEXT, updated_at TEXT);
        CREATE TABLE lib2_tracks (
            id INTEGER PRIMARY KEY, title TEXT, spotify_id TEXT,
            musicbrainz_id TEXT, external_ids TEXT, updated_at TEXT);
        INSERT INTO lib2_albums (id, title) VALUES (3747, 'Memory Reboot');
        INSERT INTO lib2_albums (id, title) VALUES (3731, 'Memory Reboot (Slowed)');
        """
    )
    yield connection
    connection.close()


# --- the write-time invariant ---------------------------------------------

def test_second_album_cannot_claim_a_spotify_id_another_album_holds(conn):
    set_library_v2_match(conn, "album", 3747, "spotify", "38leU2pvDRxNx2u59BENZb")
    with pytest.raises(ProviderIdentityConflict) as excinfo:
        set_library_v2_match(conn, "album", 3731, "spotify", "38leU2pvDRxNx2u59BENZb")
    assert excinfo.value.owner_id == 3747

    # The refused write leaves the loser completely untouched.
    row = conn.execute("SELECT spotify_id, external_ids FROM lib2_albums WHERE id=3731").fetchone()
    assert row["spotify_id"] is None


def test_the_conflict_also_covers_external_ids_providers(conn):
    set_library_v2_match(conn, "album", 3747, "itunes", "1663316684")
    with pytest.raises(ProviderIdentityConflict):
        set_library_v2_match(conn, "album", 3731, "itunes", "1663316684")


def test_re_setting_the_same_id_on_the_same_entity_is_not_a_conflict(conn):
    set_library_v2_match(conn, "album", 3747, "spotify", "abc")
    set_library_v2_match(conn, "album", 3747, "spotify", "abc")
    assert provider_id_owner(conn, "album", "spotify", "abc") == 3747


def test_a_deliberate_user_match_moves_the_id(conn):
    """Correcting the matcher's mistake is exactly why someone opens the match
    dialog — so a manual match steals, and the loser is cleared in the same
    transaction rather than both rows carrying the id."""
    set_library_v2_match(conn, "album", 3747, "spotify", "abc")
    set_library_v2_match(conn, "album", 3731, "spotify", "abc", steal=True)

    assert provider_id_owner(conn, "album", "spotify", "abc") == 3731
    assert conn.execute(
        "SELECT spotify_id FROM lib2_albums WHERE id=3747").fetchone()["spotify_id"] is None


def test_the_same_id_on_different_entity_kinds_is_fine(conn):
    """Provider id namespaces are per-kind — a Deezer artist 123 and a Deezer
    album 123 are unrelated."""
    conn.execute("INSERT INTO lib2_artists (id, name) VALUES (9, 'A')")
    set_library_v2_match(conn, "album", 3747, "deezer", "123")
    set_library_v2_match(conn, "artist", 9, "deezer", "123")
    assert provider_id_owner(conn, "album", "deezer", "123") == 3747
    assert provider_id_owner(conn, "artist", "deezer", "123") == 9


def test_clearing_an_id_never_conflicts(conn):
    set_library_v2_match(conn, "album", 3747, "spotify", "abc")
    set_library_v2_match(conn, "album", 3731, "spotify", None)
    set_library_v2_match(conn, "album", 3747, "spotify", None)
    assert provider_id_owner(conn, "album", "spotify", "abc") is None


# --- the match-time invariant ---------------------------------------------

@pytest.mark.parametrize("left,right", [
    # every distinct album pair the production report flagged
    ("Memory Reboot (Slowed)", "Memory Reboot"),
    ("RAVE (Slowed)", "RAVE"),
    ("Particles (Slowed)", "particles"),
    ("DARKSIDE (Slowed + Reverb)", "DARKSIDE"),
    ("Fainted (Slowed)", "Fainted"),
    ("Night Drive (Slowed + Reverb)", "Night Drive"),
    ("NEON BLADE (Slowed + Reverb)", "NEON BLADE"),
    ("GigaChad Theme (Phonk House Version) [Slowed]",
     "GigaChad Theme (Phonk House Version)"),
    ("2000 - sped up", "2000"),
    # sequence numbers: "EVA 2" scored 0.80 against "EVA 4"
    ("NEON BLADE 2", "NEON BLADE"),
    ("EVA 2", "EVA 4"),
    ("METAMORPHOSIS 2 (Slowed + Reverb)", "METAMORPHOSIS"),
    ("Sea Of Feelings 2", "Sea Of Feelings"),
])
def test_recording_variants_are_not_the_same_release(left, right):
    assert titles_are_same_release(left, right) is False
    assert titles_are_same_release(right, left) is False


@pytest.mark.parametrize("left,right", [
    ("Abbey Road", "Abbey Road"),
    ("abbey road", "Abbey Road"),
    # Edition words describe the SAME recordings. Treating them as variants
    # would cost real matching coverage for no correctness gain.
    ("Abbey Road (Remastered)", "Abbey Road"),
    ("Discovery (Deluxe Edition)", "Discovery"),
    ("Kind of Blue (Legacy Edition)", "Kind of Blue"),
    ("In Rainbows (2016 Reissue)", "In Rainbows"),
    # A variant word that is part of BOTH titles is not a difference.
    ("Live at Wembley", "Live At Wembley"),
])
def test_editions_and_equal_titles_still_match(left, right):
    assert titles_are_same_release(left, right) is True


# --- where sharing is legitimate ------------------------------------------

def test_a_musicbrainz_recording_id_may_cover_several_tracks(conn):
    """MusicBrainz keys by RECORDING, not by release: the album version and the
    greatest-hits version of one song share a single MBID, and the catalogue
    holds a track row for each. Refusing that would reject correct matches."""
    conn.execute("INSERT INTO lib2_tracks (id, title) VALUES (1, 'Song')")
    conn.execute("INSERT INTO lib2_tracks (id, title) VALUES (2, 'Song')")
    set_library_v2_match(conn, "track", 1, "musicbrainz", "rec-mbid")
    set_library_v2_match(conn, "track", 2, "musicbrainz", "rec-mbid")

    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks WHERE musicbrainz_id='rec-mbid'"
    ).fetchone()[0] == 2


@pytest.mark.parametrize("service", ["lastfm", "audiodb", "genius"])
def test_content_keyed_track_services_may_be_shared(conn, service):
    conn.execute("INSERT INTO lib2_tracks (id, title) VALUES (1, 'Song')")
    conn.execute("INSERT INTO lib2_tracks (id, title) VALUES (2, 'Song')")
    set_library_v2_match(conn, "track", 1, service, "same")
    set_library_v2_match(conn, "track", 2, service, "same")


@pytest.mark.parametrize("service", ["spotify", "deezer", "itunes"])
def test_release_scoped_track_ids_are_still_unique(conn, service):
    """Spotify/Deezer/iTunes issue a per-release track id — the same recording
    on two albums has two ids, so two rows holding one is always a mistake."""
    conn.execute("INSERT INTO lib2_tracks (id, title) VALUES (1, 'Song')")
    conn.execute("INSERT INTO lib2_tracks (id, title) VALUES (2, 'Song')")
    set_library_v2_match(conn, "track", 1, service, "t-1")
    with pytest.raises(ProviderIdentityConflict):
        set_library_v2_match(conn, "track", 2, service, "t-1")


def test_the_conflict_report_lists_existing_duplicates(conn):
    """The guard only stops NEW conflicts; databases that predate it still
    carry the old ones and the user has to be able to see them."""
    from core.library2.match_status import provider_id_conflicts

    conn.execute("UPDATE lib2_albums SET spotify_id='shared' WHERE id IN (3747, 3731)")
    found = provider_id_conflicts(conn, "album")
    assert [(f["service"], f["external_id"], sorted(f["entity_ids"])) for f in found] == [
        ("spotify", "shared", [3731, 3747]),
    ]


def test_the_conflict_report_ignores_legitimately_shared_track_ids(conn):
    from core.library2.match_status import provider_id_conflicts

    conn.execute("INSERT INTO lib2_tracks (id, title, musicbrainz_id) VALUES (1, 'S', 'rec')")
    conn.execute("INSERT INTO lib2_tracks (id, title, musicbrainz_id) VALUES (2, 'S', 'rec')")
    assert provider_id_conflicts(conn, "track") == []
