"""#1129 — a leading dot in an album/track/movie title must not produce a
hidden directory.

Reported against "...Baby One More Time (Digital Deluxe Version) (1999)": the
sanitizers trimmed TRAILING dots (a Windows requirement) but left LEADING ones,
so on Linux the album folder became a dotfile — invisible to `ls`, and skipped
outright by Plex/Jellyfin/Navidrome, which is why the user's album silently
never appeared in their library.

Titles that genuinely start with dots are common enough to matter:
"...And Justice for All", "...Baby One More Time", "…Like Clockwork".
"""

from __future__ import annotations

import pytest

from core.imports.paths import sanitize_filename
from core.video.library_paths import sanitize as video_sanitize

# The music-side and video-side sanitizers are separate implementations that
# must agree on this rule.
SANITIZERS = [
    pytest.param(sanitize_filename, id="music"),
    pytest.param(video_sanitize, id="video"),
]

LEADING_DOT_TITLES = [
    "...Baby One More Time (Digital Deluxe Version)",
    "...And Justice for All",
    ".hidden",
    ". Leading dot space",
    "..double",
]


@pytest.mark.parametrize("sanitizer", SANITIZERS)
@pytest.mark.parametrize("title", LEADING_DOT_TITLES)
def test_output_is_never_hidden(sanitizer, title):
    out = sanitizer(title)
    assert not out.startswith("."), (
        f"{sanitizer.__module__}.{sanitizer.__name__}({title!r}) -> {out!r} "
        f"starts with a dot, which is a hidden entry on Unix and invisible to "
        f"every media server (#1129)."
    )


@pytest.mark.parametrize("sanitizer", SANITIZERS)
def test_the_reported_album_keeps_its_readable_name(sanitizer):
    """The dots go, the actual title survives — this is what lands on disk."""
    assert sanitizer("...Baby One More Time (Digital Deluxe Version)") == (
        "Baby One More Time (Digital Deluxe Version)"
    )


@pytest.mark.parametrize("sanitizer", SANITIZERS)
def test_trailing_dots_still_trimmed(sanitizer):
    """The Windows rule that was already there must not regress."""
    assert sanitizer("trailing dots...") == "trailing dots"
    # "Fred again.." is the real-world artist the trailing rule was added for.
    assert sanitizer("Fred again..") == "Fred again"


@pytest.mark.parametrize("sanitizer", SANITIZERS)
def test_interior_dots_are_untouched(sanitizer):
    """Only the ends are trimmed — dots inside a title carry meaning."""
    assert sanitizer("Mr. Bungle") == "Mr. Bungle"
    assert sanitizer("Panic! at the Disco - A.O.K.") == "Panic! at the Disco - A.O.K"


def test_music_all_dots_falls_back_rather_than_emptying():
    """A title that is nothing but dots must still yield a usable component."""
    assert sanitize_filename("...") == "_"
    assert sanitize_filename(". . .") == "_"


@pytest.mark.parametrize("sanitizer", SANITIZERS)
def test_ordinary_names_unchanged(sanitizer):
    assert sanitizer("Normal Album") == "Normal Album"
    assert sanitizer("Beck - Guero") == "Beck - Guero"
