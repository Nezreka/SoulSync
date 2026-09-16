"""The finder has to read a source's dispatch key, not guess at it.

A streaming source does not hand over a path. It encodes what its worker needs
to start the download, and the finder wants only the human-readable tail so it
can match the file that actually landed on disk.

Most sources encode two parts, ``id||name``. SoundCloud encodes three,
``id||url||name``, because its worker needs the permalink. The finder read
everything after the FIRST separator, so for a SoundCloud track it went looking
for a file called ``https://soundcloud.com/...||SSIO - Alles oder Nix``.

Nothing is called that. The file was never found, so post-processing never ran:
no tags were written and the file stayed in the completed folder, while the
download reported success (#1239).
"""

from __future__ import annotations

import pytest

from core.downloads.file_finder import _encoded_display_name, _extract_basename

SOUNDCLOUD = ("2093480||https://soundcloud.com/probst-jakob/ssio-alles-oder-nix-x-acdc"
              "||SSIO - Alles oder Nix x ACDC")


def test_a_soundcloud_key_yields_the_name_that_is_on_disk():
    assert _encoded_display_name(SOUNDCLOUD) == "SSIO - Alles oder Nix x ACDC"


def test_the_url_never_reaches_the_search_target():
    """The whole failure: a url in the middle became part of what we looked for."""
    assert "soundcloud.com" not in (_encoded_display_name(SOUNDCLOUD) or "")
    assert "||" not in (_encoded_display_name(SOUNDCLOUD) or "")


@pytest.mark.parametrize("key,expected", [
    ("dQw4w9WgXcQ||Rick Astley - Never Gonna Give You Up",
     "Rick Astley - Never Gonna Give You Up"),          # youtube
    ("434945950||Aphex Twin - Xtal", "Aphex Twin - Xtal"),   # tidal
    ("12345||Artist - Title", "Artist - Title"),             # qobuz / hifi / deezer
])
def test_two_part_keys_read_exactly_as_before(key, expected):
    assert _encoded_display_name(key) == expected


def test_a_title_containing_a_slash_is_not_a_path(   ):
    """#835. A '/' in a title is part of the title, not a directory.

    Splitting on it truncated the target to 'T:T' and quarantined good files.
    """
    assert _encoded_display_name("abc||YouSeeBIGGIRL/T:T") == "YouSeeBIGGIRL/T:T"


def test_a_title_containing_pipes_is_left_alone():
    """The three-part shape is recognised by its url, not by counting parts.

    Counting would make 'abc||A || B' look like a soundcloud key and truncate
    the title to 'B'.
    """
    assert _encoded_display_name("abc||A || B") == "A || B"


@pytest.mark.parametrize("not_encoded", [
    "music/Artist/Album/01 - Song.flac",
    "01 - Song.flac",
    "",
    None,
])
def test_a_real_remote_path_is_not_an_encoded_key(not_encoded):
    """None sends the caller down the path-handling branch, as before."""
    assert _encoded_display_name(not_encoded) is None


def test_a_middle_part_that_is_not_a_url_is_not_treated_as_soundcloud():
    assert _encoded_display_name("id||notaurl||name") == "notaurl||name"


def test_the_helper_and_the_live_path_agree():
    """_extract_basename used its own copy of this parse and disagreed.

    Two readings of one format is how the finder ended up looking for a url.
    """
    assert _extract_basename(SOUNDCLOUD) == _encoded_display_name(SOUNDCLOUD)
    assert _extract_basename("abc||Title") == _encoded_display_name("abc||Title")
