"""#1981: a playlist-origin wishlist track that fails inside a wishlist
ALBUM batch must be re-added with its playlist provenance intact.

The re-add used to take its source_type/source_context from the batch
alone ('album' + 'Wishlist (Album: X)' / 'wishlist'), and the wishlist
DB refresh overwrites the existing row's provenance with whatever the
re-add carries. The row then no longer groups as a playlist batch on
the next cycle, and when the track finally downloads,
register_completed_playlist_batch resolves the batch label to no
mirrored playlist — the playlist is never re-synced to the server.
"""
import threading
from types import SimpleNamespace

import pytest

import core.downloads.wishlist_failed as wf_mod
from core.sync import post_download_sync as pds
from core.wishlist.payloads import build_failed_track_wishlist_context
from core.wishlist.selection import sanitize_and_dedupe_wishlist_tracks
from core.wishlist.service import WishlistService


class _CapturingWishlistService:
    def __init__(self):
        self.adds = []

    def add_failed_track_from_modal(self, track_info=None, source_type=None,
                                    source_context=None, profile_id=1, detailed=False):
        self.adds.append({
            "track_info": track_info,
            "source_type": source_type,
            "source_context": source_context,
            "profile_id": profile_id,
        })
        # the re-add asks for the detailed outcome (#1998)
        return {"status": "created", "created": True, "reason": None} if detailed else True

    def mark_track_download_result(self, spotify_track_id, success,
                                   error_message=None, profile_id=1, **kwargs):
        return True


_ALBUM_BATCH = {
    "profile_id": 1,
    "playlist_id": "wishlist",
    "playlist_name": "Wishlist (Album: Jam)",
    "is_album_download": True,
    "album_context": {"id": "alb-1", "name": "Jam", "total_tracks": 10},
    "artist_context": {"id": "art-1", "name": "Some Artist"},
}


def _failed_track(track_data):
    return {
        "track_name": track_data.get("name", "Track"),
        "artist_name": "Some Artist",
        "track_data": track_data,
        "spotify_track": track_data,
        "failure_reason": "No matching track found",
        "candidates": [],
    }


def test_album_batch_failure_readds_playlist_track_with_its_playlist():
    service = _CapturingWishlistService()
    track_data = {
        "id": "dz-track-1",
        "name": "Run Away",
        "artists": [{"name": "Some Artist"}],
        "album": {"id": "alb-1", "name": "Jam"},
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin"},
    }

    wf_mod.add_failed_tracks_to_wishlist(
        _ALBUM_BATCH, [_failed_track(track_data)], service)

    assert len(service.adds) == 1
    add = service.adds[0]
    assert add["source_type"] == "playlist"
    assert add["source_context"]["playlist_id"] == "908622995"
    assert add["source_context"]["playlist_name"] == "Jam Skatin"


def test_album_batch_failure_readds_plain_track_with_batch_provenance():
    service = _CapturingWishlistService()
    track_data = {
        "id": "sp-plain",
        "name": "Plain Song",
        "artists": [{"name": "Some Artist"}],
        "album": {"id": "alb-1", "name": "Jam"},
    }

    wf_mod.add_failed_tracks_to_wishlist(
        _ALBUM_BATCH, [_failed_track(track_data)], service)

    assert len(service.adds) == 1
    add = service.adds[0]
    assert add["source_type"] == "album"
    assert add["source_context"]["playlist_name"] == "Wishlist (Album: Jam)"
    assert add["source_context"]["playlist_id"] == "wishlist"


class _StampingCapturingWishlistService(_CapturingWishlistService):
    def __init__(self):
        super().__init__()
        self.stamps = []

    def mark_track_download_result(self, spotify_track_id, success,
                                   error_message=None, profile_id=1, **kwargs):
        self.stamps.append((spotify_track_id, success, profile_id))
        return True


def test_resolver_failure_falls_back_to_the_batch_pair_and_still_stamps(monkeypatch):
    """The per-track resolver call must be fail-safe: if it raises, the
    track is still re-added with the precomputed batch-level pair and
    its attempt is still stamped — a provenance hiccup must not skip
    the re-add or count as an error."""
    service = _StampingCapturingWishlistService()
    track_data = {
        "id": "dz-track-1",
        "name": "Run Away",
        "artists": [{"name": "Some Artist"}],
        "album": {"id": "alb-1", "name": "Jam"},
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin"},
    }

    def _boom(*args, **kwargs):
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(
        wf_mod, "_resolve_failed_track_requeue_provenance", _boom)

    summary = wf_mod.add_failed_tracks_to_wishlist(
        _ALBUM_BATCH, [_failed_track(track_data)], service)

    assert summary["errors"] == 0
    assert summary["tracks_added"] == 1
    assert len(service.adds) == 1
    add = service.adds[0]
    assert add["source_type"] == "album"
    assert add["source_context"]["playlist_id"] == "wishlist"
    assert add["source_context"]["playlist_name"] == "Wishlist (Album: Jam)"
    assert service.stamps == [("dz-track-1", False, 1)]


class _ResolveDb:
    """Mimics MusicDatabase.resolve_mirrored_playlist_assignment's
    contract: an exact ref match resolves, and so does an unambiguous
    name match when the ref is missing/unknown."""

    def __init__(self):
        self.calls = []

    def resolve_mirrored_playlist_assignment(self, playlist_ref=None,
                                             playlist_name=None, profile_id=1):
        self.calls.append((playlist_ref, playlist_name, profile_id))
        if str(playlist_ref) == "908622995" or playlist_name == "Jam Skatin":
            return {"id": 7, "name": "Jam Skatin"}
        return None


def _wishlist_row(source_info, *, source_type="playlist", track_id="dz-track-1"):
    """A wishlist DB row in the shape get_wishlist_tracks returns."""
    return {
        "id": "wl-1",
        "spotify_track_id": track_id,
        "track_data": {
            "id": track_id,
            "name": "Run Away",
            "artists": [{"name": "Some Artist"}],
            "album": {"id": "alb-1", "name": "Jam"},
        },
        "failure_reason": "No matching track found",
        "retry_count": 0,
        "date_added": "2026-01-01",
        "last_attempted": None,
        "source_type": source_type,
        "source_info": source_info,
    }


def _producer_chain_failed_track(row):
    """Run a wishlist row through the real producer steps that turn it
    into a failed-track entry for the re-add:

    format_track_for_download (row -> flat track_info, the shape a
    download task carries) -> sanitize_and_dedupe_wishlist_tracks (the
    wishlist flows' sanitize pass; it flattens artists to plain strings)
    -> build_failed_track_wishlist_context (the cancel/recover failure
    builders; the main failure path in core/downloads/lifecycle.py calls
    the same ensure_wishlist_track_format on the task's track_info).
    """
    formatted = WishlistService.format_track_for_download(row)
    sanitized, _dups = sanitize_and_dedupe_wishlist_tracks([formatted])
    return build_failed_track_wishlist_context(
        sanitized[0], track_index=0, failure_reason="No matching track found")


def _register_completed(formatted_track_info, db=None):
    return pds.register_completed_playlist_batch(
        {"playlist_id": "wishlist", "profile_id": 1},
        tasks_snapshot={"t1": {"status": "completed",
                               "track_info": formatted_track_info}},
        database=db or _ResolveDb(),
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    pds.clear_pending_playlist_syncs()
    yield
    pds.clear_pending_playlist_syncs()


def test_preserved_provenance_registers_the_playlist_on_a_later_success():
    """The end of the chain the fix restores, exercised through the real
    intermediate steps: wishlist row -> format_track_for_download ->
    sanitize -> failed-track context -> re-add; then the refreshed row
    (the re-add's source_context becomes the row's source_info, which is
    what add_failed_track_from_modal stores) -> format_track_for_download
    again -> register_completed_playlist_batch resolves the mirrored
    playlist. The degraded pair the old code stored ('wishlist' /
    'Wishlist (Album: Jam)') resolves to nothing — asserted here so the
    contrast is pinned, not implied.

    The one step not exercised is the DB write/read itself
    (add_to_wishlist + get_wishlist_tracks against a real database);
    the refreshed row below is built from the captured re-add exactly
    the way the service stores it."""
    service = _CapturingWishlistService()
    failed = _producer_chain_failed_track(_wishlist_row(
        {"playlist_id": "908622995", "playlist_name": "Jam Skatin"}))
    wf_mod.add_failed_tracks_to_wishlist(_ALBUM_BATCH, [failed], service)
    add = service.adds[0]
    assert add["source_type"] == "playlist"

    next_row = _wishlist_row(
        {"playlist_id": add["source_context"]["playlist_id"],
         "playlist_name": add["source_context"]["playlist_name"]},
        source_type=add["source_type"])
    next_track_info = WishlistService.format_track_for_download(next_row)
    assert _register_completed(next_track_info) == [7]

    degraded_row = _wishlist_row(
        {"playlist_id": "wishlist", "playlist_name": "Wishlist (Album: Jam)"},
        source_type="album")
    degraded_info = WishlistService.format_track_for_download(degraded_row)
    assert _register_completed(degraded_info) == []


def test_producer_chain_string_artists_keep_playlist_provenance():
    """Finding-1 regression: the row's artists start as dicts, the real
    sanitize pass flattens them to plain strings, and the failure
    builder's format pass must still carry the playlist provenance into
    the re-add (pre-fix the normalized rebuild dropped it and the track
    came back as the degraded ('album', wishlist) pair)."""
    service = _CapturingWishlistService()
    failed = _producer_chain_failed_track(_wishlist_row(
        {"playlist_id": "908622995", "playlist_name": "Jam Skatin"}))
    assert failed["track_data"]["artists"] == [{"name": "Some Artist"}]

    wf_mod.add_failed_tracks_to_wishlist(_ALBUM_BATCH, [failed], service)

    add = service.adds[0]
    assert add["source_type"] == "playlist"
    assert add["source_context"]["playlist_id"] == "908622995"
    assert add["source_context"]["playlist_name"] == "Jam Skatin"


def test_producer_chain_json_string_source_info_keeps_provenance():
    """Rows whose source_info is stored as a JSON string (the DB shape)
    resolve through the same chain at the wishlist_failed level."""
    service = _CapturingWishlistService()
    failed = _producer_chain_failed_track(_wishlist_row(
        '{"playlist_id": "908622995", "playlist_name": "Jam Skatin"}'))

    wf_mod.add_failed_tracks_to_wishlist(_ALBUM_BATCH, [failed], service)

    add = service.adds[0]
    assert add["source_type"] == "playlist"
    assert add["source_context"]["playlist_id"] == "908622995"
    assert add["source_context"]["playlist_name"] == "Jam Skatin"


def test_mixed_batch_tracks_keep_independent_provenance():
    """One album batch, three failures: playlist tracks keep their own
    playlists, the plain track keeps the batch pair, and every track of
    the batch shares one source_context timestamp (built once)."""
    service = _CapturingWishlistService()
    playlist_a = {
        "id": "dz-a", "name": "Song A", "artists": [{"name": "Some Artist"}],
        "album": {"id": "alb-1", "name": "Jam"},
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin"},
    }
    plain = {
        "id": "sp-plain", "name": "Plain Song",
        "artists": [{"name": "Some Artist"}],
        "album": {"id": "alb-1", "name": "Jam"},
    }
    playlist_b = {
        "id": "dz-b", "name": "Song B", "artists": [{"name": "Some Artist"}],
        "album": {"id": "alb-1", "name": "Jam"},
        "source_type": "playlist",
        "source_info": {"playlist_id": "777", "playlist_name": "Other Mix"},
    }

    wf_mod.add_failed_tracks_to_wishlist(
        _ALBUM_BATCH,
        [_failed_track(playlist_a), _failed_track(plain), _failed_track(playlist_b)],
        service)

    assert len(service.adds) == 3
    first, second, third = service.adds
    assert first["source_type"] == "playlist"
    assert first["source_context"]["playlist_id"] == "908622995"
    assert second["source_type"] == "album"
    assert second["source_context"]["playlist_id"] == "wishlist"
    assert second["source_context"]["playlist_name"] == "Wishlist (Album: Jam)"
    assert third["source_type"] == "playlist"
    assert third["source_context"]["playlist_id"] == "777"
    assert third["source_context"]["playlist_name"] == "Other Mix"
    timestamps = {add["source_context"]["timestamp"] for add in service.adds}
    assert len(timestamps) == 1


def test_residual_playlist_batch_requeues_every_track_with_the_repaired_pair():
    """Gate pass 3, end to end: in a residual playlist batch the
    builder's context carries the 'wishlist' sentinel (it never reads
    source_playlist_id), and the resolver's repair is what substitutes
    the batch's real ref. That repair describes the batch, so a track
    whose payload lost its source_type requeues with the SAME pair as
    its playlist batch-mates — not the sentinel beside the real name.
    The batch is for a playlist named 'Wishlist (2024)': its own name
    (dispatch grouped by it) survives beside the ref too."""
    service = _CapturingWishlistService()
    residual_batch = {
        "profile_id": 1,
        "playlist_id": "wishlist",
        "playlist_name": "Wishlist (2024)",
        "is_playlist_batch": True,
        "source_playlist_name": "Wishlist (2024)",
        "source_playlist_id": "908622995",
        "mirrored_playlist_id": 7,
    }
    playlist_track = {
        "id": "dz-a", "name": "Song A", "artists": [{"name": "Some Artist"}],
        "album": {"id": "alb-1", "name": "Jam"},
        "source_type": "playlist",
        "source_info": {"playlist_id": "stale-1", "playlist_name": "Old Name"},
    }
    plain_track = {
        "id": "sp-plain", "name": "Plain Song",
        "artists": [{"name": "Some Artist"}],
        "album": {"id": "alb-1", "name": "Jam"},
    }

    wf_mod.add_failed_tracks_to_wishlist(
        residual_batch,
        [_failed_track(playlist_track), _failed_track(plain_track)],
        service)

    assert len(service.adds) == 2
    for add in service.adds:
        assert add["source_type"] == "playlist"
        assert add["source_context"]["playlist_id"] == "908622995"
        assert add["source_context"]["playlist_name"] == "Wishlist (2024)"
        assert add["source_context"]["mirrored_playlist_id"] == 7


def test_auto_album_batch_plain_track_keeps_the_degraded_name_fallback():
    """The auto flow's album-batch label ('Wishlist (Auto - Album: X)')
    is the fallback pair for tracks without their own provenance."""
    service = _CapturingWishlistService()
    auto_batch = {**_ALBUM_BATCH, "playlist_name": "Wishlist (Auto - Album: Jam)"}
    track_data = {
        "id": "sp-plain", "name": "Plain Song",
        "artists": [{"name": "Some Artist"}],
        "album": {"id": "alb-1", "name": "Jam"},
    }

    wf_mod.add_failed_tracks_to_wishlist(
        auto_batch, [_failed_track(track_data)], service)

    add = service.adds[0]
    assert add["source_type"] == "album"
    assert add["source_context"]["playlist_name"] == "Wishlist (Auto - Album: Jam)"
    assert add["source_context"]["playlist_id"] == "wishlist"


@pytest.mark.parametrize("source_info", [
    pytest.param(
        {"playlist_id": "wishlist", "playlist_name": "Jam Skatin"},
        id="name-only"),
    pytest.param(
        {"playlist_id": "908622995",
         "playlist_name": "Wishlist (Album: Jam)"},
        id="ref-only"),
])
def test_partial_provenance_still_registers_on_a_later_success(source_info):
    """Name-only and ref-only preservation both stay registerable: the
    dead half of the pair is absent from the refreshed row (not the
    batch sentinel), and resolution succeeds on the surviving half."""
    service = _CapturingWishlistService()
    failed = _producer_chain_failed_track(_wishlist_row(source_info))
    wf_mod.add_failed_tracks_to_wishlist(_ALBUM_BATCH, [failed], service)
    add = service.adds[0]
    assert add["source_type"] == "playlist"

    refreshed_info = {
        key: add["source_context"][key]
        for key in ("playlist_id", "playlist_name")
        if key in add["source_context"]
    }
    # exactly one half survived, and it is not a sentinel/label
    assert len(refreshed_info) == 1
    assert "wishlist" not in refreshed_info.values()
    assert "Wishlist (Album: Jam)" not in refreshed_info.values()

    next_row = _wishlist_row(refreshed_info, source_type=add["source_type"])
    next_track_info = WishlistService.format_track_for_download(next_row)
    assert _register_completed(next_track_info) == [7]


class _NoResolveDb:
    """A DB whose resolver finds nothing — registration can only
    succeed here through the int-PK path, never through resolution."""

    def resolve_mirrored_playlist_assignment(self, playlist_ref=None,
                                             playlist_name=None, profile_id=1):
        return None


def test_int_ref_registers_as_a_mirrored_pk_on_a_later_success():
    """An int playlist ref survives the re-add AS an int, and
    registration treats it as a mirrored-playlist PK directly (no
    resolution needed). The same id as a digit STRING is an upstream
    id: it goes through resolution and registers nothing when the DB
    resolves nothing — the contrast the int-preservation pins."""
    service = _CapturingWishlistService()
    track_data = {
        "id": "dz-track-1",
        "name": "Run Away",
        "artists": [{"name": "Some Artist"}],
        "album": {"id": "alb-1", "name": "Jam"},
        "source_type": "playlist",
        "source_info": {"playlist_id": 7, "playlist_name": "Jam Skatin"},
    }

    wf_mod.add_failed_tracks_to_wishlist(
        _ALBUM_BATCH, [_failed_track(track_data)], service)

    add = service.adds[0]
    assert add["source_type"] == "playlist"
    assert add["source_context"]["playlist_id"] == 7
    assert isinstance(add["source_context"]["playlist_id"], int)

    next_row = _wishlist_row(
        {"playlist_id": add["source_context"]["playlist_id"],
         "playlist_name": add["source_context"]["playlist_name"]},
        source_type=add["source_type"])
    next_track_info = WishlistService.format_track_for_download(next_row)
    assert _register_completed(next_track_info, db=_NoResolveDb()) == [7]

    string_row = _wishlist_row(
        {"playlist_id": "7", "playlist_name": "Jam Skatin"})
    string_info = WishlistService.format_track_for_download(string_row)
    assert _register_completed(string_info, db=_NoResolveDb()) == []


def _dispatch_runtime():
    class _Exec:
        def submit(self, fn, *a, **k):
            pass

    return SimpleNamespace(
        processing_guard=None,
        is_actually_processing=lambda: False,
        app_context_factory=None,
        get_profiles_database=lambda: None,
        get_music_database=lambda: None,
        download_batches={},
        tasks_lock=threading.Lock(),
        update_automation_progress=lambda *a, **k: None,
        automation_engine=None,
        missing_download_executor=_Exec(),
        album_bundle_executor=None,
        run_full_missing_tracks_process=lambda *a, **k: None,
        get_batch_max_concurrent=lambda: 3,
        get_active_server=lambda: "test",
        current_time_fn=lambda: 0.0,
        profile_id=1,
        logger=SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
            error=lambda *a, **k: None, debug=lambda *a, **k: None,
        ),
    )


def _dispatch_track(track_id, playlist_id):
    return {
        "track_id": track_id,
        "spotify_track_id": track_id,
        "track_name": "Track",
        "name": "Track",
        "artists": [{"name": "Some Artist"}],
        "profile_id": 1,
        "source_type": "playlist",
        "source_info": {"playlist_id": playlist_id,
                        "playlist_name": "Jam Skatin"},
    }


def test_dispatch_playlist_group_ref_skips_a_sentinel_for_a_real_ref():
    """Gate pass 4: dispatch stamps the playlist batch's
    source_playlist_id from the FIRST truthy source_info.playlist_id
    in the group — a 'wishlist' sentinel on an early track used to
    hide a later track's real ref, leaving the batch (and every
    requeue from it) with no usable playlist resolution."""
    from core.wishlist.processing import _run_wishlist_cycle

    runtime = _dispatch_runtime()
    _run_wishlist_cycle(
        runtime, playlist_id="wishlist", cycle="singles",
        tracks=[_dispatch_track("sp-1", "wishlist"),
                _dispatch_track("sp-2", "908622995")],
        run_id="run-1", auto_initiated=False,
    )

    batches = list(runtime.download_batches.values())
    assert len(batches) == 1
    assert batches[0]["source_playlist_id"] == "908622995"


def test_dispatch_playlist_group_all_sentinel_refs_stamp_none():
    """Gate pass 5: when every track in the group carries only the
    'wishlist' sentinel, the batch's source_playlist_id is None (the
    mirrored resolution falls back to the batch name) rather than the
    sentinel itself."""
    from core.wishlist.processing import _run_wishlist_cycle

    runtime = _dispatch_runtime()
    _run_wishlist_cycle(
        runtime, playlist_id="wishlist", cycle="singles",
        tracks=[_dispatch_track("sp-1", "wishlist"),
                _dispatch_track("sp-2", "wishlist")],
        run_id="run-1", auto_initiated=False,
    )

    batches = list(runtime.download_batches.values())
    assert len(batches) == 1
    assert batches[0]["source_playlist_id"] is None


def test_dispatch_playlist_group_ref_is_stored_cleaned():
    """Gate pass 6: the stamped source_playlist_id is the CLEANED
    ref — a padded ' 908622995 ' is stored as '908622995', the same
    value every downstream cleaner would compute."""
    from core.wishlist.processing import _run_wishlist_cycle

    runtime = _dispatch_runtime()
    _run_wishlist_cycle(
        runtime, playlist_id="wishlist", cycle="singles",
        tracks=[_dispatch_track("sp-1", " 908622995 ")],
        run_id="run-1", auto_initiated=False,
    )

    batches = list(runtime.download_batches.values())
    assert len(batches) == 1
    assert batches[0]["source_playlist_id"] == "908622995"
