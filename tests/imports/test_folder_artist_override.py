"""Folder-artist override can be disabled to keep an identified artist.

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

``resolve_folder_artist`` is the extracted, pure decision. It must keep the
identified artist when the feature is OFF, and reproduce the original
folder-derived artist when enabled.
"""

from core.imports.folder_artist import resolve_folder_artist


# --- Disabled: never override -----------------------------------------------

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


# --- Enabled: original behaviour --------------------------------------------

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


# --- the folder the album sits in, not the top of staging (Tostadaman) ------
# a torrent client's completed folder mounted inside staging named every
# artist "qbittorrent": /MUSIC/qbittorrent/qbittorrent - TrackName.


def test_client_mount_above_artist_album_keeps_the_real_artist():
    # candidate paths are album folders; the artist folder sits above it
    assert resolve_folder_artist(
        "qbittorrent/lidarr-done/Linkin Park/Meteora",
        identified_artist="Linkin Park",
        enabled=True,
    ) is None


def test_client_mount_still_lets_the_real_artist_folder_win():
    # the legacy point of the feature: bad tags, good Artist/Album folders
    assert resolve_folder_artist(
        "qbittorrent/lidarr-done/Linkin Park/Meteora",
        identified_artist="Linkin Prk",
        enabled=True,
    ) == "Linkin Park"


def test_album_directly_in_a_client_folder_is_not_an_artist():
    assert resolve_folder_artist(
        "qbittorrent/Meteora",
        identified_artist="Linkin Park",
        enabled=True,
    ) is None
    assert resolve_folder_artist(
        "transmission/complete/Meteora",
        identified_artist="Linkin Park",
        enabled=True,
    ) is None


def test_disc_folders_belong_to_their_album():
    assert resolve_folder_artist(
        "Pink Floyd/The Wall/CD1",
        identified_artist="Some DJ",
        enabled=True,
    ) == "Pink Floyd"
    assert resolve_folder_artist(
        "Pink Floyd/The Wall/Disc 2/01 - Hey You.flac",
        identified_artist="Some DJ",
        enabled=True,
    ) == "Pink Floyd"


def test_album_folder_at_the_staging_root_has_no_folder_artist():
    assert resolve_folder_artist("Meteora", identified_artist="Linkin Park", enabled=True) is None


# --- singular release-type folders (discord, SeadogsBooty) ---------------------

def test_singular_release_type_folder_is_not_the_artist():
    # soulsync's own Artist/$albumtype/[year] Release layout, staged back in.
    # "Album" used to win, filing everything under mp3/Album/Album/...
    for kind in ("Album", "EP", "Single", "Compilation", "Live"):
        assert resolve_folder_artist(
            f"A Skylit Drive/{kind}/[2013] Rise/01 - Intro.mp3",
            identified_artist="Some Tagger Typo",
            enabled=True,
        ) == "A Skylit Drive", kind


def test_singular_release_type_folder_keeps_a_matching_identified_artist():
    assert resolve_folder_artist(
        "A Skylit Drive/Album/[2013] Rise/01 - Intro.mp3",
        identified_artist="A Skylit Drive",
        enabled=True,
    ) is None


# --- #2045: a folder soulsync named itself is not another artist ------------

REPORTED = "$uicideboy$ (American hip-hop duo)/Album/$uicideboy$ - Long Term Effects of SUFFERING (2021)"


def test_a_disambiguation_on_the_folder_is_not_a_new_artist():
    """$albumartist ($albumartistdisambiguation) named the folder, the folder
    came back through staging, and the Album Artist tag became
    "$uicideboy$ (American hip-hop duo)". plex could not match it."""
    assert resolve_folder_artist(REPORTED, "$uicideboy$", enabled=True) is None
    assert resolve_folder_artist("Air [FR]/Moon Safari", "Air", enabled=True) is None
    assert resolve_folder_artist("Nirvana (UK) [rock]/Album/Songlife", "nirvana", enabled=True) is None


def test_the_filesystem_safe_name_is_the_same_artist():
    assert resolve_folder_artist("AC_DC/Back in Black", "AC/DC", enabled=True) is None
    assert resolve_folder_artist("Panic! at the Disco/Pray", "Panic! at the Disco.", enabled=True) is None


def test_a_name_the_template_renders_is_the_same_artist():
    assert resolve_folder_artist("Air - FR/Moon Safari", "Air", enabled=True) == "Air - FR"
    assert resolve_folder_artist("Air - FR/Moon Safari", "Air", enabled=True,
                                 same_as={"Air - FR"}) is None


def test_a_different_artist_still_overrides():
    # the folder really does say someone else
    assert resolve_folder_artist("Nirvana UK/Songlife", "Nirvana", enabled=True) == "Nirvana UK"
    assert resolve_folder_artist("Airbourne (AU)/Runnin Wild", "Air", enabled=True) == "Airbourne (AU)"
    assert resolve_folder_artist("(American hip-hop duo)/Album", "", enabled=True) == "(American hip-hop duo)"


def test_artist_folder_names_reads_the_users_templates(monkeypatch):
    from core.imports import artist_facts as facts_module
    from core.imports.paths import artist_folder_names, preview_settings

    monkeypatch.setattr(facts_module, "artist_facts",
                        lambda name, **_kw: {"disambiguation": "American hip-hop duo", "country": "US"})
    templates = {
        "album_path": "$albumartist ($albumartistdisambiguation)/$albumtype/$albumartist - $album ($year)/$disc-$track - $title",
        "single_path": "${albumartist} - $albumartistcountry/$title/$title",
        "playlist_path": "$playlist/$artist - $title",
    }
    with preview_settings({"file_organization.templates": templates}):
        names = artist_folder_names("$uicideboy$")
    assert names == {"$uicideboy$", "$uicideboy$ (American hip-hop duo)", "$uicideboy$ - US"}
    with preview_settings({"file_organization.templates": templates}):
        assert resolve_folder_artist("$uicideboy$ - US/Album/x", "$uicideboy$", True,
                                     same_as=artist_folder_names("$uicideboy$")) is None
    assert artist_folder_names("") == set()
