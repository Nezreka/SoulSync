"""Seam tests for YouTube playlist artist/title derivation (GitHub #863).

Flat playlist extraction gives sparse entries; the parser used to take the
artist straight from `uploader`, which on a playlist is the OWNER — so every
track came out as "Wing It" / "Unknown Artist". `derive_artist_and_title` picks
the best available signal instead. These pin the precedence + the
"never use the playlist owner" guarantee.
"""

from __future__ import annotations

from core.youtube_track_meta import derive_artist_and_title, is_music_youtube_url


def test_music_artists_field_wins():
    artist, title = derive_artist_and_title(
        {'title': 'Forgiven', 'artists': ['Within Temptation'], 'uploader': 'Wing It'})
    assert artist == 'Within Temptation'
    assert title == 'Forgiven'


def test_artist_field_used_when_no_artists_list():
    artist, title = derive_artist_and_title(
        {'title': 'Alive', 'artist': 'Empire of the Sun', 'uploader': 'Wing It'})
    assert artist == 'Empire of the Sun'
    assert title == 'Alive'


def test_topic_channel_is_the_artist():
    artist, title = derive_artist_and_title(
        {'title': 'Revolte', 'uploader': 'Paul Kalkbrenner - Topic'})
    assert artist == 'Paul Kalkbrenner'
    assert title == 'Revolte'


def test_artist_title_split_from_title():
    # The exact #863 log case — title carries "Artist - Track", uploader is the
    # playlist owner.
    artist, title = derive_artist_and_title(
        {'title': 'Paul Kalkbrenner - Revolte (Original Mix) [Bpitch]', 'uploader': 'Wing It'})
    assert artist == 'Paul Kalkbrenner'
    # Splits on the FIRST separator; the remainder keeps the qualifiers for the
    # title cleaner to strip downstream.
    assert title == 'Revolte (Original Mix) [Bpitch]'


def test_no_signal_returns_empty_artist_not_playlist_owner():
    # The unrecoverable case: plain title, uploader is the owner. Must NOT label
    # the track with the owner's channel (#863).
    artist, title = derive_artist_and_title(
        {'title': 'Forgiven', 'uploader': 'Wing It'})
    assert artist == ''
    assert title == 'Forgiven'


def test_hyphenated_name_without_spaces_not_split():
    # "Jean-Michel Jarre" has no spaced dash → not an Artist-Title split.
    artist, title = derive_artist_and_title({'title': 'Jean-Michel Jarre', 'uploader': 'Wing It'})
    assert artist == ''
    assert title == 'Jean-Michel Jarre'


def test_en_dash_separator_splits():
    artist, title = derive_artist_and_title({'title': 'Koven – Worlds Apart'})  # en dash
    assert artist == 'Koven'
    assert title == 'Worlds Apart'


def test_topic_beats_title_split_but_cleaner_handles_prefix():
    # Topic channel present AND title repeats "Artist - Title": topic wins for the
    # artist; the full title is returned for the downstream cleaner to de-prefix.
    artist, title = derive_artist_and_title(
        {'title': 'Paul Kalkbrenner - Revolte', 'uploader': 'Paul Kalkbrenner - Topic'})
    assert artist == 'Paul Kalkbrenner'
    assert title == 'Paul Kalkbrenner - Revolte'


def test_missing_title_is_safe():
    artist, title = derive_artist_and_title({'uploader': 'Wing It'})
    assert artist == ''
    assert title == 'Unknown Track'


def test_bad_input_is_safe():
    assert derive_artist_and_title(None) == ('', 'Unknown Track')
    assert derive_artist_and_title("not a dict") == ('', 'Unknown Track')


def test_empty_artists_list_falls_through():
    # An empty/blank artists list must not win — fall through to the title split.
    artist, title = derive_artist_and_title(
        {'title': 'Koven - Worlds Apart', 'artists': ['', None]})
    assert artist == 'Koven'
    assert title == 'Worlds Apart'


# ── music.youtube.com channel fallback ────────────────────────────────────
# On youtube.com the entry channel is the playlist OWNER (#863, pinned above).
# On music.youtube.com it is the TRACK's own channel, so the #863 rule was
# discarding a correct artist on every YT Music track. These pin the split.


def test_channel_not_used_as_artist_by_default():
    # Default stays the #863 behaviour: youtube.com callers pass nothing.
    artist, title = derive_artist_and_title({'title': 'Forgiven', 'channel': 'Wing It'})
    assert artist == ''
    assert title == 'Forgiven'


def test_channel_used_as_artist_when_allowed():
    # The real music.youtube.com shape — flat entry, no music fields, plain
    # per-track channel.
    artist, title = derive_artist_and_title(
        {'title': 'Example Track', 'channel': 'Example Artist'},
        allow_channel_artist=True)
    assert artist == 'Example Artist'
    assert title == 'Example Track'


def test_channel_fallback_is_last_and_never_overrides_a_better_signal():
    # Music fields still win, so enabling the fallback can only ADD artists.
    artist, _ = derive_artist_and_title(
        {'title': 'Forgiven', 'artists': ['Within Temptation'], 'channel': 'Some Label'},
        allow_channel_artist=True)
    assert artist == 'Within Temptation'
    # ...and so does an "Artist - Title" split.
    artist, title = derive_artist_and_title(
        {'title': 'Koven - Worlds Apart', 'channel': 'Monstercat'},
        allow_channel_artist=True)
    assert artist == 'Koven'
    assert title == 'Worlds Apart'


def test_topic_suffix_still_stripped_when_channel_fallback_on():
    # The Topic branch must win, otherwise the artist keeps the " - Topic".
    artist, _ = derive_artist_and_title(
        {'title': 'Revolte', 'uploader': 'Paul Kalkbrenner - Topic'},
        allow_channel_artist=True)
    assert artist == 'Paul Kalkbrenner'


def test_channel_fallback_with_no_channel_is_still_empty():
    artist, title = derive_artist_and_title({'title': 'Forgiven'}, allow_channel_artist=True)
    assert artist == ''
    assert title == 'Forgiven'


# ── URL host detection ────────────────────────────────────────────────────


def test_music_host_detected():
    assert is_music_youtube_url('https://music.youtube.com/playlist?list=PL123')
    assert is_music_youtube_url('HTTPS://MUSIC.YOUTUBE.COM/playlist?list=LM')


def test_plain_youtube_is_not_music():
    # The #863 host — the channel fallback must stay OFF here.
    assert not is_music_youtube_url('https://www.youtube.com/playlist?list=PL123')
    assert not is_music_youtube_url('https://youtube.com/playlist?list=PL123')
    assert not is_music_youtube_url('https://m.youtube.com/playlist?list=PL123')


def test_music_host_must_be_the_host_not_a_substring():
    # A youtube.com URL that merely mentions the music host (redirect param,
    # lookalike domain) must not switch the fallback on.
    assert not is_music_youtube_url(
        'https://www.youtube.com/playlist?list=PL1&next=music.youtube.com')
    assert not is_music_youtube_url('https://music.youtube.com.evil.test/playlist?list=PL1')


def test_bad_url_input_is_safe():
    for bad in (None, '', '   ', 123, {}, 'not a url'):
        assert not is_music_youtube_url(bad)


def test_strip_topic_suffix_helper():
    from core.youtube_track_meta import strip_topic_suffix
    assert strip_topic_suffix('Example Band - Topic') == 'Example Band'
    assert strip_topic_suffix('Fifth Artist - Topic') == 'Fifth Artist'
    assert strip_topic_suffix('Fourth Artist  -  TOPIC') == 'Fourth Artist'
    # No suffix — unchanged.
    assert strip_topic_suffix('Third Artist') == 'Third Artist'
    # Would strip to nothing: keep the original rather than invent an empty artist.
    assert strip_topic_suffix('- Topic') == '- Topic'
    assert strip_topic_suffix('') == ''
    assert strip_topic_suffix(None) == ''


def test_plural_creators_field_is_read():
    # yt-dlp emits `creators` (plural) on flat entries — always None today,
    # but only the plural key will carry the value if upstream populates it.
    # The singular-only lookup silently missed it (flagged in PR #1136).
    artist, title = derive_artist_and_title(
        {'title': 'Worlds Apart', 'creators': ['Koven', '']})
    assert artist == 'Koven'
    assert title == 'Worlds Apart'
    # artists (plural) still outranks creators when both are present.
    artist, _ = derive_artist_and_title(
        {'title': 'X', 'artists': ['Primary'], 'creators': ['Secondary']})
    assert artist == 'Primary'
