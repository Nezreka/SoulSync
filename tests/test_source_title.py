"""Extreme test battery for source-track normalization (#768).

YouTube/streaming sources carry "Artist - Song" titles and "Official Artist"/
"Artist - Topic"/"ArtistVEVO" artist names; the library has clean metadata, so
matching fails and tracks are reported missing. These helpers strip the
decoration. The batteries below pin both the positives (must clean) and the
negatives (must NOT mangle real titles/artists).
"""

from __future__ import annotations

import pytest

from core.text.source_title import (
    canonical_source_track,
    clean_source_artist,
    restated_title,
    strip_artist_prefix,
)


# ── clean_source_artist ───────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Official Arctic Monkeys", "Arctic Monkeys"),
    ("The Official Weeknd", "Weeknd"),
    ("Arctic Monkeys - Topic", "Arctic Monkeys"),
    ("Coldplay - Topic", "Coldplay"),
    ("ColdplayVEVO", "Coldplay"),
    ("Coldplay VEVO", "Coldplay"),
    ("EminemVEVO", "Eminem"),
    ("  Official Radiohead  ", "Radiohead"),
])
def test_clean_source_artist_strips_decoration(raw, expected):
    assert clean_source_artist(raw) == expected


@pytest.mark.parametrize("raw", [
    "Arctic Monkeys",            # already clean
    "Coldplay",
    "Twenty One Pilots",
    "Death",
    "U2",
    "AJR",                        # would be emptied by a naive vevo/official strip
    "",
])
def test_clean_source_artist_leaves_clean_names(raw):
    assert clean_source_artist(raw) == raw


def test_clean_source_artist_never_empties():
    # Pathological: artist that is ONLY decoration must not become "".
    assert clean_source_artist("VEVO") == "VEVO"
    assert clean_source_artist("Official ") == "Official"


# ── strip_artist_prefix ───────────────────────────────────────────────────

@pytest.mark.parametrize("title,artist,expected", [
    ("Arctic Monkeys - Do I Wanna Know?", "Arctic Monkeys", "Do I Wanna Know?"),
    ("Death - Pull the Plug", "Death", "Pull the Plug"),
    ("Coldplay – Yellow", "Coldplay", "Yellow"),          # en dash
    ("Coldplay — Yellow", "Coldplay", "Yellow"),          # em dash
    ("Eminem: Lose Yourself", "Eminem", "Lose Yourself"),  # colon
    ("Daft Punk | Get Lucky", "Daft Punk", "Get Lucky"),   # pipe
    ("ARCTIC MONKEYS - 505", "arctic monkeys", "505"),     # case-fold
])
def test_strip_artist_prefix_strips_when_prefix_is_artist(title, artist, expected):
    assert strip_artist_prefix(title, artist) == expected


@pytest.mark.parametrize("title,artist", [
    ("Marvin Gaye", "Charlie Puth"),                 # title is not "artist - ..."
    ("Do I Wanna Know?", "Arctic Monkeys"),          # already clean
    ("Self-Titled", "Whoever"),                      # hyphen w/o spaces — not a sep
    ("Jay-Z Anthem", "Somebody"),                    # hyphen inside a word
    ("Song - Live", "Coldplay"),                     # prefix "Song" != artist
    ("Stay With Me", "Sam Smith"),
    ("", "Arctic Monkeys"),
    ("Arctic Monkeys -", "Arctic Monkeys"),          # nothing after sep -> unchanged
])
def test_strip_artist_prefix_leaves_others_untouched(title, artist):
    assert strip_artist_prefix(title, artist) == title


def test_strip_only_first_separator():
    # "Artist - Song - Remix" -> strip only the leading artist segment.
    assert strip_artist_prefix("Gorillaz - Feel Good Inc - Remix", "Gorillaz") == "Feel Good Inc - Remix"


# ── canonical_source_track (combined) ─────────────────────────────────────

def test_canonical_handles_youtube_channel_and_prefix():
    # The reported case: channel-name artist + "Artist - Title" title.
    title, artist = canonical_source_track(
        "Arctic Monkeys - Do I Wanna Know?", "Official Arctic Monkeys",
    )
    assert title == "Do I Wanna Know?"
    assert artist == "Arctic Monkeys"


def test_canonical_strips_prefix_using_raw_artist_when_clean_differs():
    # Title prefixed with the channel-style raw artist itself.
    title, artist = canonical_source_track(
        "Official Arctic Monkeys - 505", "Official Arctic Monkeys",
    )
    # cleaned artist is "Arctic Monkeys"; raw prefix "Official Arctic Monkeys"
    # also stripped via the raw-artist fallback.
    assert title == "505"
    assert artist == "Arctic Monkeys"


def test_canonical_noop_on_clean_input():
    assert canonical_source_track("Yellow", "Coldplay") == ("Yellow", "Coldplay")


# ── restated_title ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    # YouTube Music appends a transliteration to the localized title. Providers
    # index the transliteration, so it has to become a match candidate.
    ("狂乱 Hey Kids!! - Kyouran Hey Kids!!", "Kyouran Hey Kids!!"),
    ("花に亡霊 - Ghost in a Flower", "Ghost in a Flower"),
    ("すずめ - Suzume (feat. Toaka)", "Suzume (feat. Toaka)"),
    ("紅蓮華 - Gurenge", "Gurenge"),
    ("暁の鎮魂歌 - Akatsuki no Requiem", "Akatsuki no Requiem"),
    # Latin half first is just as valid.
    ("Silhouette - シルエット", "Silhouette"),
    # Stated three times: original, translation, transliteration.
    ("烏 - Raven - Karasu - Raven", "Raven"),
    # Same title restated within one script, only the decoration differs.
    ("COLORS (Album Mix) - Colors (Ailu Mix)", "Colors (Ailu Mix)"),
    ("Rain (Long Ver.) - Rain (Long Version)", "Rain (Long Version)"),
    ("newsong (album ver.) - Newsong", "Newsong"),
    ("Sign -NARUTO Opening Mix- - Sign (Naruto Opening Mix)", "Sign (Naruto Opening Mix)"),
    ("forget-me-not -unknown mix- - forget-me-not (unknown mix)",
     "forget-me-not (unknown mix)"),
    # Devanagari restated in Latin — same shape as the CJK/Cyrillic/etc cases.
    ("फूल - Flower", "Flower"),
])
def test_restated_title_returns_the_latin_statement(raw, expected):
    assert restated_title(raw) == expected


@pytest.mark.parametrize("raw", [
    # "Title - Band" is not a restatement; splitting it loses the title.
    "Sketchy - Molly And The Zombies",
    "Last Kiss (cover) - Brian Fallon",
    "Ren - Bittersweet symphony (The Verve retake) - Lyrics",
    # A genuine subtitle, not the same title twice.
    "believe - Fate/stay night Unlimited Blade Works ED",
    "KAZE - Run With You",
    # Both halves non-Latin: nothing to prefer between them.
    "金木犀 - 水野あつ",
    # A live-recording credit is a genuine subtitle, not a restatement — even
    # though the title before it is a single non-Latin character.
    "夜 - Live at Budokan",
    # No separator at all.
    "だから僕は音楽を辞めた",
    "Yellow",
    # Hyphens without surrounding whitespace must never split.
    "Jay-Z",
    "Self-Titled",
    "",
])
def test_restated_title_leaves_everything_else_alone(raw):
    assert restated_title(raw) is None


def test_canonical_source_track_uses_the_restatement():
    title, artist = canonical_source_track("紅蓮華 - Gurenge", "LiSA")
    assert (title, artist) == ("Gurenge", "LiSA")


def test_artist_prefix_still_wins_over_restatement():
    # An "Artist - Title" prefix is the stronger signal; the restatement path
    # only runs when no prefix was stripped.
    title, artist = canonical_source_track("Gorillaz - Feel Good Inc", "Gorillaz")
    assert title == "Feel Good Inc"
