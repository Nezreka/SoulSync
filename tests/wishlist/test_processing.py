from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.wishlist import processing


# ---------------------------------------------------------------------------
# _resolve_album_bundle_threshold — config-driven wishlist bundle threshold
# ---------------------------------------------------------------------------


def test_resolve_album_bundle_threshold_uses_default_when_config_missing():
    """Default 2 matches the bumped ``min_tracks_per_album`` floor in
    ``group_wishlist_tracks_by_album`` — single-track wishlist items
    take the per-track path, multi-track items take the bundle path."""
    with patch('core.settings.config_manager') as cm:
        cm.get.return_value = processing._DEFAULT_ALBUM_BUNDLE_MIN_TRACKS
        assert processing._resolve_album_bundle_threshold() == 2


def test_resolve_album_bundle_threshold_honors_config_override():
    """Power users who want bundle for every wishlist track (old
    behaviour) can set the key to 1. Users who want bundle only on
    bigger gaps can set higher."""
    with patch('core.settings.config_manager') as cm:
        cm.get.return_value = 1
        assert processing._resolve_album_bundle_threshold() == 1
        cm.get.return_value = 5
        assert processing._resolve_album_bundle_threshold() == 5


def test_resolve_album_bundle_threshold_falls_back_on_garbage():
    """Non-numeric / non-positive / config-manager-raise all fall back
    to the safe default. Same defensive shape as get_poll_interval /
    get_transient_miss_threshold."""
    with patch('core.settings.config_manager') as cm:
        cm.get.return_value = 'oops'
        assert processing._resolve_album_bundle_threshold() == 2
        cm.get.return_value = 0
        assert processing._resolve_album_bundle_threshold() == 2
        cm.get.return_value = -3
        assert processing._resolve_album_bundle_threshold() == 2
        cm.get.side_effect = RuntimeError('boom')
        assert processing._resolve_album_bundle_threshold() == 2


class _FakeLogger:
    def __init__(self):
        self.errors = []
        self.infos = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(msg)

    def info(self, msg):
        self.infos.append(msg)

    def warning(self, msg):
        self.warnings.append(msg)


class _FakeAutomationEngine:
    def __init__(self):
        self.events = []

    def emit(self, name, payload):
        self.events.append((name, payload))


class _FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))


class _FakeConnection:
    def __init__(self):
        self.cursor_obj = _FakeCursor()
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


class _FakeDB:
    def __init__(self):
        self.connection = _FakeConnection()

    @contextmanager
    def _get_connection(self):
        yield self.connection


class _FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_remove_completed_tracks_from_wishlist_calls_remover():
    batch = {"queue": ["a", "b"]}
    download_tasks = {
        "a": {"status": "completed", "track_info": {"name": "Song A"},
              "final_file_path": "/library/Artist/Album/01.mp3"},
        "b": {"status": "failed", "track_info": {"name": "Song B"}},
    }
    calls = []

    removed = processing.remove_completed_tracks_from_wishlist(
        batch,
        download_tasks,
        lambda context, **kw: calls.append((context, kw)),
        logger=_FakeLogger(),
    )

    assert removed == 1
    # #1289: the remover is handed where the import actually landed, so its
    # guard can tell a published track from one still in staging or still in
    # the downloads folder.
    assert calls == [(
        {"track_info": {"name": "Song A"}, "original_search_result": {"name": "Song A"}},
        {"published_path": "/library/Artist/Album/01.mp3", "quiet_refusal": True},
    )]


def test_add_cancelled_tracks_to_failed_tracks_builds_entries():
    batch = {"queue": ["a"], "cancelled_tracks": {1}}
    download_tasks = {
        "a": {
            "status": "cancelled",
            "track_index": 1,
            "track_info": {"name": "Song A", "artist": "Artist A", "artists": [{"name": "Artist A"}]},
            "cached_candidates": [{"title": "candidate"}],
        }
    }
    failed = []

    processed = processing.add_cancelled_tracks_to_failed_tracks(
        batch,
        download_tasks,
        failed,
        logger=_FakeLogger(),
    )

    assert processed == 1
    assert failed[0]["track_name"] == "Song A"
    assert failed[0]["artist_name"] == "Artist A"
    assert failed[0]["failure_reason"] == "Download cancelled"


def test_resolve_wishlist_source_type_for_batch_returns_album_for_album_batch():
    assert processing.resolve_wishlist_source_type_for_batch({"is_album_download": True}) == "album"


def test_resolve_wishlist_source_type_for_batch_returns_playlist_otherwise():
    assert processing.resolve_wishlist_source_type_for_batch({}) == "playlist"
    assert processing.resolve_wishlist_source_type_for_batch({"is_album_download": False}) == "playlist"


def test_build_wishlist_source_context_minimal_batch_skips_album_provenance():
    """Non-album batches must not carry album_context (would mislead the
    requeue logic into routing single-track sync failures through
    album_path)."""
    batch = {
        "playlist_name": "My Mix",
        "playlist_id": "pl-99",
    }

    context = processing.build_wishlist_source_context(batch)

    assert context["playlist_name"] == "My Mix"
    assert context["playlist_id"] == "pl-99"
    assert context["added_from"] == "webui_modal"
    assert "is_album_download" not in context
    assert "album_context" not in context
    assert "artist_context" not in context


def test_build_wishlist_source_context_uses_source_playlist_ref_for_organize_batches():
    batch = {
        "playlist_name": "Summer Mix",
        "playlist_id": "42",
        "source_playlist_ref": "spotifyPlaylistId123",
        "mirrored_playlist_id": 42,
        "organize_by_playlist": True,
    }

    context = processing.build_wishlist_source_context(batch)

    assert context["playlist_id"] == "spotifyPlaylistId123"
    assert context["mirrored_playlist_id"] == 42
    assert context["organize_by_playlist"] is True


def test_build_wishlist_source_context_preserves_album_context_for_album_batches():
    """Album batches must carry album_context/artist_context through to the
    wishlist row so a later requeue has authoritative routing data instead
    of having to reconstruct it from per-track album dicts."""
    batch = {
        "playlist_name": "Album: GNX",
        "playlist_id": "library_redownload_abc",
        "is_album_download": True,
        "album_context": {"id": "alb-1", "name": "GNX", "total_tracks": 12},
        "artist_context": {"id": "art-1", "name": "Kendrick Lamar"},
    }

    context = processing.build_wishlist_source_context(batch)

    assert context["is_album_download"] is True
    assert context["album_context"] == {"id": "alb-1", "name": "GNX", "total_tracks": 12}
    assert context["artist_context"] == {"id": "art-1", "name": "Kendrick Lamar"}


_ALBUM_BATCH = {
    "playlist_name": "Wishlist (Album: Jam)",
    "playlist_id": "wishlist",
    "is_album_download": True,
    "album_context": {"id": "alb-1", "name": "Jam", "total_tracks": 10},
    "artist_context": {"id": "art-1", "name": "Some Artist"},
}


def test_requeue_provenance_keeps_playlist_origin_inside_an_album_batch():
    """#1981: a playlist-origin track that fails inside a wishlist album
    batch must be re-added AS a playlist track with its real playlist
    ref/name — the batch label ('Wishlist (Album: Jam)' / 'wishlist')
    overwriting the row is what later stops the playlist re-syncing."""
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "908622995"
    assert context["playlist_name"] == "Jam Skatin"
    # the batch's album flags are NOT kept alongside: they describe
    # the batch the track failed in, and derive_download_origin reads
    # source_info['is_album_download'] — keeping it would reclassify
    # this restored playlist track as a hand-picked download.
    assert "is_album_download" not in context
    assert "album_context" not in context
    assert "artist_context" not in context


def test_requeue_provenance_restored_row_derives_playlist_origin():
    """Gate pass 4: the restored row, fed through the real
    derive_download_origin as its source_info, must come back as a
    playlist download — the origin the #1981 tracks lose."""
    from core.downloads.origin import derive_download_origin

    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH, track)

    assert source_type == "playlist"
    origin, origin_context = derive_download_origin(
        {"track_info": {"source_info": context}})
    assert origin == "playlist"
    assert origin_context == "Jam Skatin"


def test_requeue_provenance_reads_a_json_string_source_info():
    track = {
        "source_type": "playlist",
        "source_info": '{"playlist_id": "dz-7", "playlist_name": "Road Trip"}',
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "dz-7"
    assert context["playlist_name"] == "Road Trip"


def test_requeue_provenance_name_only_when_the_ref_is_the_wishlist_sentinel():
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "wishlist", "playlist_name": "Jam Skatin"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH, track)

    assert source_type == "playlist"
    assert context["playlist_name"] == "Jam Skatin"
    # The dead key is REMOVED, not left holding the batch sentinel:
    # grouping/registration read the pair together and take the first
    # truthy value, so a leftover 'wishlist' ref would poison both.
    assert "playlist_id" not in context


def test_requeue_provenance_ref_only_drops_the_batch_label_name():
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995",
                        "playlist_name": "Wishlist (Auto - Album: Jam)"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "908622995"
    assert "playlist_name" not in context


@pytest.mark.parametrize("source_info", [
    pytest.param(
        {"playlist_id": " wishlist ", "playlist_name": "Wishlist "},
        id="padded-wishlist-sentinel"),
    pytest.param(
        {"playlist_id": "WISHLIST", "playlist_name": "wishlist (album: jam)"},
        id="casefolded-sentinel-and-label-name"),
    pytest.param(
        {"playlist_id": None, "playlist_name": "Unknown Playlist"},
        id="unknown-playlist-placeholder-name"),
    pytest.param(
        {"playlist_id": ["908622995"], "playlist_name": {"name": "Jam Skatin"}},
        id="non-scalar-ref-and-name"),
    pytest.param(
        {"playlist_id": True, "playlist_name": 42},
        id="bool-ref-and-int-name"),
])
def test_requeue_provenance_rejects_padded_and_placeholder_provenance(source_info):
    """Sentinels must not slip past with padding/case tricks, the
    'Unknown Playlist' placeholder is not a name, and non-string values
    are never stored verbatim."""
    source_type, context = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH,
        {"source_type": "playlist", "source_info": source_info})

    assert source_type == "album"
    assert context["playlist_name"] == "Wishlist (Album: Jam)"
    assert context["playlist_id"] == "wishlist"


def test_requeue_provenance_keeps_a_real_ref_for_a_wishlist_named_playlist():
    """Documented trade-off: a playlist genuinely named 'Wishlist
    (2024)' is indistinguishable from a batch label, so its name is not
    preserved — but its real ref is, and registration resolves by ref."""
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995",
                        "playlist_name": "Wishlist (2024)"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "908622995"
    assert "playlist_name" not in context


def test_requeue_provenance_int_ref_is_preserved_as_an_int():
    """A positive int ref is stored AS an int, not stringified:
    register_completed_playlist_batch treats an int ref as a
    mirrored-playlist PK and a digit string as an upstream id, so the
    string form would resolve to a different (or no) playlist."""
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": 908622995, "playlist_name": "Jam Skatin"},
    }

    _, context = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH, track)

    assert context["playlist_id"] == 908622995
    assert isinstance(context["playlist_id"], int)


def test_requeue_provenance_survives_a_pathological_json_source_info():
    """A deeply nested JSON string raises RecursionError in json.loads;
    that must degrade to the batch pair, not skip the re-add."""
    track = {
        "source_type": "playlist",
        "source_info": "[" * 3000 + "]" * 3000,
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH, track)

    assert source_type == "album"
    assert context["playlist_id"] == "wishlist"


def test_requeue_provenance_does_not_let_a_track_override_a_playlist_batch():
    """A playlist-named residual batch carries a FRESH resolution from
    dispatch (grouped by playlist, mirrored id resolved then); a track's
    older stored provenance must not override it, and the track's stale
    mirrored id never replaces the batch's fresher one."""
    playlist_batch = {
        "playlist_id": "wishlist",
        "playlist_name": "Jam Skatin",
        "is_playlist_batch": True,
        "source_playlist_id": "908622995",
        "mirrored_playlist_id": 7,
    }
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "stale-1", "playlist_name": "Old Name",
                        "mirrored_playlist_id": 3},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        playlist_batch, track)

    assert source_type == "playlist"
    assert context["playlist_name"] == "Jam Skatin"
    # The batch's real ref must reach the context: the builder reads
    # source_playlist_ref / playlist_id only, so without the resolver
    # writing source_playlist_id in, this was the sentinel 'wishlist'
    # beside the real name — the poisoned pairing.
    assert context["playlist_id"] == "908622995"
    assert context["mirrored_playlist_id"] == 7


def test_requeue_provenance_residual_batch_recovers_its_source_playlist_id():
    """Dedicated pin for the residual-batch shape: dispatch sets ONLY
    source_playlist_id (scanned from the grouped tracks' source_info)
    on a playlist-named batch whose playlist_id is the 'wishlist'
    sentinel. Batch-wins must requeue with that cleaned ref, not the
    sentinel paired with the batch's real name."""
    residual_batch = {
        "playlist_id": "wishlist",
        "playlist_name": "Jam Skatin",
        "is_playlist_batch": True,
        "source_playlist_name": "Jam Skatin",
        "source_playlist_id": "908622995",
    }
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "stale-1", "playlist_name": "Old Name"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        residual_batch, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "908622995"
    assert context["playlist_name"] == "Jam Skatin"


def test_requeue_provenance_batch_with_only_a_real_source_ref_suppresses():
    """Organize-download shape: the batch sets ONLY source_playlist_ref
    (no is_playlist_batch / source_playlist_id). A real ref is a fresh
    resolution, so the batch pair stands over a track's older one."""
    organize_batch = {
        "playlist_name": "Summer Mix",
        "playlist_id": "42",
        "source_playlist_ref": "spotifyPlaylistId123",
        "mirrored_playlist_id": 42,
        "organize_by_playlist": True,
    }
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        organize_batch, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "spotifyPlaylistId123"
    assert context["playlist_name"] == "Summer Mix"
    assert context["mirrored_playlist_id"] == 42


def test_requeue_provenance_sentinel_source_ref_does_not_suppress():
    """A batch whose source_playlist_ref is only the 'wishlist'
    sentinel carries no fresh resolution — the cleaner turns it into
    nothing — so a track's legit provenance must still win."""
    batch = {**_ALBUM_BATCH, "source_playlist_ref": "wishlist"}
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        batch, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "908622995"
    assert context["playlist_name"] == "Jam Skatin"


def test_requeue_provenance_direct_playlist_batch_ref_suppresses():
    """Direct playlist download shape (start-missing-process for a
    playlist the user opened): the batch's ONLY playlist ref is
    playlist_id — no source_playlist_ref, no is_playlist_batch. That
    is still a fresh batch resolution, so a track's older provenance
    must not override the batch's own playlist."""
    direct_batch = {
        "playlist_id": "908622995",
        "playlist_name": "Jam Skatin",
    }
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "stale-1", "playlist_name": "Old Name"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        direct_batch, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "908622995"
    assert context["playlist_name"] == "Jam Skatin"


def test_requeue_provenance_synthetic_batch_playlist_id_does_not_suppress():
    """Hand-picked downloads park a MADE-UP id in playlist_id (an
    album/song picked by hand, a library redownload, the playback
    queue). Those name no playlist, so they are not a fresh batch
    resolution — a playlist track's real provenance still wins, the
    same #1981 shape as the 'wishlist' sentinel album batch."""
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin"},
    }
    for synthetic_id in ("artist_album_55", "enhanced_search_9",
                         "redownload_12", "playback_queue"):
        batch = {**_ALBUM_BATCH, "playlist_id": synthetic_id}

        source_type, context = processing.resolve_failed_track_requeue_provenance(
            batch, track)

        assert source_type == "playlist", synthetic_id
        assert context["playlist_id"] == "908622995", synthetic_id
        assert context["playlist_name"] == "Jam Skatin", synthetic_id


@pytest.mark.parametrize("synthetic_id", [
    pytest.param("beatport_release_1700000000000_ab12cd", id="beatport-release"),
    pytest.param("lbl_album_sp-1", id="label-release-modal"),
    pytest.param("resync_42", id="history-resync-fallback"),
    pytest.param("resync_album_42", id="history-resync-album-context"),
    pytest.param("ituneslink_ab12cd", id="ituneslink-state-key"),
    pytest.param("legacy_modal", id="legacy-modal-endpoint"),
    pytest.param("spotify_library_42", id="spotify-library-album"),
    pytest.param("issue_download_7", id="issue-download-album"),
    pytest.param("discover_cache_9", id="discover-cache-album"),
    pytest.param("wing_it_1700000000000", id="wing-it-virtual-playlist"),
    pytest.param("library_v2_search", id="library-v2-search"),
])
def test_requeue_provenance_new_synthetic_batch_ids_do_not_suppress(synthetic_id):
    """Gate pass 3: the deny-list missed server/webui-made album ids.
    These batches are NOT album-flagged (the label modal's id never
    trips the client's isAlbumContextPlaylistId list, so its batch
    arrives with is_album_download unset), so the prefix list itself
    must catch them — each names no playlist, so a playlist track's
    real provenance still wins."""
    batch = {"playlist_id": synthetic_id, "playlist_name": "Some Album"}
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        batch, track)

    assert source_type == "playlist", synthetic_id
    assert context["playlist_id"] == "908622995", synthetic_id
    assert context["playlist_name"] == "Jam Skatin", synthetic_id


def test_requeue_provenance_album_batch_real_playlist_id_does_not_suppress():
    """The is_album_download gate: an album batch's playlist_id never
    counts as a fresh playlist resolution, even when it is a real-
    looking id no deny-list could enumerate (a per-release id, a
    history-entry id). is_hand_picked makes the same call for origin
    derivation. The same id on a NON-album batch still suppresses —
    pinned by test_requeue_provenance_direct_playlist_batch_ref_suppresses."""
    batch = {**_ALBUM_BATCH, "playlist_id": "908622995"}
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "777", "playlist_name": "Other Mix"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        batch, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "777"
    assert context["playlist_name"] == "Other Mix"
    # the track override won, so the batch's album flag is dropped
    # with it (gate pass 4: derive_download_origin would otherwise
    # reclassify the restored track as hand-picked)
    assert "is_album_download" not in context


def test_requeue_provenance_residual_batch_keeps_its_own_wishlist_looking_name():
    """Gate pass 3: a real playlist named 'Wishlist (2024)' groups its
    residual tracks under that name, and dispatch stamps the name on
    the batch as source_playlist_name — the batch's OWN name, taken
    verbatim from the tracks' provenance, not a generated label. With
    a usable batch ref the name must survive beside it: popping it
    (the batch-label cleaning) requeued the row ref-only, and the
    next cycle's name-only grouping lost the playlist-named batch."""
    residual_batch = {
        "playlist_id": "wishlist",
        "playlist_name": "Wishlist (2024)",
        "is_playlist_batch": True,
        "source_playlist_name": "Wishlist (2024)",
        "source_playlist_id": "908622995",
        "mirrored_playlist_id": 7,
    }
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "stale-1", "playlist_name": "Old Name"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        residual_batch, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "908622995"
    assert context["playlist_name"] == "Wishlist (2024)"
    assert context["mirrored_playlist_id"] == 7


def test_requeue_provenance_residual_batch_name_only_keeps_its_own_wishlist_looking_name():
    """The name-only half of the same pin: no usable batch ref, but
    the batch's own 'Wishlist (2024)' name is still a fresh resolution
    (dispatch resolved the mirrored id by it), so the batch pair
    stands — name kept, dead ref popped, mirrored id carried."""
    residual_batch = {
        "playlist_id": "wishlist",
        "playlist_name": "Wishlist (2024)",
        "is_playlist_batch": True,
        "source_playlist_name": "Wishlist (2024)",
        "source_playlist_id": None,
        "mirrored_playlist_id": 7,
    }
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "stale-1", "playlist_name": "Old Name"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        residual_batch, track)

    assert source_type == "playlist"
    assert context["playlist_name"] == "Wishlist (2024)"
    assert "playlist_id" not in context
    assert context["mirrored_playlist_id"] == 7


def test_requeue_provenance_batch_repair_applies_to_non_playlist_tracks():
    """Gate pass 3: the batch-side repair describes the BATCH, so it
    applies to every track in it. A track whose payload carries no
    source_type (its provenance was lost upstream, or it never had
    any) must not requeue with the builder's unrepaired pair — the
    'wishlist' sentinel beside the real name, where its playlist
    batch-mates requeue with the batch's real ref — nor with the
    sentinel left beside the name in the name-only shape."""
    residual_batch = {
        "playlist_id": "wishlist",
        "playlist_name": "Jam Skatin",
        "is_playlist_batch": True,
        "source_playlist_name": "Jam Skatin",
        "source_playlist_id": "908622995",
        "mirrored_playlist_id": 7,
    }
    plain_track = {"id": "sp-1", "name": "Plain Song"}

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        residual_batch, plain_track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "908622995"
    assert context["playlist_name"] == "Jam Skatin"
    assert context["mirrored_playlist_id"] == 7

    name_only_batch = {**residual_batch, "source_playlist_id": None}

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        name_only_batch, plain_track)

    assert source_type == "playlist"
    assert context["playlist_name"] == "Jam Skatin"
    assert "playlist_id" not in context
    assert context["mirrored_playlist_id"] == 7


def test_requeue_provenance_batch_ref_with_a_label_name_pops_the_name():
    """Batch-wins name cleaning: a batch with a real ref but only a
    placeholder / batch-label name must not store the 'real ref +
    batch label' pairing — the unusable name is popped (all-or-clean),
    mirroring the name-only branch's ref pop. A usable batch name
    stands (pinned by the residual / organize batch tests above)."""
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "stale-1", "playlist_name": "Old Name"},
    }
    for batch in (
        {**_ALBUM_BATCH, "source_playlist_ref": "dz-9"},
        {"playlist_id": "wishlist", "playlist_name": "Unknown Playlist",
         "source_playlist_ref": "dz-9"},
    ):
        source_type, context = processing.resolve_failed_track_requeue_provenance(
            batch, track)

        assert context["playlist_id"] == "dz-9"
        assert "playlist_name" not in context


def test_requeue_provenance_source_playlist_ref_wins_over_source_playlist_id():
    """A batch carrying BOTH source fields: source_playlist_ref wins
    in the stored context. build_wishlist_source_context prefers
    source_playlist_ref for the context ref, and the resolver only
    REPAIRS an unusable built ref — with the first cleaned candidate,
    source_playlist_id — so a usable source_playlist_ref stands and
    source_playlist_id is the fallback, not the winner."""
    batch = {
        "playlist_id": "wishlist",
        "playlist_name": "Jam Skatin",
        "is_playlist_batch": True,
        "source_playlist_id": "dz-111",
        "source_playlist_ref": "sp-222",
        "mirrored_playlist_id": 7,
    }
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "stale-1", "playlist_name": "Old Name"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        batch, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "sp-222"
    assert context["playlist_name"] == "Jam Skatin"
    assert context["mirrored_playlist_id"] == 7


def test_requeue_provenance_wishlist_manual_sentinel_on_the_track_side():
    """'wishlist_manual' is in the sentinel set alongside 'wishlist':
    as a track's ref it cleans to nothing, so a real name survives as
    a name-only row and the dead key is removed."""
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "wishlist_manual",
                        "playlist_name": "Jam Skatin"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH, track)

    assert source_type == "playlist"
    assert context["playlist_name"] == "Jam Skatin"
    assert "playlist_id" not in context


def test_requeue_provenance_wishlist_manual_sentinel_on_the_batch_side():
    """The sentinel set applies to the batch-ref candidates too: a
    'wishlist_manual' source_playlist_ref or playlist_id carries no
    fresh resolution, so the track's provenance still wins."""
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin"},
    }
    for batch in (
        {**_ALBUM_BATCH, "source_playlist_ref": "wishlist_manual"},
        {"playlist_id": "wishlist_manual",
         "playlist_name": "Wishlist (Residual)"},
    ):
        source_type, context = processing.resolve_failed_track_requeue_provenance(
            batch, track)

        assert source_type == "playlist"
        assert context["playlist_id"] == "908622995"
        assert context["playlist_name"] == "Jam Skatin"


def test_requeue_provenance_json_source_info_decoding_to_a_non_dict_falls_back():
    """A JSON-string source_info that decodes to a list or a number
    carries no playlist pair — the track falls back to the batch pair
    instead of crashing or storing the decoded scalar."""
    for raw in ("[1, 2, 3]", "42"):
        track = {"source_type": "playlist", "source_info": raw}

        source_type, context = processing.resolve_failed_track_requeue_provenance(
            _ALBUM_BATCH, track)

        assert source_type == "album", raw
        assert context["playlist_name"] == "Wishlist (Album: Jam)", raw
        assert context["playlist_id"] == "wishlist", raw


def test_requeue_provenance_playlist_batch_with_usable_name_only_suppresses():
    """is_playlist_batch with source_playlist_id=None but a usable
    source_playlist_name is still a fresh resolution: at dispatch the
    mirrored id was resolved by that name (it is on the batch), and
    build_wishlist_source_context carries the batch's playlist_name —
    the same name — as the context name, so the batch pair stands.
    Playlist_id pin: no usable batch ref exists, so the dead key is
    REMOVED rather than left holding the 'wishlist' sentinel beside
    the real name (all-or-clean, same as the track path); the name
    and the mirrored id carry the resolution."""
    playlist_batch = {
        "playlist_id": "wishlist",
        "playlist_name": "Jam Skatin",
        "is_playlist_batch": True,
        "source_playlist_id": None,
        "source_playlist_name": "Jam Skatin",
        "mirrored_playlist_id": 7,
    }
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "stale-1", "playlist_name": "Old Name"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        playlist_batch, track)

    assert source_type == "playlist"
    assert context["playlist_name"] == "Jam Skatin"
    assert "playlist_id" not in context
    assert context["mirrored_playlist_id"] == 7


def test_requeue_provenance_playlist_batch_without_usable_id_or_name_falls_through():
    """The other half of the name-only scoping: a playlist batch whose
    id is None and whose names are only batch labels has no usable
    fresh resolution, so the track's own provenance wins instead."""
    playlist_batch = {
        "playlist_id": "wishlist",
        "playlist_name": "Wishlist (Residual)",
        "is_playlist_batch": True,
        "source_playlist_id": None,
        "source_playlist_name": "Wishlist (Residual)",
    }
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        playlist_batch, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "908622995"
    assert context["playlist_name"] == "Jam Skatin"


def test_requeue_provenance_auto_generic_label_is_not_a_batch_own_name():
    """The auto cycle's generated generic label ('Wishlist (Auto -
    Singles)') is the other name dispatch stamps on the non-playlist
    residual group: like 'Wishlist (Residual)' it is never a batch's
    own playlist name, even on a batch flagged is_playlist_batch, so
    the track's own provenance wins."""
    playlist_batch = {
        "playlist_id": "wishlist",
        "playlist_name": "Wishlist (Auto - Singles)",
        "is_playlist_batch": True,
        "source_playlist_id": None,
        "source_playlist_name": "Wishlist (Auto - Singles)",
    }
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        playlist_batch, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "908622995"
    assert context["playlist_name"] == "Jam Skatin"


def test_requeue_provenance_album_label_is_not_a_batch_own_name():
    """Gate pass 4: rows stored before the batch-type split can carry
    the album-batch label AS their playlist_name with source_type
    'playlist', so residual grouping groups them under 'Wishlist
    (Album: Jam)' and flags the batch is_playlist_batch. The label is
    still a generated label, never the batch's own playlist name —
    the track's real provenance must win."""
    playlist_batch = {
        "playlist_id": "wishlist",
        "playlist_name": "Wishlist (Album: Jam)",
        "is_playlist_batch": True,
        "source_playlist_id": None,
        "source_playlist_name": "Wishlist (Album: Jam)",
    }
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        playlist_batch, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "908622995"
    assert context["playlist_name"] == "Jam Skatin"


def test_requeue_provenance_direct_batch_keeps_a_wishlist_looking_real_name():
    """Gate pass 5: a DIRECT playlist download batch (not a wishlist
    cycle batch) whose playlist is really named 'Wishlist (2024)'
    carries its name and ref together from the same download origin —
    the pair is corroborated, so the name stands beside the ref. Only
    GENERATED labels are popped in the batch-ref branch. Dropping it
    requeued every failed track ref-only, and the next cycle's
    name-only grouping lost the playlist-named batch."""
    batch = {"playlist_id": "37i9dQZF1DX", "playlist_name": "Wishlist (2024)"}
    track = {"source_type": "single", "source_info": {}}

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        batch, track)

    assert context["playlist_id"] == "37i9dQZF1DX"
    assert context["playlist_name"] == "Wishlist (2024)"


def test_requeue_provenance_repair_replaces_a_synthetic_context_ref():
    """Gate pass 6: the batch-ref repair must judge the built
    context's ref by the direct-batch standard — a synthetic id the
    builder took from batch['playlist_id'] is not a usable ref, so
    the batch's real source_playlist_id replaces it instead of being
    stored beside the real name."""
    batch = {
        "playlist_id": "enhanced_search_9",
        "playlist_name": "Jam Skatin",
        "is_playlist_batch": True,
        "source_playlist_id": "908622995",
        "source_playlist_name": "Jam Skatin",
    }
    track = {"source_type": "single", "source_info": {}}

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        batch, track)

    assert context["playlist_id"] == "908622995"
    assert context["playlist_name"] == "Jam Skatin"


def test_requeue_provenance_none_batch_name_leaves_no_dead_key():
    """Gate pass 6: a batch with an explicit playlist_name of None
    and a real ref requeues with NO playlist_name key — a stored
    None is a dead key, not a name."""
    batch = {
        "playlist_id": "wishlist",
        "playlist_name": None,
        "source_playlist_ref": "dz-9",
    }
    track = {"source_type": "single", "source_info": {}}

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        batch, track)

    assert context["playlist_id"] == "dz-9"
    assert "playlist_name" not in context


def test_requeue_provenance_name_only_branch_stores_the_checked_name():
    """Gate pass 6: the name-only branch is entered on
    source_playlist_name, so when batch['playlist_name'] (what the
    builder stores) differs and carries nothing real, the checked
    name is stored instead of the placeholder."""
    batch = {
        "playlist_id": "wishlist",
        "playlist_name": "Unknown Playlist",
        "is_playlist_batch": True,
        "source_playlist_id": None,
        "source_playlist_name": "Jam Skatin",
        "mirrored_playlist_id": 7,
    }
    track = {"source_type": "single", "source_info": {}}

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        batch, track)

    assert context["playlist_name"] == "Jam Skatin"
    assert "playlist_id" not in context


def test_requeue_provenance_name_only_branch_pops_a_synthetic_context_ref():
    """Gate pass 7: the name-only branch judges the built context's
    ref by the same direct-batch standard as the batch-ref branch —
    a synthetic batch playlist_id is not a usable ref and is popped,
    not stored beside the real name."""
    batch = {
        "playlist_id": "enhanced_search_9",
        "playlist_name": "Jam Skatin",
        "is_playlist_batch": True,
        "source_playlist_id": None,
        "source_playlist_name": "Jam Skatin",
        "mirrored_playlist_id": 7,
    }
    track = {"source_type": "single", "source_info": {}}

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        batch, track)

    assert context["playlist_name"] == "Jam Skatin"
    assert "playlist_id" not in context


def test_requeue_provenance_name_only_without_a_mirror_does_not_suppress():
    """Gate pass 7: a name-only batch with NO mirrored id behind it
    is not a fresh resolution — the track's own ref must survive
    instead of being suppressed by the bare name."""
    batch = {
        "playlist_id": "wishlist",
        "playlist_name": "Jam Skatin",
        "is_playlist_batch": True,
        "source_playlist_id": None,
        "source_playlist_name": "Jam Skatin",
        "mirrored_playlist_id": None,
    }
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908", "playlist_name": "Jam Skatin"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        batch, track)

    assert source_type == "playlist"
    assert context["playlist_id"] == "908"
    assert context["playlist_name"] == "Jam Skatin"


def test_requeue_provenance_album_batch_source_ref_replaces_the_album_id():
    """Gate pass 7: for an album batch the builder's ref comes from
    the batch's own playlist_id, which never counts — a real
    source_playlist_id replaces it even when the album id is not a
    synthetic shape any deny-list could enumerate."""
    batch = {**_ALBUM_BATCH, "playlist_id": "alb-777",
             "source_playlist_id": "908"}
    track = {"source_type": "single", "source_info": {}}

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        batch, track)

    assert context["playlist_id"] == "908"


def test_requeue_provenance_batch_name_is_stored_stripped():
    """Gate pass 7: a padded batch name is stored stripped, like
    track-side names — the next cycle groups by the exact text."""
    batch = {"playlist_id": "dz-9", "playlist_name": " Jam Skatin "}
    track = {"source_type": "single", "source_info": {}}

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        batch, track)

    assert context["playlist_id"] == "dz-9"
    assert context["playlist_name"] == "Jam Skatin"


def test_requeue_provenance_rejects_non_positive_int_refs():
    """0 and negative ints are never a real playlist id — they must
    clean to nothing instead of being stored as the refs '0' / '-5'."""
    name_only = {
        "source_type": "playlist",
        "source_info": {"playlist_id": 0, "playlist_name": "Jam Skatin"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH, name_only)

    assert source_type == "playlist"
    assert context["playlist_name"] == "Jam Skatin"
    assert "playlist_id" not in context

    nothing_real = {
        "source_type": "playlist",
        "source_info": {"playlist_id": -5,
                        "playlist_name": "Wishlist (Album: Jam)"},
    }

    source_type, context = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH, nothing_real)

    assert source_type == "album"
    assert context["playlist_name"] == "Wishlist (Album: Jam)"
    assert context["playlist_id"] == "wishlist"


def test_requeue_provenance_ignores_a_tracks_stale_mirrored_id():
    """Nothing reads mirrored_playlist_id back from a wishlist row, so
    the resolver does not copy the track's (possibly stale) value —
    and when the track's own provenance wins, the BATCH's mirrored id
    is dropped too: it was resolved for the batch's playlist, so
    keeping it would pair the track's ref/name with a different
    playlist's mirror."""
    track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin",
                        "mirrored_playlist_id": 3},
    }

    _, context = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH, track)
    assert "mirrored_playlist_id" not in context

    _, context = processing.resolve_failed_track_requeue_provenance(
        {**_ALBUM_BATCH, "mirrored_playlist_id": 7}, track)
    assert "mirrored_playlist_id" not in context


def test_requeue_provenance_shared_batch_context_is_copied_per_track():
    """Callers may build the batch context once for a whole batch; one
    track's preserved provenance must not leak into the next track's."""
    batch_context = processing.build_wishlist_source_context(_ALBUM_BATCH)
    playlist_track = {
        "source_type": "playlist",
        "source_info": {"playlist_id": "908622995", "playlist_name": "Jam Skatin"},
    }

    _, first = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH, playlist_track, batch_context=batch_context)
    _, second = processing.resolve_failed_track_requeue_provenance(
        _ALBUM_BATCH, {"id": "sp-1"}, batch_context=batch_context)

    assert first["playlist_id"] == "908622995"
    assert second["playlist_id"] == "wishlist"
    assert batch_context["playlist_id"] == "wishlist"


def test_requeue_provenance_falls_back_to_the_batch_pair_without_track_provenance():
    """Tracks that did not come from a playlist keep today's behaviour:
    an album batch's failures requeue as 'album' with the batch context."""
    for track in (
        {"id": "sp-1", "name": "Plain"},
        {"source_type": "album", "source_info": {"playlist_name": "Jam Skatin"}},
        {"source_type": "playlist",
         "source_info": {"playlist_id": "wishlist",
                         "playlist_name": "Wishlist (Album: Jam)"}},
        None,
        "not-a-dict",
    ):
        source_type, context = processing.resolve_failed_track_requeue_provenance(
            _ALBUM_BATCH, track)

        assert source_type == "album"
        assert context["playlist_name"] == "Wishlist (Album: Jam)"
        assert context["playlist_id"] == "wishlist"


def test_recover_uncaptured_failed_tracks_builds_entries():
    batch = {"queue": ["a"]}
    download_tasks = {
        "a": {
            "status": "failed",
            "track_index": 2,
            "track_info": {"name": "Song B", "artist": "Artist B", "artists": [{"name": "Artist B"}]},
            "retry_count": 3,
            "error_message": "boom",
            "cached_candidates": [],
        }
    }
    failed = []

    recovered = processing.recover_uncaptured_failed_tracks(
        batch,
        download_tasks,
        failed,
        logger=_FakeLogger(),
    )

    assert recovered == 1
    assert failed[0]["track_name"] == "Song B"
    assert failed[0]["retry_count"] == 3
    assert failed[0]["failure_reason"] == "boom"


def test_finalize_auto_wishlist_completion_defers_toggle_when_siblings_active():
    """When the completing batch shares a ``wishlist_run_id`` with
    siblings still in pre-terminal phases, finalize must NOT toggle
    the cycle yet — that only happens when the LAST sibling done.
    Pinned to prevent the regression where every sub-batch's
    completion fired its own cycle toggle (Phase 1c.2.1 split path)."""
    db = _FakeDB()
    automation_engine = _FakeAutomationEngine()
    resets = []
    activities = []
    summary = {"tracks_added": 1, "total_failed": 1, "errors": 0}

    # Two sub-batches share the same run id. The first finishes,
    # the second is still 'analysis'.
    download_batches = {
        "batch-A": {"current_cycle": "albums", "wishlist_run_id": "run-1", "phase": "complete"},
        "batch-B": {"current_cycle": "albums", "wishlist_run_id": "run-1", "phase": "analysis"},
    }

    processing.finalize_auto_wishlist_completion(
        "batch-A",
        summary,
        download_batches=download_batches,
        tasks_lock=_FakeLock(),
        reset_processing_state=lambda: resets.append(True),
        add_activity_item=lambda *args: activities.append(args),
        automation_engine=automation_engine,
        db_factory=lambda: db,
        logger=_FakeLogger(),
    )

    # Activity log still fires (it's a per-batch record), but cycle
    # toggle + state reset + automation emit are deferred.
    assert activities == [("", "Wishlist Updated", "1 failed tracks added to wishlist", "Now")]
    assert resets == []  # NOT reset yet — siblings still active
    assert automation_engine.events == []  # NOT emitted yet
    assert db.connection.cursor_obj.calls == []  # DB cycle-toggle NOT written


def test_finalize_auto_wishlist_completion_toggles_when_last_sibling_done():
    """When all siblings of the same run are in terminal phases (or
    don't exist), the completing batch IS the last → cycle toggles
    + state resets + automation event fires."""
    db = _FakeDB()
    automation_engine = _FakeAutomationEngine()
    resets = []
    summary = {"tracks_added": 1, "total_failed": 1, "errors": 0}

    download_batches = {
        # Both siblings already terminal — current batch is the last.
        "batch-A": {"current_cycle": "albums", "wishlist_run_id": "run-1", "phase": "complete"},
        "batch-B": {"current_cycle": "albums", "wishlist_run_id": "run-1", "phase": "complete"},
        "batch-C": {"current_cycle": "albums", "wishlist_run_id": "run-1", "phase": "analysis"},  # the completing one
    }

    processing.finalize_auto_wishlist_completion(
        "batch-C",
        summary,
        download_batches=download_batches,
        tasks_lock=_FakeLock(),
        reset_processing_state=lambda: resets.append(True),
        add_activity_item=lambda *_a: None,
        automation_engine=automation_engine,
        db_factory=lambda: db,
        logger=_FakeLogger(),
    )

    assert resets == [True]
    assert db.connection.committed is True
    assert db.connection.cursor_obj.calls[0][1] == ("singles",)
    assert automation_engine.events  # event emitted


def test_finalize_playlist_scoped_run_leaves_global_cycle_unchanged():
    db = _FakeDB()
    resets = []
    summary = {"tracks_added": 0, "total_failed": 0, "errors": 0}

    processing.finalize_auto_wishlist_completion(
        "batch-scoped",
        summary,
        download_batches={
            "batch-scoped": {
                "current_cycle": "playlist",
                "wishlist_run_id": "run-scoped",
                "toggle_wishlist_cycle": False,
                "phase": "complete",
            },
        },
        tasks_lock=_FakeLock(),
        reset_processing_state=lambda: resets.append(True),
        add_activity_item=lambda *_args: None,
        automation_engine=None,
        db_factory=lambda: db,
        logger=_FakeLogger(),
    )

    assert resets == [True]
    assert db.connection.cursor_obj.calls == []
    assert db.connection.committed is False


def test_finalize_auto_wishlist_completion_legacy_no_run_id_toggles_immediately():
    """Back-compat: a batch with NO ``wishlist_run_id`` (legacy
    single-batch run from before Phase 1c.2.1) should keep firing
    the toggle on its own completion regardless of any unrelated
    batches in the dict."""
    db = _FakeDB()
    automation_engine = _FakeAutomationEngine()
    resets = []
    summary = {"tracks_added": 0, "total_failed": 0, "errors": 0}

    download_batches = {
        "batch-legacy": {"current_cycle": "albums"},  # no wishlist_run_id
        # Even with another unrelated batch active, legacy should toggle.
        "unrelated": {"current_cycle": "singles", "phase": "analysis"},
    }

    processing.finalize_auto_wishlist_completion(
        "batch-legacy",
        summary,
        download_batches=download_batches,
        tasks_lock=_FakeLock(),
        reset_processing_state=lambda: resets.append(True),
        add_activity_item=lambda *_a: None,
        automation_engine=automation_engine,
        db_factory=lambda: db,
        logger=_FakeLogger(),
    )

    assert resets == [True]
    assert db.connection.cursor_obj.calls[0][1] == ("singles",)


def test_finalize_auto_wishlist_completion_toggles_cycle_and_resets_state():
    db = _FakeDB()
    automation_engine = _FakeAutomationEngine()
    resets = []
    activities = []
    summary = {"tracks_added": 2, "total_failed": 5, "errors": 0}

    result = processing.finalize_auto_wishlist_completion(
        "batch-1",
        summary,
        download_batches={"batch-1": {"current_cycle": "albums"}},
        tasks_lock=_FakeLock(),
        reset_processing_state=lambda: resets.append(True),
        add_activity_item=lambda *args: activities.append(args),
        automation_engine=automation_engine,
        db_factory=lambda: db,
        logger=_FakeLogger(),
    )

    assert result is summary
    assert resets == [True]
    assert activities == [("", "Wishlist Updated", "2 failed tracks added to wishlist", "Now")]
    assert automation_engine.events == [
        (
            "wishlist_processing_completed",
            {
                "tracks_processed": "5",
                "tracks_found": "2",
                "tracks_failed": "3",
            },
        )
    ]
    assert db.connection.committed is True
    assert db.connection.cursor_obj.calls[0][1] == ("singles",)


def test_automatic_wishlist_cleanup_after_db_update_removes_library_matches():
    class _CleanupWishlistService:
        def __init__(self, tracks):
            self.tracks = tracks
            self.removed = []

        def get_wishlist_tracks_for_download(self, profile_id=1):
            return list(self.tracks)

        def mark_track_download_result(self, spotify_track_id, success, error_message=None, profile_id=1, **kwargs):
            self.removed.append((spotify_track_id, success, error_message, profile_id))
            return True

    class _CleanupProfilesDatabase:
        def get_all_profiles(self):
            return [{"id": 1}]

    class _CleanupMusicDatabase:
        def check_track_exists(self, track_name, artist_name, confidence_threshold=0.7, server_source=None, album=None, include_downloads=False):
            if track_name == "Song A" and artist_name == "Artist A":
                return SimpleNamespace(id='db-track', title=track_name,
                                       artist_name=artist_name, album_title=album,
                                       file_path='/music/owned.flac'), 0.9
            return None, 0.0

    wishlist_service = _CleanupWishlistService(
        [
            {
                "name": "Song A",
                "artists": [{"name": "Artist A"}],
                "spotify_track_id": "sp-1",
                "id": "sp-1",
                "album": {"name": "Album A"},
            },
            {
                "name": "Song B",
                "artists": [{"name": "Artist B"}],
                "spotify_track_id": "sp-2",
                "id": "sp-2",
                "album": {"name": "Album B"},
            },
        ]
    )

    removed = processing.automatic_wishlist_cleanup_after_db_update(
        wishlist_service=wishlist_service,
        profiles_database=_CleanupProfilesDatabase(),
        music_database=_CleanupMusicDatabase(),
        active_server="navidrome",
        logger=_FakeLogger(),
    )

    assert removed == 1
    assert wishlist_service.removed == [("sp-1", True, None, 1)]


def test_automatic_wishlist_cleanup_after_db_update_removes_manual_matches(monkeypatch):
    wishlist_service = _CleanupWishlistService(
        [
            {
                "name": "Manual Song",
                "artists": [{"name": "Artist A"}],
                "spotify_track_id": "sp-manual",
                "id": "sp-manual",
                "provider": "spotify",
                "album": {"name": "Album A"},
            },
        ]
    )
    music_db = _CleanupMusicDatabase()
    monkeypatch.setattr(
        "core.library.manual_library_match.get_match_for_track",
        lambda *_args, **_kwargs: {"id": 1, "library_track_id": 42},
    )

    removed = processing.automatic_wishlist_cleanup_after_db_update(
        wishlist_service=wishlist_service,
        profiles_database=_CleanupProfilesDatabase(),
        music_database=music_db,
        active_server="navidrome",
        logger=_FakeLogger(),
    )

    assert removed == 1
    assert wishlist_service.removed == [("sp-manual", True, None, 1)]
    assert music_db.track_checks == []


class _CleanupProfilesDatabase:
    def get_all_profiles(self):
        return [{"id": 1}]


class _CleanupWishlistService:
    def __init__(self, tracks):
        self._tracks = list(tracks)
        self.removed = []

    def get_wishlist_tracks_for_download(self, profile_id=1):
        return list(self._tracks)

    def mark_track_download_result(self, spotify_track_id, success, error_message=None, profile_id=1, **kwargs):
        self.removed.append((spotify_track_id, success, error_message, profile_id))
        return True


class _CleanupMusicDatabase:
    def __init__(self):
        self.track_checks = []

    def check_track_exists(self, track_name, artist_name, confidence_threshold=0.7, server_source=None, album=None, include_downloads=False):
        self.track_checks.append((track_name, artist_name, server_source, album))
        if track_name == "Owned Song" and artist_name == "Artist A":
            return SimpleNamespace(id='db-track', title=track_name,
                                   artist_name=artist_name, album_title=album,
                                   file_path='/music/owned.flac'), 0.9
        if track_name == "Broken Song":
            raise RuntimeError("boom")
        return None, 0.0


def test_remove_tracks_already_in_library_skips_enhance_tracks_and_handles_errors():
    wishlist_service = _CleanupWishlistService(
        [
            {
                "name": "Owned Song",
                "artists": ["Artist A"],
                "spotify_track_id": "sp-1",
                "album": {"name": "Album A"},
            },
            {
                "name": "Enhance Song",
                "artists": [{"name": "Artist B"}],
                "spotify_track_id": "sp-2",
                "source_type": "enhance",
                "album": {"name": "Album B"},
            },
            {
                "name": "Broken Song",
                "artists": [{"name": "Artist C"}],
                "spotify_track_id": "sp-3",
                "album": {"name": "Album C"},
            },
        ]
    )
    music_db = _CleanupMusicDatabase()

    removed = processing.remove_tracks_already_in_library(
        wishlist_service,
        _CleanupProfilesDatabase(),
        music_db,
        "navidrome",
        logger=_FakeLogger(),
        skip_track_fn=lambda track: track.get("source_type") == "enhance",
    )

    assert removed == 1
    assert wishlist_service.removed == [("sp-1", True, None, 1)]
    assert music_db.track_checks == [
        ("Owned Song", "Artist A", "navidrome", "Album A"),
        ("Broken Song", "Artist C", "navidrome", "Album C"),
    ]


def test_finalize_auto_wishlist_completion_with_no_tracks_added_still_resets_state():
    db = _FakeDB()
    automation_engine = _FakeAutomationEngine()
    resets = []
    activities = []
    summary = {"tracks_added": 0, "total_failed": 5, "errors": 0}

    result = processing.finalize_auto_wishlist_completion(
        "batch-2",
        summary,
        download_batches={"batch-2": {"current_cycle": "singles"}},
        tasks_lock=_FakeLock(),
        reset_processing_state=lambda: resets.append(True),
        add_activity_item=lambda *args: activities.append(args),
        automation_engine=automation_engine,
        db_factory=lambda: db,
        logger=_FakeLogger(),
    )

    assert result is summary
    assert resets == [True]
    assert activities == []
    assert automation_engine.events == [
        (
            "wishlist_processing_completed",
            {
                "tracks_processed": "5",
                "tracks_found": "0",
                "tracks_failed": "5",
            },
        )
    ]
    assert db.connection.committed is True



# ---------------------------------------------------------------------------
# jadux's stall: the auto cleanup must never stack — a request arriving while
# one is running is SKIPPED, and post-run the guard resets so the next DB
# update's cleanup runs normally.
# ---------------------------------------------------------------------------


def test_auto_cleanup_overlap_guard_skips_concurrent_and_resets(monkeypatch):
    import threading as _threading

    entered = _threading.Event()
    release = _threading.Event()
    calls = []

    def _slow_remove(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(timeout=10), "test deadlock: release never set"
        return 0

    monkeypatch.setattr(processing, "remove_tracks_already_in_library", _slow_remove)

    kwargs = dict(
        wishlist_service=_CleanupWishlistService([{  # one track so the inner body runs
            "name": "Song", "artists": [{"name": "A"}],
            "spotify_track_id": "sp-1", "album": {"name": "Al"},
        }]),
        profiles_database=_CleanupProfilesDatabase(),
        music_database=_CleanupMusicDatabase(),
        active_server="navidrome",
        logger=_FakeLogger(),
    )

    t = _threading.Thread(target=lambda: processing.automatic_wishlist_cleanup_after_db_update(**kwargs))
    t.start()
    assert entered.wait(timeout=10), "first cleanup never started"

    # Second request while the first is mid-flight → skipped, does NOT stack.
    log2 = _FakeLogger()
    out = processing.automatic_wishlist_cleanup_after_db_update(**{**kwargs, "logger": log2})
    assert out == 0
    assert calls == [1]          # the worker body ran exactly once
    assert any("skipping" in str(m).lower() for m in log2.infos)

    release.set()
    t.join(timeout=10)
    assert not t.is_alive()

    # Guard reset → the next cleanup runs again.
    release.set()  # keep the fake non-blocking for the third call
    processing.automatic_wishlist_cleanup_after_db_update(**kwargs)
    assert calls == [1, 1]


def test_auto_cleanup_guard_resets_after_inner_error(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(processing, "remove_tracks_already_in_library", _boom)
    kwargs = dict(
        wishlist_service=_CleanupWishlistService([{
            "name": "Song", "artists": [{"name": "A"}],
            "spotify_track_id": "sp-1", "album": {"name": "Al"},
        }]),
        profiles_database=_CleanupProfilesDatabase(),
        music_database=_CleanupMusicDatabase(),
        active_server="navidrome",
        logger=_FakeLogger(),
    )
    assert processing.automatic_wishlist_cleanup_after_db_update(**kwargs) == 0
    # inner swallowed the error AND the guard reset — a second call still runs
    # (it would return 0/skip forever if the flag leaked)
    assert processing.automatic_wishlist_cleanup_after_db_update(**kwargs) == 0
    with processing._auto_cleanup_lock:
        assert processing._auto_cleanup_running is False


def test_db_update_cleanup_runs_on_dedicated_thread_not_shared_pool():
    """Source guard: the post-DB-update cleanup must not ride the shared
    3-worker missing_download_executor (it starves download post-processing)."""
    from pathlib import Path
    root = Path(processing.__file__).resolve().parents[2]
    # the spawn site moved to api/database_admin.py (aug 25 lift); the ban
    # holds in both homes
    for name in ("web_server.py", "api/database_admin.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "missing_download_executor.submit(_automatic_wishlist_cleanup_after_db_update)" not in text
    assert 'name="WishlistCleanup"' in (root / "api/database_admin.py").read_text(encoding="utf-8")
