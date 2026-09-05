from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_legacy_player_exposes_acquisition_aware_queue_contract():
    source = (ROOT / "webui/static/media-player.js").read_text(encoding="utf-8")

    assert "'/api/playback/queue/prefetch'" in source
    assert "`/api/playback/queue/prefetch/status?${query}`" in source
    # Not the exact argument list: the point is that the acquisition-aware
    # helper is AWAITED before playback, not how many arguments it takes.
    # Pinning the full call broke when a cancellation check was threaded in.
    assert "await npEnsureQueueTrackReady(track" in source
    assert "npQueue = npPrepareQueueTracks(list);" in source
    assert "info._queue_request_ids" in source
    assert "npQueuePrefetchReschedule" in source
    assert "npScheduleQueuePrefetch();" in source


def test_queue_auto_download_control_is_present_and_disabled_by_default():
    source = (ROOT / "webui/index.html").read_text(encoding="utf-8")
    player_source = (ROOT / "webui/static/media-player.js").read_text(encoding="utf-8")

    assert 'id="np-autodownload-btn"' in source
    assert 'class="np-autodownload-btn"' in source
    assert 'aria-pressed="false"' in source
    assert "let npAutoDownloadQueue = false;" in player_source
    assert "localStorage.getItem(npAutoDownloadStorageKey()) === '1'" in player_source
