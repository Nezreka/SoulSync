"""#808: parenthetical qualifiers that restate album context must not block
library-presence matching.

carlosjfcasero's case: the wishlist held 'Champagne Supernova (OurVinyl
Sessions)' (Deezer/iTunes title) while the library track was on the album
'Champagne Supernova (OurVinyl Sessions)'. When one side's title carries the
qualifier and the other doesn't, the length-ratio penalty crushed the pair to
~0.17 — wishlist cleanup never recognised the owned edition and the track
re-appeared every cycle. The qualifier appearing in the (db) album title
proves it's album context, not a different version.
"""

from __future__ import annotations

import os

import pytest

from core.text.title_match import strip_redundant_context_qualifiers
from database.music_database import MusicDatabase


# ── the pure helper ──────────────────────────────────────────────────────────

def test_qualifier_confirmed_by_album_is_stripped():
    out = strip_redundant_context_qualifiers(
        'champagne supernova (ourvinyl sessions)',
        'champagne supernova (ourvinyl sessions)',  # db album title
    )
    assert out == 'champagne supernova'


def test_version_marker_on_unrelated_album_is_kept():
    assert strip_redundant_context_qualifiers('song (live)', 'studio album') == 'song (live)'
    assert strip_redundant_context_qualifiers('song (remix)', 'the album') == 'song (remix)'


def test_version_marker_confirmed_by_album_is_stripped():
    # Owning 'Song (Live)' on the album 'Live at Wembley' IS owning that cut.
    assert strip_redundant_context_qualifiers('song (live)', 'live at wembley') == 'song'


def test_word_boundary_containment():
    # 'live' inside 'alive' must NOT count as context confirmation.
    assert strip_redundant_context_qualifiers('song (live)', 'alive and well') == 'song (live)'


def test_no_context_or_title_untouched():
    assert strip_redundant_context_qualifiers('plain title', 'anything') == 'plain title'
    assert strip_redundant_context_qualifiers('', 'ctx') == ''
    assert strip_redundant_context_qualifiers('song (x)') == 'song (x)'


# ── end to end through check_track_exists (the wishlist-cleanup contract) ────

@pytest.fixture()
def lib_db(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    conn = db._get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO artists (id, name, server_source) VALUES ('a1', 'Jillette Johnson', 'plex')")
    c.execute("""INSERT INTO albums (id, title, artist_id, server_source)
                 VALUES ('al1', 'Champagne Supernova (OurVinyl Sessions)', 'a1', 'plex')""")
    c.execute("""INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source)
                 VALUES ('t1', 'al1', 'a1', 'Champagne Supernova', '/m/cs.mp3', 'plex')""")
    # Version-safety control: a live cut on a studio-named album.
    c.execute("""INSERT INTO albums (id, title, artist_id, server_source)
                 VALUES ('al2', 'Water In A Whale', 'a1', 'plex')""")
    c.execute("""INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source)
                 VALUES ('t2', 'al2', 'a1', 'Cameron', '/m/c.mp3', 'plex')""")
    conn.commit()
    conn.close()
    return db


def test_808_qualified_search_matches_bare_library_track(lib_db):
    """The reported direction: source/wishlist title carries the qualifier,
    library title is bare, the library ALBUM carries the qualifier."""
    match, conf = lib_db.check_track_exists(
        'Champagne Supernova (OurVinyl Sessions)', 'Jillette Johnson',
        confidence_threshold=0.7, server_source='plex',
        album='Jillette Johnson | OurVinyl Sessions',
    )
    assert match is not None and conf >= 0.7


def test_version_marker_still_blocks_without_album_confirmation(lib_db):
    """'Cameron (Live)' must NOT match the studio 'Cameron' — the qualifier
    appears in no album context, so the mismatch penalty stands."""
    match, conf = lib_db.check_track_exists(
        'Cameron (Live)', 'Jillette Johnson',
        confidence_threshold=0.7, server_source='plex',
    )
    assert conf < 0.7


def test_different_song_prefix_still_blocked(lib_db):
    """'Champagne' alone is a different (hypothetical) song — the length
    penalty on the reduced forms still applies."""
    match, conf = lib_db.check_track_exists(
        'Champagne', 'Jillette Johnson',
        confidence_threshold=0.7, server_source='plex',
    )
    assert conf < 0.7
