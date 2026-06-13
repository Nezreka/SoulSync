"""Seam tests for YouTube playlist artist/title derivation (GitHub #863).

Flat playlist extraction gives sparse entries; the parser used to take the
artist straight from `uploader`, which on a playlist is the OWNER — so every
track came out as "Wing It" / "Unknown Artist". `derive_artist_and_title` picks
the best available signal instead. These pin the precedence + the
"never use the playlist owner" guarantee.
"""

from __future__ import annotations

from core.youtube_track_meta import derive_artist_and_title


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
