"""Live speed/ETA on video downloads (Sonarr/Radarr queue parity).

Both sources always HAD the data — slskd reports ``averageSpeed`` per transfer
and every torrent client adapter carries ``download_speed``/``eta`` — but the
monitor discarded it. Now: flatten keeps the speed, the per-poll patches carry
``speed_bps`` + ``eta_seconds`` into the row (new ``_COLUMN_MIGRATIONS``
columns), and the queue row renders '↓ speed · ~time left'.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.video.client_download import process_client_download
from core.video.download_monitor import process_download
from core.video.slskd_download import eta_seconds, flatten_downloads
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_DL_JS = (_ROOT / "webui" / "static" / "video" / "video-downloads-page.js").read_text(encoding="utf-8")


def _slskd_payload(state="InProgress, Downloading", speed=2_000_000):
    return [{"username": "u1", "directories": [{"files": [{
        "filename": "a\\b\\Movie.2026.1080p.mkv", "id": "t1", "state": state,
        "size": 4_000_000_000, "bytesTransferred": 1_000_000_000,
        "averageSpeed": speed}]}]}]


def test_flatten_keeps_average_speed():
    t = flatten_downloads(_slskd_payload())[0]
    assert t["speed"] == 2_000_000
    assert eta_seconds(t) == 1500          # 3 GB left at 2 MB/s


def test_eta_none_when_not_moving_or_sizeless():
    assert eta_seconds({"size": 100, "transferred": 0, "speed": 0}) is None
    assert eta_seconds({"size": 0, "transferred": 0, "speed": 100}) is None


def test_slskd_patch_carries_speed_and_eta():
    dl = {"username": "u1", "filename": "a\\b\\Movie.2026.1080p.mkv"}
    patch = process_download(dl, flatten_downloads(_slskd_payload()), "/dl",
                             lister=lambda d: [], mover=lambda s, d: None)
    assert patch["status"] == "downloading"
    assert patch["speed_bps"] == 2_000_000 and patch["eta_seconds"] == 1500
    # queued rows zero the speed so a stalled row never shows a stale rate
    q = process_download(dl, flatten_downloads(_slskd_payload(state="Queued, Remotely")), "/dl",
                         lister=lambda d: [], mover=lambda s, d: None)
    assert q["status"] == "queued" and q["speed_bps"] == 0 and q["eta_seconds"] is None


def test_client_patch_prefers_real_eta_and_falls_back_to_computed():
    dl = {"client_ref": "hash1", "source": "torrent"}
    def mk(**kw):
        base = dict(progress=0.25, size=4_000_000_000, downloaded=1_000_000_000,
                    download_speed=3_000_000, eta=None, error=None)
        base.update(kw)
        return SimpleNamespace(**base)

    real = process_client_download(dl, get_status=lambda s, r: mk(eta=420),
                                   resolve_path=lambda p: p, find_video=lambda a, b: None)
    assert real["speed_bps"] == 3_000_000 and real["eta_seconds"] == 420

    # qBittorrent's 8640000 'unknown' sentinel → computed from speed instead
    sentinel = process_client_download(dl, get_status=lambda s, r: mk(eta=8_640_000),
                                       resolve_path=lambda p: p, find_video=lambda a, b: None)
    assert sentinel["eta_seconds"] == 1000    # 3 GB left at 3 MB/s

    stalled = process_client_download(dl, get_status=lambda s, r: mk(download_speed=0),
                                      resolve_path=lambda p: p, find_video=lambda a, b: None)
    assert stalled["speed_bps"] == 0 and stalled["eta_seconds"] is None


def test_columns_migrate_and_patch_persists(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    did = db.add_video_download({"kind": "movie", "source": "soulseek", "title": "X",
                                 "status": "downloading", "target_dir": "/x"})
    db.update_video_download(did, speed_bps=1_500_000, eta_seconds=90)
    row = [d for d in db.get_active_video_downloads() if d["id"] == did][0]
    assert row["speed_bps"] == 1_500_000 and row["eta_seconds"] == 90


def test_queue_row_renders_speed_and_eta():
    assert "fmtEta(d.eta_seconds)" in _DL_JS
    assert "'↓ ' + fmtSpeed(d.speed_bps)" in _DL_JS
    fn = _DL_JS.split("function fmtEta")[1].split("function pad2")[0]
    assert "left" in fn     # human phrasing, not a bare number
