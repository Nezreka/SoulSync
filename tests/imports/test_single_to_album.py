"""Seam tests for single -> parent-album resolution (Sokhi: single-matched track
splits from its album -> mixed cover art). The selector is pure; the resolver
takes injected fetchers, so neither needs a live metadata client.
"""

from __future__ import annotations

import pytest

from core.imports.single_to_album import (
    select_parent_album,
    resolve_single_to_album,
)
from core.imports.context import detect_album_info_web


# ── pure selector ─────────────────────────────────────────────────────────────
def _alb(name, tracks, album_type="album", **extra):
    return {"name": name, "album_type": album_type, "tracks": tracks, **extra}


def test_picks_album_that_contains_the_track():
    got = select_parent_album("Yellow", [
        _alb("Parachutes", ["Don't Panic", "Shiver", "Yellow", "Trouble"]),
    ])
    assert got and got["name"] == "Parachutes"


def test_returns_none_when_no_album_contains_the_track():
    assert select_parent_album("Yellow", [
        _alb("Some Other Album", ["Track A", "Track B"]),
    ]) is None


def test_never_promotes_onto_a_single_release():
    # The single's own release (album_type 'single', name == track) must be ignored.
    assert select_parent_album("Yellow", [
        _alb("Yellow", ["Yellow"], album_type="single"),
    ]) is None


def test_ignores_ep_and_compilation_types():
    assert select_parent_album("Yellow", [
        _alb("Yellow EP", ["Yellow", "Yellow (Live)"], album_type="ep"),
        _alb("Greatest Hits", ["Yellow", "Clocks"], album_type="compilation"),
    ]) is None


def test_skips_album_named_exactly_like_the_track():
    # An 'album' whose name IS the track title is the single dressed as an album;
    # don't treat it as the parent.
    assert select_parent_album("Yellow", [
        _alb("Yellow", ["Yellow"]),
    ]) is None


def test_matches_through_album_version_qualifier():
    got = select_parent_album("Yellow", [
        _alb("Parachutes", ["Shiver", "Yellow (Album Version)"]),
    ])
    assert got and got["name"] == "Parachutes"


def test_first_qualifying_candidate_wins_deterministically():
    got = select_parent_album("Yellow", [
        _alb("Parachutes", ["Yellow"]),
        _alb("Parachutes (Deluxe)", ["Yellow", "Bonus"]),
    ])
    assert got["name"] == "Parachutes"  # input order = priority


def test_empty_title_returns_none():
    assert select_parent_album("", [_alb("Parachutes", ["Yellow"])]) is None


# ── injected-I/O resolver ─────────────────────────────────────────────────────
def test_resolver_finds_parent_album_lazily():
    calls = {"tracks": 0}
    albums = [
        {"name": "Single Yellow", "album_type": "single", "id": "s1"},   # skipped (not album)
        {"name": "Wrong Album", "album_type": "album", "id": "a1"},
        {"name": "Parachutes", "album_type": "album", "id": "a2"},
    ]

    def fetch_tracks(alb):
        calls["tracks"] += 1
        return {"a1": ["Other"], "a2": ["Yellow", "Shiver"]}.get(alb["id"], [])

    got = resolve_single_to_album(
        "Yellow",
        fetch_album_candidates=lambda: albums,
        fetch_album_tracks=fetch_tracks,
    )
    assert got and got["name"] == "Parachutes" and got["album_id"] == "a2"
    assert calls["tracks"] == 2  # probed a1 then a2, stopped; never probed the single


def test_resolver_returns_none_when_nothing_contains_track():
    got = resolve_single_to_album(
        "Yellow",
        fetch_album_candidates=lambda: [{"name": "X", "album_type": "album", "id": "a1"}],
        fetch_album_tracks=lambda alb: ["Nope"],
    )
    assert got is None


def test_resolver_is_failsafe_on_candidate_fetch_error():
    def boom():
        raise RuntimeError("api down")
    assert resolve_single_to_album(
        "Yellow", fetch_album_candidates=boom, fetch_album_tracks=lambda a: []) is None


def test_resolver_is_failsafe_on_track_fetch_error():
    def boom(alb):
        raise RuntimeError("api down")
    got = resolve_single_to_album(
        "Yellow",
        fetch_album_candidates=lambda: [{"name": "Parachutes", "album_type": "album", "id": "a1"}],
        fetch_album_tracks=boom)
    assert got is None


def test_resolver_caps_albums_probed():
    albums = [{"name": f"A{i}", "album_type": "album", "id": str(i)} for i in range(20)]
    probed = {"n": 0}

    def fetch_tracks(alb):
        probed["n"] += 1
        return ["nope"]

    resolve_single_to_album(
        "Yellow",
        fetch_album_candidates=lambda: albums,
        fetch_album_tracks=fetch_tracks,
        max_albums=5)
    assert probed["n"] == 5  # never probes more than the cap


# ── gated wiring through detect_album_info_web (config gate + client shapes) ───
class _Cfg:
    def __init__(self, on):
        self._on = on

    def get(self, key, default=None):
        if key == "metadata_enhancement.single_to_album":
            return self._on
        return default


_SINGLE_CTX = {
    "source": "spotify",
    "artist": {"id": "art1", "name": "Coldplay"},
    # album_type unset + name == track + total_tracks 1 -> is_album False, and the
    # existing best-effort skips (album name == track), so the glue is reached.
    "album": {"id": "s1", "name": "Yellow", "total_tracks": 1},
    "track_info": {"id": "t1", "name": "Yellow", "track_number": 7},
    "original_search_result": {"title": "Yellow", "album": "Yellow"},
}


def _patch_clients(monkeypatch, albums, tracks_by_id):
    monkeypatch.setattr("core.metadata.album_tracks.get_artist_albums_for_source",
                        lambda *a, **k: albums)
    monkeypatch.setattr("core.metadata.album_tracks.get_artist_album_tracks",
                        lambda album_id, **k: {"tracks": tracks_by_id.get(album_id, [])})


def test_glue_promotes_single_to_parent_album_when_enabled(monkeypatch):
    monkeypatch.setattr("core.metadata.common.get_config_manager", lambda: _Cfg(True))
    _patch_clients(
        monkeypatch,
        albums=[{"name": "Yellow", "album_type": "single", "id": "s1"},
                {"name": "Parachutes", "album_type": "album", "id": "a2"}],
        tracks_by_id={"a2": [{"title": "Shiver"}, {"title": "Yellow"}]},
    )
    out = detect_album_info_web(dict(_SINGLE_CTX), {"id": "art1", "name": "Coldplay"})
    assert out and out["is_album"] is True
    assert out["album_name"] == "Parachutes"
    assert out["track_number"] == 7   # preserved


def test_glue_disabled_by_default_returns_none(monkeypatch):
    monkeypatch.setattr("core.metadata.common.get_config_manager", lambda: _Cfg(False))
    # Even with clients that WOULD match, the flag off => no promotion.
    _patch_clients(monkeypatch,
                   albums=[{"name": "Parachutes", "album_type": "album", "id": "a2"}],
                   tracks_by_id={"a2": [{"title": "Yellow"}]})
    assert detect_album_info_web(dict(_SINGLE_CTX), {"id": "art1", "name": "Coldplay"}) is None


def test_glue_no_match_returns_none(monkeypatch):
    monkeypatch.setattr("core.metadata.common.get_config_manager", lambda: _Cfg(True))
    _patch_clients(monkeypatch,
                   albums=[{"name": "Other Album", "album_type": "album", "id": "a9"}],
                   tracks_by_id={"a9": [{"title": "Different Song"}]})
    assert detect_album_info_web(dict(_SINGLE_CTX), {"id": "art1", "name": "Coldplay"}) is None


def test_glue_failsafe_when_client_raises(monkeypatch):
    monkeypatch.setattr("core.metadata.common.get_config_manager", lambda: _Cfg(True))

    def boom(*a, **k):
        raise RuntimeError("spotify down")
    monkeypatch.setattr("core.metadata.album_tracks.get_artist_albums_for_source", boom)
    assert detect_album_info_web(dict(_SINGLE_CTX), {"id": "art1", "name": "Coldplay"}) is None
