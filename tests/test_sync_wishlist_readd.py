"""Re-add a synced unmatched track to the wishlist with the original context.

reconstruct_sync_track_data rebuilds the spotify_track_data the sync used. It must
prefer the full cached track (with album images), fall back to the track_result
fields, and refuse anything that wasn't a 'wishlist' row.
"""

from __future__ import annotations

from core.sync.wishlist_readd import reconstruct_sync_track_data


def _tr(index, sid, status='wishlist', **kw):
    return {"index": index, "source_track_id": sid, "download_status": status,
            "name": kw.get("name", ""), "artist": kw.get("artist", ""),
            "album": kw.get("album", ""), "image_url": kw.get("image_url", ""),
            "duration_ms": kw.get("duration_ms", 0)}


def _full(sid, name="Song", with_images=True):
    album = {"name": "Album"}
    if with_images:
        album["images"] = [{"url": "http://cdn/cover.jpg"}]
    return {"id": sid, "name": name, "artists": [{"name": "Artist"}], "album": album,
            "duration_ms": 200000, "popularity": 50}


def test_prefers_full_original_track_by_index():
    trs = [_tr(0, "sp_a"), _tr(1, "sp_b")]
    tracks = [_full("sp_a"), _full("sp_b", name="Other")]
    out = reconstruct_sync_track_data(trs, tracks, 1)
    assert out is tracks[1]                       # exact original (full album+images)
    assert out["album"]["images"][0]["url"] == "http://cdn/cover.jpg"


def test_matches_by_id_when_index_misaligns():
    # tracks_json in a different order than track_results -> match by source_track_id.
    trs = [_tr(0, "sp_a"), _tr(1, "sp_b")]
    tracks = [_full("sp_b"), _full("sp_a")]       # reversed
    out = reconstruct_sync_track_data(trs, tracks, 0)
    assert out["id"] == "sp_a"                    # found by id, not by position


def test_falls_back_to_track_result_fields_when_no_full_track():
    trs = [_tr(0, "sp_x", name="Real Love Baby", artist="Father John Misty",
               album="Real Love Baby", image_url="http://img/x.jpg", duration_ms=188000)]
    out = reconstruct_sync_track_data(trs, [], 0)   # no tracks_json
    assert out["id"] == "sp_x"
    assert out["name"] == "Real Love Baby"
    assert out["artists"] == [{"name": "Father John Misty"}]
    assert out["album"]["name"] == "Real Love Baby"
    assert out["album"]["images"] == [{"url": "http://img/x.jpg"}]
    assert out["duration_ms"] == 188000


def test_fallback_without_image_omits_images():
    out = reconstruct_sync_track_data([_tr(0, "sp_x", album="A")], [], 0)
    assert "images" not in out["album"]


def test_refuses_non_wishlist_row():
    # A matched/downloaded track must not be re-wishlistable.
    trs = [_tr(0, "sp_a", status="completed")]
    assert reconstruct_sync_track_data(trs, [_full("sp_a")], 0) is None


def test_refuses_out_of_range_or_empty():
    assert reconstruct_sync_track_data([], [], 0) is None
    assert reconstruct_sync_track_data(None, None, 0) is None
    assert reconstruct_sync_track_data([_tr(0, "sp_a")], [], 5) is None
    assert reconstruct_sync_track_data([_tr(0, "sp_a")], [], -1) is None


def test_refuses_when_no_id_and_no_full_track():
    # A wishlist row with no source_track_id and no cached track is unidentifiable.
    assert reconstruct_sync_track_data([_tr(0, "")], [], 0) is None
