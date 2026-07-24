"""Music-video artist/title resolution (Boulder's 'bad boy edd' folder).

A fan-channel upload titled '"Weird Al" Yankovic - Fat Official Music Video'
filed under the UPLOADER's channel folder because (1) the suffix stripper
only handled parenthesized "(Official Music Video)" so the search query kept
the noise, and (2) the 'Artist - Title' parse fallback lived inside
``if results:`` — an EMPTY metadata result (the common case, e.g. Spotify's
premium-wall 403 returns []) skipped it entirely, leaving the channel name
as the artist. The parse now covers every unmatched path, and the channel is
strictly the last resort for titles with no separator.

Pure-function tests + a source pin on the handler's structure. No network.
"""

from __future__ import annotations

import inspect

import web_server
from web_server import _clean_music_video_title, _parse_music_video_artist_title


# ── suffix cleaning ─────────────────────────────────────────────────────────

def test_bare_official_music_video_suffix_is_stripped():
    assert _clean_music_video_title(
        '"Weird Al" Yankovic - Fat Official Music Video'
    ) == '"Weird Al" Yankovic - Fat'


def test_parenthesized_suffixes_still_stripped():
    assert _clean_music_video_title('Muse - Uprising (Official Music Video)') == 'Muse - Uprising'
    assert _clean_music_video_title('Muse - Uprising [Official Audio]') == 'Muse - Uprising'


def test_bare_suffix_variants():
    assert _clean_music_video_title('Artist - Song Official Video') == 'Artist - Song'
    assert _clean_music_video_title('Artist - Song Official Lyric Video') == 'Artist - Song'
    assert _clean_music_video_title('MJ Thriller Music Video') == 'MJ Thriller'
    assert _clean_music_video_title('Artist - Song | Official Video') == 'Artist - Song'


def test_real_titles_are_never_eaten():
    # Songs genuinely ending in words the stripper must not treat as noise.
    assert _clean_music_video_title('Lana Del Rey - Video Games') == 'Lana Del Rey - Video Games'
    assert _clean_music_video_title('India.Arie - Video') == 'India.Arie - Video'
    assert _clean_music_video_title('Radiohead - Videotape') == 'Radiohead - Videotape'


# ── artist/title parsing ────────────────────────────────────────────────────

def test_fan_upload_parses_the_real_artist_not_the_channel():
    artist, title = _parse_music_video_artist_title(
        '"Weird Al" Yankovic - Fat Official Music Video', 'Bad Boy Edd')
    assert artist == '"Weird Al" Yankovic'
    assert title == 'Fat'


def test_en_dash_separator_parses_too():
    artist, title = _parse_music_video_artist_title(
        'Daft Punk – Around the World (Official Video)', 'randomfan42')
    assert artist == 'Daft Punk'
    assert title == 'Around the World'


def test_channel_is_the_last_resort_only_when_no_separator():
    artist, title = _parse_music_video_artist_title(
        'Thriller Official Music Video', 'Some Channel')
    assert artist == 'Some Channel'
    assert title == 'Thriller'


# ── handler structure pin ───────────────────────────────────────────────────

def test_parse_fallback_covers_the_empty_results_path():
    """The old code nested the parse under `if results:` — zero results (the
    Spotify-403 shape) skipped it and the channel name won. The fallback must
    now be an unconditional `if not matched:` using the shared parser."""
    src = inspect.getsource(web_server.download_music_video)
    assert 'if not matched:' in src
    assert '_parse_music_video_artist_title(raw_title, raw_channel)' in src
    assert '_clean_music_video_title(raw_title)' in src
    # A confident match must also require a non-empty artist list — the
    # channel name must never ride in through an artist-less match.
    assert 'best_score >= 0.5 and best.artists' in src
