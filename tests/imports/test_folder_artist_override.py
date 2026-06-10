"""Folder-artist override is opt-in and never clobbers an identified artist.

Background
----------

The auto-import "parent folder artist override" used to fire unconditionally:
whenever the Staging path had >=2 levels and the top folder wasn't a category
word, it replaced the (already metadata-identified) artist with the top folder
name. A user who staged a mixed pile of singles under one container folder
named ``soulsync`` therefore got the album-artist of *every* file forced to
"soulsync" — even when the track was confidently resolved to a real artist
with a Spotify/MusicBrainz id. Navidrome groups by album-artist, so 65 singles
collapsed under a bogus "soulsync" artist.

``resolve_folder_artist`` is the extracted, pure decision. It must:
- return ``None`` (i.e. keep the identified artist) when the feature is OFF,
  regardless of folder structure — this is the regression guard;
- only when explicitly enabled, reproduce the original folder-derived artist.
"""

from core.imports.folder_artist import resolve_folder_artist


# --- OFF by default: never override (the bug fix) ---------------------------

def test_disabled_keeps_identified_artist_even_with_artist_album_structure():
    # The 'soulsync' mass mis-file: identified artist is real, folder is a
    # generic container. With the feature off it must NOT be overridden.
    assert resolve_folder_artist(
        "soulsync/Bunny Girl/01 - Bunny Girl.flac",
        identified_artist="1nonly, Ciscaux",
        enabled=False,
    ) is None


def test_disabled_returns_none_for_clean_artist_album_path():
    assert resolve_folder_artist(
        "AC+DC/Back In Black/01 - Hells Bells.flac",
        identified_artist="AC/DC",
        enabled=False,
    ) is None


# --- Enabled: original behaviour is available on request --------------------

def test_enabled_uses_top_folder_as_artist_when_it_differs():
    assert resolve_folder_artist(
        "soulsync/Bunny Girl/01 - Bunny Girl.flac",
        identified_artist="1nonly, Ciscaux",
        enabled=True,
    ) == "soulsync"


def test_enabled_skips_category_folder_and_uses_artist_above_it():
    assert resolve_folder_artist(
        "Pink Floyd/Albums/The Wall/01 - In the Flesh.flac",
        identified_artist="Some DJ",
        enabled=True,
    ) == "Pink Floyd"


def test_enabled_flat_file_has_no_folder_artist():
    assert resolve_folder_artist(
        "01 - Bitch Lasagna.flac",
        identified_artist="PewDiePie",
        enabled=True,
    ) is None


def test_enabled_no_override_when_folder_matches_identified():
    # Folder already equals the identified artist (case-insensitive) -> no-op.
    assert resolve_folder_artist(
        "AC_DC/Back In Black/01 - Hells Bells.flac",
        identified_artist="ac_dc",
        enabled=True,
    ) is None


def test_enabled_top_level_category_word_is_not_an_artist():
    # 'singles/Track/file' — top folder is a category, not an artist.
    assert resolve_folder_artist(
        "singles/Headache/01 - Headache.flac",
        identified_artist="Asal",
        enabled=True,
    ) is None
