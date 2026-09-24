"""Music-video artist/title resolution (Boulder's 'bad boy edd' folder).

A fan-channel upload titled '"Weird Al" Yankovic - Fat Official Music Video'
filed under the UPLOADER's channel folder because (1) the suffix stripper
only handled parenthesized "(Official Music Video)" so the search query kept
the noise, and (2) the 'Artist - Title' parse fallback lived inside
``if results:`` — an EMPTY metadata result (the common case, e.g. Spotify's
premium-wall 403 returns []) skipped it entirely, leaving the channel name
as the artist. The parse now covers every unmatched path, and the channel is
strictly the last resort for titles with no separator.

Pure-function tests. No network. the job itself (the empty-results fallback
included) is driven in tests/test_music_video_job.py.
"""

from __future__ import annotations

from core.downloads.music_video import (
    clean_channel,
    parse_artist_title as _parse_music_video_artist_title,
    clean_title as _clean_music_video_title,
    render_path,
)


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


# ── channel names and versions ──────────────────────────────────────────────

def test_channel_noise_is_cleaned_for_the_fallback_artist():
    assert clean_channel('RadioheadVEVO') == 'Radiohead'
    assert clean_channel('Radiohead - Topic') == 'Radiohead'
    assert clean_channel('Muse Official') == 'Muse'
    # a channel that is ALL noise keeps its name rather than going blank
    assert clean_channel('VEVO') == 'VEVO'


def test_a_topic_upload_files_under_the_artist_not_the_topic_channel():
    artist, title = _parse_music_video_artist_title('Creep', 'Radiohead - Topic')
    assert (artist, title) == ('Radiohead', 'Creep')


def test_a_version_bracket_survives_the_parse():
    # dropping "(Live at Glastonbury)" filed a live cut as the studio song
    _, title = _parse_music_video_artist_title('Radiohead - Creep (Live at Glastonbury 1997)', 'x')
    assert title == 'Creep (Live at Glastonbury 1997)'
    _, title = _parse_music_video_artist_title('Radiohead - Creep (Acoustic Cover)', 'Jane')
    assert title == 'Creep (Acoustic Cover)'


def test_noise_brackets_still_go():
    _, title = _parse_music_video_artist_title('Daft Punk - One More Time (feat. Romanthony) [HD]', 'x')
    assert title == 'One More Time'


# ── the path ────────────────────────────────────────────────────────────────

def _letter(name):
    return name[:1].upper()


def test_default_template_files_artist_slash_title_video():
    assert render_path('', 'Radiohead', 'Creep', '', _letter) == ('Radiohead', 'Creep-video')


def test_a_missing_year_leaves_no_empty_brackets_or_dangling_dash():
    assert render_path('$artist/$title ($year)', 'Muse', 'Uprising', '', _letter) == ('Muse', 'Uprising')
    assert render_path('$artist/$year - $title', 'Muse', 'Uprising', '', _letter) == ('Muse', 'Uprising')
    assert render_path('$artist/$title ($year)', 'Muse', 'Uprising', '2009', _letter) == ('Muse', 'Uprising (2009)')


def test_path_characters_in_names_cannot_make_folders():
    folder, stem = render_path('$artistletter/$artist/$title-video', 'AC/DC', 'T.N.T.', '', _letter)
    assert folder == 'A/AC_DC'
    assert stem == 'T.N.T-video'
