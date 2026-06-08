"""Tests for #767-2 alternate-edition fetching: the pure same-release name
matcher, and the production default_fetch_alternates wired over fake source APIs."""

from __future__ import annotations

import core.metadata.canonical_resolver as cr
from core.metadata.canonical_resolver import (
    _release_name_key,
    _same_release,
    default_fetch_alternates,
)


# ── pure same-release name matching ───────────────────────────────────────────

def test_release_name_key_strips_editions_and_punctuation():
    assert _release_name_key("Scatterbrain") == "scatterbrain"
    assert _release_name_key("Scatterbrain (Deluxe Edition)") == "scatterbrain"
    assert _release_name_key("Scatterbrain - Single") == "scatterbrain"
    assert _release_name_key("Scatterbrain [Remastered]") == "scatterbrain"
    assert _release_name_key("Scatterbrain (Expanded)") == "scatterbrain"


def test_same_release_matches_editions_of_one_album():
    assert _same_release("Scatterbrain", "Scatterbrain (Deluxe)")
    assert _same_release("Scatterbrain - Single", "Scatterbrain (Deluxe Edition)")
    assert _same_release("The Wall", "The Wall [Remastered]")


def test_same_release_rejects_different_albums():
    assert not _same_release("Scatterbrain", "Brain Scatter")
    assert not _same_release("Yellow", "Parachutes")
    assert not _same_release("", "Anything")  # empty key never matches


# ── production fetcher over fake source APIs ──────────────────────────────────

SINGLE = [{"name": "Scatterbrain", "track_number": 1, "duration_ms": 129_000}]
DELUXE = [{"name": "Intro", "track_number": 1, "duration_ms": 200_000}] + [
    {"name": f"Track {i}", "track_number": i + 1, "duration_ms": 180_000}
    for i in range(1, 10)
]


def _install_fake_apis(monkeypatch, *, artist_albums, tracklists, album_meta=None):
    """Patch the album_tracks module functions default_fetch_alternates imports."""
    import core.metadata.album_tracks as at

    monkeypatch.setattr(at, "get_album_for_source",
                        lambda s, aid: (album_meta or {}).get(aid), raising=True)
    monkeypatch.setattr(at, "get_artist_albums_for_source",
                        lambda s, a_id, a_name, **kw: artist_albums, raising=True)
    # default_fetch_alternates pulls per-edition tracklists via default_fetch_tracklist,
    # which calls get_album_tracks_for_source in the metadata_service module.
    monkeypatch.setattr(
        "core.metadata_service.get_album_tracks_for_source",
        lambda s, aid: tracklists.get(aid), raising=True,
    )


def test_default_fetch_alternates_finds_the_single(monkeypatch):
    artist_albums = [
        {"id": "sp_deluxe", "name": "Scatterbrain (Deluxe)"},
        {"id": "sp_single", "name": "Scatterbrain - Single"},
        {"id": "other", "name": "A Different Album"},
    ]
    tracklists = {"sp_deluxe": DELUXE, "sp_single": SINGLE, "other": SINGLE}
    _install_fake_apis(monkeypatch, artist_albums=artist_albums, tracklists=tracklists)

    out = default_fetch_alternates(
        "spotify", "sp_deluxe",
        artist_id="art1", artist_name="The Band", album_title="Scatterbrain",
    )
    ids = {e["album_id"] for e in out}
    assert ids == {"sp_deluxe", "sp_single"}      # the unrelated album is excluded
    single = next(e for e in out if e["album_id"] == "sp_single")
    assert len(single["tracks"]) == 1 and single["tracks"][0]["duration_ms"] == 129_000


def test_default_fetch_alternates_discovers_artist_from_album_meta(monkeypatch):
    # No artist context supplied -> it must call get_album_for_source to learn it.
    album_meta = {"sp_deluxe": {"title": "Scatterbrain", "artist_id": "art1", "artist": "The Band"}}
    artist_albums = [{"id": "sp_single", "name": "Scatterbrain (Single)"}]
    tracklists = {"sp_single": SINGLE}
    _install_fake_apis(monkeypatch, artist_albums=artist_albums,
                       tracklists=tracklists, album_meta=album_meta)

    out = default_fetch_alternates("spotify", "sp_deluxe")
    assert [e["album_id"] for e in out] == ["sp_single"]


def test_default_fetch_alternates_empty_when_no_artist(monkeypatch):
    _install_fake_apis(monkeypatch, artist_albums=[], tracklists={})
    out = default_fetch_alternates("spotify", "x", album_title="Scatterbrain")
    assert out == []  # no artist id/name and no album meta -> nothing to search


def test_default_fetch_alternates_caps_editions(monkeypatch):
    # 10 same-release editions, cap is 6.
    artist_albums = [{"id": f"e{i}", "name": "Scatterbrain (Version %d)" % i} for i in range(10)]
    tracklists = {f"e{i}": SINGLE for i in range(10)}
    _install_fake_apis(monkeypatch, artist_albums=artist_albums, tracklists=tracklists)
    out = default_fetch_alternates(
        "spotify", "e0", artist_id="a", album_title="Scatterbrain", max_editions=6,
    )
    assert len(out) == 6
