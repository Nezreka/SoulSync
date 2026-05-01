"""Regression tests for routing singles/EPs through the album_path template.

Discord-reported scenario (winecountrygames + fresh.dumbledore):
"Import only makes Albums folder no singles or eps". Users with a
``${albumtype}s/$albumartist/...`` ``album_path`` template saw an
"Albums" folder fill up correctly, but singles never produced a
"Singles" folder because the staging/auto-import code routed them
through the ``single_path`` template (which doesn't honour
``$albumtype``).

The detection in ``build_import_album_info`` previously required
``total_tracks > 1`` AND ``album_name != track_title``. Singles fail
both — they have one track and the release is usually named after
the song.

Fix: when the metadata source explicitly identifies the release
type ("single" / "ep" / "compilation"), trust it and route through
``album_path`` so the user's ``$albumtype``-aware template runs.
``"album"`` is excluded — it's the default fallback for missing
metadata, so triggering on it would change behaviour for
single-track downloads that happen to have no source data.
"""

import pytest

from core.imports.context import build_import_album_info, normalize_import_context


def _make_context(album_type: str, total_tracks: int, album_name: str, track_name: str):
    return normalize_import_context(
        {
            "source": "spotify",
            "artist": {"name": "Test Artist"},
            "album": {
                "name": album_name,
                "release_date": "2024-01-01",
                "total_tracks": total_tracks,
                "album_type": album_type,
            },
            "track_info": {
                "name": track_name,
                "track_number": 1,
                "disc_number": 1,
                "artists": [{"name": "Test Artist"}],
            },
            "original_search_result": {
                "title": track_name,
                "album": album_name,
                "clean_title": track_name,
                "clean_album": album_name,
                "clean_artist": "Test Artist",
            },
        }
    )


# ---------------------------------------------------------------------------
# The reported scenarios
# ---------------------------------------------------------------------------


def test_spotify_single_with_same_name_as_track_routes_through_album_path() -> None:
    """The reported case: a single named after its only track. Used to
    fail every condition and fall through to single_path; must now
    surface as ``is_album=True`` so the album_path template applies."""
    context = _make_context(
        album_type="single",
        total_tracks=1,
        album_name="Hello",
        track_name="Hello",
    )
    info = build_import_album_info(context)
    assert info["is_album"] is True


def test_spotify_single_with_different_album_name_also_routes_through_album_path() -> None:
    context = _make_context(
        album_type="single",
        total_tracks=1,
        album_name="Hello (Single Version)",
        track_name="Hello",
    )
    info = build_import_album_info(context)
    assert info["is_album"] is True


def test_explicit_ep_routes_through_album_path() -> None:
    """EPs already passed the multi-track check, but pin the
    explicit-type path so a 1-track EP (rare but possible) doesn't
    silently fall through if the source labels it as such."""
    context = _make_context(
        album_type="ep",
        total_tracks=1,
        album_name="Tiny EP",
        track_name="Tiny EP",
    )
    info = build_import_album_info(context)
    assert info["is_album"] is True


def test_explicit_compilation_routes_through_album_path() -> None:
    context = _make_context(
        album_type="compilation",
        total_tracks=1,
        album_name="Greatest Hits Sampler",
        track_name="Greatest Hits Sampler",
    )
    info = build_import_album_info(context)
    assert info["is_album"] is True


# ---------------------------------------------------------------------------
# Regression guards
# ---------------------------------------------------------------------------


def test_normal_album_still_detected_as_album() -> None:
    """Multi-track albums must keep being detected — the original
    heuristic is preserved as a fallback when album_type is generic."""
    context = _make_context(
        album_type="album",
        total_tracks=12,
        album_name="The Real Album",
        track_name="Track One",
    )
    info = build_import_album_info(context)
    assert info["is_album"] is True


def test_default_album_type_does_not_trip_explicit_path() -> None:
    """``album_type='album'`` is the default fallback — must NOT
    trigger the explicit-type bypass, otherwise standalone tracks
    with no real metadata would suddenly route through album_path
    and get an "Albums" folder they didn't have before."""
    context = _make_context(
        album_type="album",
        total_tracks=1,
        album_name="Some Single",
        track_name="Some Single",
    )
    info = build_import_album_info(context)
    # Single-track release with default 'album' type and matching
    # album/title still falls through (not detected as album) so the
    # user's existing single_path behaviour is preserved.
    assert info["is_album"] is False


@pytest.mark.parametrize("album_type", ["", None, "unknown", "playlist"])
def test_unknown_or_missing_album_type_falls_through(album_type) -> None:
    """Defensive: only the three known release types trip the
    explicit path. Empty / unknown values must not."""
    context = _make_context(
        album_type=album_type or "",
        total_tracks=1,
        album_name="Foo",
        track_name="Foo",
    )
    info = build_import_album_info(context)
    assert info["is_album"] is False
