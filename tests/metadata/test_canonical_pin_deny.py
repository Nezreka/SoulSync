"""Canonical pin deny (#re-releases-showing-as-owned, second layer).

When a local album carries a canonical pin (the exact release its files were
scored against), a discography card from the SAME source with a DIFFERENT id
is a sibling edition — a name match must not credit it as owned.

The deny demands proof before firing: the card's id must actually resolve in
the pin's source (its tracklist loads), so a card that slipped in from a
fallback source (different id-space) can never false-deny. Every missing
piece of data → the original fuzzy behavior, untouched.
"""

from __future__ import annotations

import sys
import types

# Same import stubs as the sibling completion tests.
if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")
    spotipy.Spotify = type("S", (), {"__init__": lambda self, *a, **k: None})
    oauth2 = types.ModuleType("spotipy.oauth2")
    oauth2.SpotifyOAuth = type("O", (), {"__init__": lambda self, *a, **k: None})
    oauth2.SpotifyClientCredentials = oauth2.SpotifyOAuth
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2

if "config.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("config.settings")

    class _DummyConfigManager:
        def get_active_media_server(self):
            return "plex"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

from core.metadata import completion as metadata_completion  # noqa: E402


class _PinnedDB:
    """A library where 'Album X' matched and carries a canonical pin."""

    def __init__(self, canonical):
        self.canonical = canonical
        self.local_album = types.SimpleNamespace(id="local-1")

    def check_album_exists_with_completeness(self, **kwargs):
        return self.local_album, 0.95, 12, 12, True, ["FLAC"]

    def get_album_canonical(self, album_id):
        return self.canonical

    def check_album_completeness(self, album_id, expected_track_count=None):
        expected = expected_track_count or 12
        return 12, expected, 12 >= expected, ["FLAC"]


def _check(db, monkeypatch, *, card_id="rerelease-99", resolvable=True,
           pin_tracks=12):
    """Run check_album_completion for a spotify card against the pinned db."""

    def get_tracks(source, album_id):
        # the pin's own tracklist always loads; the card's id loads only when
        # the test says it is resolvable in that source
        if album_id == (db.canonical or {}).get("album_id"):
            return {"items": [{"id": f"t{i}"} for i in range(pin_tracks)]}
        if resolvable:
            return {"items": [{"id": f"t{i}"} for i in range(15)]}
        return {"items": []}

    monkeypatch.setattr(metadata_completion, "get_album_tracks_for_source",
                        get_tracks)
    return metadata_completion.check_album_completion(
        db,
        {"id": card_id, "name": "Album X", "total_tracks": 15},
        "Artist",
        source_chain=["spotify"],
    )


def test_pin_denies_sibling_edition_from_same_source(monkeypatch):
    # files pinned to spotify:original-1; the spotify re-release card must not
    # read as owned even though the name matches
    db = _PinnedDB({"source": "spotify", "album_id": "original-1"})
    result = _check(db, monkeypatch, card_id="rerelease-99", resolvable=True)
    assert result["status"] == "missing"
    assert result["found_in_db"] is False
    assert result["owned_tracks"] == 0


def test_pin_confirms_its_own_card(monkeypatch):
    # the card that IS the pinned release keeps its owned status
    db = _PinnedDB({"source": "spotify", "album_id": "original-1"})
    result = _check(db, monkeypatch, card_id="original-1")
    assert result["status"] == "completed"
    assert result["found_in_db"] is True


def test_unresolvable_card_id_never_denies(monkeypatch):
    # a card whose id does not load in the pin's source (e.g. it came from a
    # fallback source with a different id-space) falls back to old behavior
    db = _PinnedDB({"source": "spotify", "album_id": "original-1"})
    result = _check(db, monkeypatch, card_id="itunes-12345", resolvable=False)
    assert result["found_in_db"] is True
    assert result["status"] in ("completed", "partial")


def test_pin_from_other_source_never_denies(monkeypatch):
    # pin is a musicbrainz release; a spotify card can't be compared to it
    db = _PinnedDB({"source": "musicbrainz", "album_id": "mb-uuid-1"})
    result = _check(db, monkeypatch, card_id="rerelease-99", resolvable=True)
    assert result["found_in_db"] is True


def test_no_pin_is_untouched(monkeypatch):
    db = _PinnedDB(None)
    result = _check(db, monkeypatch, card_id="rerelease-99", resolvable=True)
    assert result["found_in_db"] is True
    assert result["status"] in ("completed", "partial")
