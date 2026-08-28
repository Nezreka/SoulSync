"""Tests for the Discogs discography classifier fix.

Two independent bugs made `get_artist_albums()` / `Album.from_discogs_release()`
mislabel far too much as generic "album":

1. The classifier checked for the substring 'compilation', but the
   `/artists/{id}/releases` endpoint `get_artist_albums()` actually calls
   returns Discogs' abbreviated format code "Comp" -- never the full word.
   Every compilation silently fell through to the 'album' default.
2. There was no filtering by medium at all -- non-audio formats (Blu-ray,
   DVD-V, Laserdisc, CDV, professional broadcast tape) landed in the
   generic 'album' bucket alongside real studio albums.

Fix: match the `Comp` abbreviation (and fold `ep` into `single`, since
Discogs' own artist pages have no separate EP facet), and exclude
non-audio-format releases from `get_artist_albums()`'s results via a new
per-item whitelist helper, `_is_non_audio_discogs_release()`. See the
module-level comment above that helper in `core.discogs_client` for why it
went through three design iterations (blacklist -> naive whitelist ->
per-item whitelist), each forced by a real release that broke the simpler
version.
"""

from core.discogs_client import Album, _is_non_audio_discogs_release


# ---------------------------------------------------------------------------
# Change 1 -- Comp abbreviation + ep folded into single
# ---------------------------------------------------------------------------

def test_comp_abbreviation_is_recognized_as_compilation():
    # Real format string from the bug report's reproduction (Naoya Matsuoka).
    album = Album.from_discogs_release({
        'id': 1, 'title': 'Drive Best!', 'format': 'CD, Comp, RM',
    })
    assert album.album_type == 'compilation'


def test_full_word_compilation_still_works():
    album = Album.from_discogs_release({
        'id': 2, 'title': 'Old Style', 'format': 'CD, Compilation',
    })
    assert album.album_type == 'compilation'


def test_ep_format_string_folds_into_single():
    # Discogs has no separate EP facet on its own artist pages -- 'ep'
    # should no longer produce a distinct album_type.
    album = Album.from_discogs_release({
        'id': 3, 'title': 'Fan Club EP', 'format': 'CD, EP, Club, Ltd',
    })
    assert album.album_type == 'single'


def test_explicit_single_format_unaffected():
    album = Album.from_discogs_release({
        'id': 4, 'title': 'El Viento', 'format': '7", Single',
    })
    assert album.album_type == 'single'


def test_plain_album_format_unaffected():
    album = Album.from_discogs_release({
        'id': 5, 'title': 'Majestic', 'format': 'Vinyl, LP, Album',
    })
    assert album.album_type == 'album'


def test_track_count_fallback_collapsed_to_single_six_track_cutoff():
    # No type keyword at all -- falls back to track-count guessing. The old
    # two-tier fallback (<=3 -> single, <=6 -> ep) is now one <=6 -> single
    # check, since both tiers fed the same "no Discogs signal" logic for
    # what's now a single output value.
    album = Album.from_discogs_release({
        'id': 6, 'title': 'No Format Data', 'tracklist': [{'title': f't{i}'} for i in range(5)],
    })
    assert album.album_type == 'single'


def test_track_count_fallback_above_six_defaults_to_album():
    album = Album.from_discogs_release({
        'id': 7, 'title': 'No Format Data Long', 'tracklist': [{'title': f't{i}'} for i in range(10)],
    })
    assert album.album_type == 'album'


def test_promo_is_not_an_exclusion_signal_still_classifies_by_type():
    # Promo is a modifier, not a type -- a Comp+Promo release must still
    # classify as compilation, not fall through or get excluded.
    album = Album.from_discogs_release({
        'id': 8, 'title': "X'mas Campaign '91", 'format': 'CD, Comp, Promo',
    })
    assert album.album_type == 'compilation'


# ---------------------------------------------------------------------------
# Change 2 -- _is_non_audio_discogs_release, the per-item whitelist
# ---------------------------------------------------------------------------

def test_pure_video_release_is_excluded():
    assert _is_non_audio_discogs_release({'format': 'Blu-ray'}) is True


def test_dvd_v_release_is_excluded():
    assert _is_non_audio_discogs_release({'format': 'DVD-V, NTSC'}) is True


def test_laserdisc_with_own_12_inch_disc_size_is_still_excluded():
    # The exact collision that broke the naive whitelist (v2): a flat token
    # scan misread this Laserdisc's OWN 12" disc size as "a vinyl record is
    # also present" and wrongly un-excluded it.
    assert _is_non_audio_discogs_release({'format': 'Laserdisc, 12", NTSC'}) is True


def test_cdv_release_is_excluded():
    assert _is_non_audio_discogs_release({'format': 'CDV, 5", NTSC'}) is True


def test_umatic_promo_tape_is_excluded_even_with_single_type_marker():
    # The exact release that broke the blacklist (v1): Michael Jackson's
    # "You Are Not Alone" on Umatic professional broadcast tape. It also
    # carries a 'Single' type marker in the same string -- medium must
    # disqualify it regardless of what type tag rides along with it.
    assert _is_non_audio_discogs_release({'format': 'Umatic, Single, NTSC'}) is True


def test_betacam_sp_is_excluded():
    assert _is_non_audio_discogs_release({'format': 'Betacam SP, Advance, PAL'}) is True


def test_vhs_with_single_type_marker_is_still_excluded():
    assert _is_non_audio_discogs_release({'format': 'VHS, Single, Promo, PAL'}) is True


def test_bundled_bluray_plus_cd_is_kept_not_excluded():
    # "Blu-ray + 2xCD" is TWO distinct bundled items -- a video disc and a
    # real 2-CD audio set. Per-item checking correctly recognizes the
    # audio part instead of blacklisting on "Blu-ray" appearing anywhere.
    assert _is_non_audio_discogs_release({'format': 'Blu-ray + 2xCD'}) is False


def test_mixed_cd_album_plus_dvd_bonus_is_kept_not_excluded():
    # A real audio CD with a bonus DVD -- a naive blacklist wrongly drops
    # ANY release mentioning "DVD" anywhere, even this one.
    assert _is_non_audio_discogs_release({'format': 'CD, Album + DVD'}) is False


def test_dvd_d_data_disc_of_mp3s_resolves_to_audio():
    # A DVD-format data disc actually containing digital audio files.
    assert _is_non_audio_discogs_release({'format': 'DVD-D, MP3, Compilation'}) is False


def test_plain_vinyl_release_is_kept():
    assert _is_non_audio_discogs_release({'format': 'Vinyl, LP, Album'}) is False


def test_vinyl_in_box_with_no_video_marker_is_kept():
    assert _is_non_audio_discogs_release({'format': 'Box, Vinyl, LP, Ltd'}) is False


def test_master_with_no_format_data_is_not_excluded():
    # Every master on the artist-releases endpoint has empty format data --
    # unknown must not be treated as confirmed non-audio.
    assert _is_non_audio_discogs_release({'type': 'master', 'title': 'Some Master'}) is False


def test_empty_format_is_not_excluded():
    assert _is_non_audio_discogs_release({}) is False


def test_completely_unrecognized_format_is_not_guessed_at():
    # No recognized token on either side -- don't guess.
    assert _is_non_audio_discogs_release({'format': 'Zorkulon Disc'}) is False
