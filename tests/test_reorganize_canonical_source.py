"""_resolve_source honors a pinned canonical release (#765 Stage 3, read side).

Gated + side-effect-free: only changes behavior for albums that already carry a
canonical_source/canonical_album_id, and an explicit user source pick
(strict_source) still wins. No canonical -> byte-identical to before.
"""

from __future__ import annotations

import core.library_reorganize as lr


def _patch_fetch(monkeypatch, tracklists):
    """tracklists: {(source, album_id): items_or_None}. Patches the album +
    tracklist fetchers and the normaliser (pass-through)."""
    def get_album(source, aid):
        return {"name": f"{source}:{aid}"} if tracklists.get((source, aid)) else None

    def get_tracks(source, aid):
        return tracklists.get((source, aid))

    monkeypatch.setattr(lr, "get_album_for_source", get_album)
    monkeypatch.setattr(lr, "get_album_tracks_for_source", get_tracks)
    monkeypatch.setattr(lr, "_normalize_album_tracks", lambda items: items or [])
    monkeypatch.setattr(lr, "get_source_priority", lambda primary: ["spotify", "itunes", "deezer"])


def test_canonical_source_preferred_over_priority(monkeypatch):
    # Album has spotify (priority winner) AND a pinned canonical = deezer.
    _patch_fetch(monkeypatch, {
        ("spotify", "sp1"): [{"name": "x"}],
        ("deezer", "dz1"): [{"name": "y"}],
    })
    album_data = {
        "spotify_album_id": "sp1", "deezer_id": "dz1",
        "canonical_source": "deezer", "canonical_album_id": "dz1",
    }
    source, api_album, items = lr._resolve_source(album_data, "spotify")
    assert source == "deezer"  # canonical beats the priority walk


def test_canonical_fetch_failure_falls_back_to_priority(monkeypatch):
    # Canonical points at musicbrainz but that fetch yields nothing -> fall back.
    _patch_fetch(monkeypatch, {
        ("spotify", "sp1"): [{"name": "x"}],
        # no entry for ('musicbrainz', 'mb1') -> get_tracks returns None
    })
    album_data = {
        "spotify_album_id": "sp1",
        "canonical_source": "musicbrainz", "canonical_album_id": "mb1",
    }
    source, _, _ = lr._resolve_source(album_data, "spotify")
    assert source == "spotify"  # fell back to priority


def test_strict_source_ignores_canonical(monkeypatch):
    # User explicitly picked spotify in the modal — their choice wins over canonical.
    _patch_fetch(monkeypatch, {
        ("spotify", "sp1"): [{"name": "x"}],
        ("deezer", "dz1"): [{"name": "y"}],
    })
    album_data = {
        "spotify_album_id": "sp1", "deezer_id": "dz1",
        "canonical_source": "deezer", "canonical_album_id": "dz1",
    }
    source, _, _ = lr._resolve_source(album_data, "spotify", strict_source=True)
    assert source == "spotify"


def test_no_canonical_unchanged(monkeypatch):
    # No canonical set -> identical to legacy priority resolution.
    _patch_fetch(monkeypatch, {("spotify", "sp1"): [{"name": "x"}]})
    album_data = {"spotify_album_id": "sp1"}
    source, _, _ = lr._resolve_source(album_data, "spotify")
    assert source == "spotify"
