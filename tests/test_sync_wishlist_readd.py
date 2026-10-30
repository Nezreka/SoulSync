"""Re-add a synced unmatched track to the wishlist with the EXACT auto-add payload.

The live sync and the sync-detail re-add must build the identical wishlist payload
(via build_original_tracks_map), so a re-added track is indistinguishable from the
original auto-add — including its album cover, album_type, and artist shape.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from core.sync.wishlist_readd import (
    build_original_tracks_map,
    normalize_wishlist_track,
    reconstruct_sync_track_data,
    sync_history_curation_origin,
)


def _tr(index, sid, status='wishlist', **kw):
    return {"index": index, "source_track_id": sid, "download_status": status,
            "name": kw.get("name", ""), "artist": kw.get("artist", ""),
            "album": kw.get("album", ""), "image_url": kw.get("image_url", ""),
            "duration_ms": kw.get("duration_ms", 0)}


def _full(sid, name="Song", with_images=True):
    album = {"name": "Album", "album_type": "album", "total_tracks": 12}
    if with_images:
        album["images"] = [{"url": "http://cdn/cover.jpg", "height": 640, "width": 640}]
    return {"id": sid, "name": name, "artists": [{"name": "Artist"}], "album": album,
            "duration_ms": 200000, "popularity": 50}


# ── normalize_wishlist_track ──────────────────────────────────────────────────

def test_normalize_string_album_to_dict():
    out = normalize_wishlist_track({"id": "a", "name": "T", "album": "My Single", "artists": ["X"]})
    assert out["album"] == {"name": "My Single", "images": [], "album_type": "single",
                            "total_tracks": 1, "release_date": ""}
    assert out["artists"] == [{"name": "X"}]               # strings -> dicts


def test_normalize_dict_album_preserves_images_and_type():
    out = normalize_wishlist_track(_full("a"))
    assert out["album"]["images"][0]["url"] == "http://cdn/cover.jpg"
    assert out["album"]["album_type"] == "album"
    assert out["album"]["total_tracks"] == 12


def test_normalize_is_copy_safe():
    src = {"id": "a", "album": {"name": "A"}, "artists": ["X"]}
    normalize_wishlist_track(src)
    assert "images" not in src["album"]                    # source untouched
    assert src["artists"] == ["X"]


# ── reconstruct: parity with the live auto-add ────────────────────────────────

def test_payload_is_identical_to_live_sync_map():
    # THE PARITY GUARANTEE: re-add payload == what build_original_tracks_map (used by
    # the live sync) produces for the same track.
    trs = [_tr(0, "sp_a"), _tr(1, "sp_b")]
    tracks = [_full("sp_a"), _full("sp_b", name="Other")]
    out = reconstruct_sync_track_data(trs, tracks, 1)
    assert out == build_original_tracks_map(tracks)["sp_b"]
    assert out["album"]["images"][0]["url"] == "http://cdn/cover.jpg"   # cover carries through


def test_resolves_by_source_track_id_not_position():
    trs = [_tr(0, "sp_a"), _tr(1, "sp_b")]
    tracks = [_full("sp_b"), _full("sp_a")]               # tracks_json reversed
    out = reconstruct_sync_track_data(trs, tracks, 0)     # row 0 -> sp_a
    assert out["id"] == "sp_a"


def test_fallback_rebuilds_with_cover_when_track_missing():
    # Track not in tracks_json -> rebuild from the row, seed cover from image_url,
    # run through the same normalizer (album dict + artists dicts).
    trs = [_tr(0, "sp_x", name="Real Love Baby", artist="Father John Misty",
               album="Real Love Baby", image_url="http://img/x.jpg", duration_ms=188000)]
    out = reconstruct_sync_track_data(trs, [], 0)
    assert out["id"] == "sp_x"
    assert out["name"] == "Real Love Baby"
    assert out["artists"] == [{"name": "Father John Misty"}]
    assert out["album"]["images"] == [{"url": "http://img/x.jpg"}]
    assert out["album"]["name"] == "Real Love Baby"


def test_refuses_non_wishlist_row():
    trs = [_tr(0, "sp_a", status="completed")]
    assert reconstruct_sync_track_data(trs, [_full("sp_a")], 0) is None


def test_refuses_out_of_range_or_empty():
    assert reconstruct_sync_track_data([], [], 0) is None
    assert reconstruct_sync_track_data(None, None, 0) is None
    assert reconstruct_sync_track_data([_tr(0, "sp_a")], [], 5) is None
    assert reconstruct_sync_track_data([_tr(0, "sp_a")], [], -1) is None


def test_refuses_when_no_id_and_no_full_track():
    assert reconstruct_sync_track_data([_tr(0, "")], [], 0) is None


def test_refuses_wing_it_stub():
    # Wing-it fallback stubs have no real metadata; the sync skips them, so must we —
    # even if a full (stub) track happens to be in tracks_json.
    trs = [_tr(0, "wing_it_abc123", name="Sami Matar", artist="X")]
    assert reconstruct_sync_track_data(trs, [], 0) is None
    stub = {"id": "wing_it_abc123", "name": "Sami Matar", "album": "Sami Matar", "artists": ["X"]}
    assert reconstruct_sync_track_data(trs, [stub], 0) is None


def test_build_map_skips_idless_and_non_dicts():
    m = build_original_tracks_map([_full("a"), {"name": "no id"}, "garbage", None])
    assert set(m.keys()) == {"a"}


def test_sync_history_readd_recovers_durable_mirror_origin():
    results = [_tr(0, "source-track")]
    assert sync_history_curation_origin(
        {"playlist_id": "auto_mirror_42"}, results, 0
    ) == {
        "mirrored_playlist_id": 42,
        "source_track_id": "source-track",
    }


def test_sync_history_readd_does_not_guess_numeric_upstream_id_as_mirror():
    assert sync_history_curation_origin(
        {"playlist_id": "908622995"}, [_tr(0, "source-track")], 0
    ) is None


def test_sync_history_route_forwards_curation_origin(monkeypatch):
    import web_server
    import core.wishlist_service as wishlist_service

    entry = {
        "playlist_id": "auto_mirror_42",
        "playlist_name": "Curated",
        "profile_id": 1,
        "quality_profile_id": None,
        "tracks_json": json.dumps([_full("source-track")]),
        "track_results": json.dumps([_tr(0, "source-track")]),
    }

    class _HistoryDB:
        def get_sync_history_entry(self, entry_id, profile_id=None):
            return entry if entry_id == 7 else None

    calls = []
    service = SimpleNamespace(
        add_spotify_track_to_wishlist=lambda **kwargs: calls.append(kwargs) or False
    )
    monkeypatch.setattr(web_server, "MusicDatabase", _HistoryDB)
    monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(wishlist_service, "get_wishlist_service", lambda: service)

    response = web_server.app.test_client().post(
        "/api/sync/history/7/track/0/wishlist"
    )

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["source_context"]["mirrored_playlist_id"] == 42
    assert calls[0]["source_context"]["source_track_id"] == "source-track"
