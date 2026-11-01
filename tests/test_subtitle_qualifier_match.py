"""#825: bracketed SUBTITLES must not block library-presence matching.

carlosjfcasero's case (round 2): the mirrored-playlist sync auto-added
'Llamando a la tierra (Serenade From the Stars)' by M-Clan to the wishlist on
every run, even though his library has the song (stored bare). The subtitle is
the song's official parenthetical — it restates no album context, so the #808
strip kept it, and the length-ratio penalty crushed the pair to ~0.14. The
sync matcher reported it missing forever AND the wishlist cleanup (the same
matcher) could never remove it.

Fix: a bracketed qualifier with no version-marker token and no new numeric
token is a subtitle — compare with it stripped. Version qualifiers ('(Live)',
'(Versión 1988)', '(Dueto 2007)') still block, both EN and ES.
"""

from __future__ import annotations

import pytest

from core.text.title_match import strip_subtitle_qualifiers
from database.music_database import MusicDatabase


# ── the pure helper ──────────────────────────────────────────────────────────

def test_subtitle_is_stripped():
    out = strip_subtitle_qualifiers(
        'llamando a la tierra (serenade from the stars)', 'llamando a la tierra')
    assert out == 'llamando a la tierra'


def test_version_markers_kept_english():
    for q in ('live', 'remix', 'acoustic', 'instrumental', 'demo', 'radio edit'):
        assert strip_subtitle_qualifiers(f'song ({q})', 'song') == f'song ({q})'


def test_version_markers_kept_spanish():
    assert strip_subtitle_qualifiers('song (version 1988)', 'song') == 'song (version 1988)'
    assert strip_subtitle_qualifiers('song (dueto 2007)', 'song') == 'song (dueto 2007)'
    assert strip_subtitle_qualifiers('song (en directo en el liceu / 2008)', 'song') \
        == 'song (en directo en el liceu / 2008)'


def test_new_numeric_token_kept():
    # '(Pt. 2)' / '(2007)' are different releases, never subtitles.
    assert strip_subtitle_qualifiers('song (pt. 2)', 'song') == 'song (pt. 2)'
    assert strip_subtitle_qualifiers('song (2007)', 'song') == 'song (2007)'


def test_distinct_track_qualifiers_kept():
    # '(Interlude)' etc. are SEPARATE short tracks sharing the base name —
    # treating them as subtitles would wrongly count the full song as owned.
    for q in ('interlude', 'intro', 'outro', 'skit', 'freestyle'):
        assert strip_subtitle_qualifiers(f'song ({q})', 'song') == f'song ({q})'


def test_roman_numeral_parts_kept():
    # No digits, so the numeric guard alone can't catch these.
    assert strip_subtitle_qualifiers('song (pt. ii)', 'song') == 'song (pt. ii)'
    assert strip_subtitle_qualifiers('song (part two)', 'song') == 'song (part two)'
    assert strip_subtitle_qualifiers('song (vol. iii)', 'song') == 'song (vol. iii)'


def test_numeric_token_shared_with_other_title_is_fine():
    # The digit appears on the other side too — not a new release marker.
    assert strip_subtitle_qualifiers('song 2007 (the ballad)', 'song 2007') == 'song 2007'


def test_qualifier_restated_in_other_title_left_for_direct_compare():
    full = 'song (the ballad)'
    assert strip_subtitle_qualifiers(full, 'song (the ballad)') == full


def test_empty_and_plain_untouched():
    assert strip_subtitle_qualifiers('', 'x') == ''
    assert strip_subtitle_qualifiers('plain title', 'other') == 'plain title'


# ── end to end through check_track_exists (sync + cleanup contract) ──────────

@pytest.fixture()
def lib_db(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    conn = db._get_connection()
    c = conn.cursor()
    from tests.support.catalogue_seed import seed_album, seed_artist, seed_track

    artist = seed_artist(c, server_id='a1', name='M-Clan', server_source='jellyfin')
    album = seed_album(c, server_id='al1', title='Usar y tirar', artist_id=artist,
                       server_source='jellyfin')
    seed_track(c, server_id='t1', title='Llamando a la tierra', album_id=album,
               artist_id=artist, server_source='jellyfin',
               file_path='/m/llamando.mp3')
    seed_track(c, server_id='t2', title='Carolina', album_id=album,
               artist_id=artist, server_source='jellyfin',
               file_path='/m/carolina.mp3')
    conn.commit()
    conn.close()
    return db


def test_825_subtitled_search_matches_bare_library_track(lib_db):
    """The reported case verbatim: playlist title carries the subtitle, the
    library stores the bare title — must match (sync) and clean (cleanup)."""
    match, conf = lib_db.check_track_exists(
        'Llamando a la tierra (Serenade From the Stars)', 'M-Clan',
        confidence_threshold=0.7, server_source='jellyfin',
    )
    assert match is not None and conf >= 0.7
    assert match.title == 'Llamando a la tierra'


def test_825_reverse_direction_matches(lib_db):
    """Library could equally store the FULL title while the playlist has the
    bare one — both directions must match."""
    conn = lib_db._get_connection()
    c = conn.cursor()
    from tests.support.catalogue_seed import seed_track

    album = c.execute("SELECT id FROM lib2_albums WHERE server_id='al1'").fetchone()[0]
    artist = c.execute("SELECT id FROM lib2_artists WHERE server_id='a1'").fetchone()[0]
    seed_track(c, server_id='t3', title='Maggie (despierta)', album_id=album,
               artist_id=artist, server_source='jellyfin', file_path='/m/maggie.mp3')
    conn.commit()
    conn.close()
    match, conf = lib_db.check_track_exists(
        'Maggie', 'M-Clan', confidence_threshold=0.7, server_source='jellyfin')
    assert match is not None and conf >= 0.7


def test_live_version_still_blocked(lib_db):
    match, conf = lib_db.check_track_exists(
        'Llamando a la tierra (Live)', 'M-Clan',
        confidence_threshold=0.7, server_source='jellyfin',
    )
    assert conf < 0.7


def test_spanish_version_qualifiers_still_blocked(lib_db):
    for title in ('Carolina (Versión 1988)', 'Carolina (Dueto 2007)',
                  'Carolina (En Directo / 2005)'):
        match, conf = lib_db.check_track_exists(
            title, 'M-Clan', confidence_threshold=0.7, server_source='jellyfin')
        assert conf < 0.7, title


def test_different_song_prefix_still_blocked(lib_db):
    """Non-bracketed extensions are untouched — the length penalty stands."""
    match, conf = lib_db.check_track_exists(
        'Carolina en mi mente y otras cosas', 'M-Clan',
        confidence_threshold=0.7, server_source='jellyfin',
    )
    assert conf < 0.7


def test_batched_candidate_path_also_fixed(lib_db):
    """The sync matcher uses the candidate_tracks (batched) path — the fix must
    apply there too, not just the SQL-variation path."""
    candidates = lib_db.search_tracks(artist='M-Clan', limit=50, server_source='jellyfin')
    assert candidates
    match, conf = lib_db.check_track_exists(
        'Llamando a la tierra (Serenade From the Stars)', 'M-Clan',
        confidence_threshold=0.7, server_source='jellyfin',
        candidate_tracks=candidates,
    )
    assert match is not None and conf >= 0.7
