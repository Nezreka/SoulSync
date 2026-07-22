"""ReplayGain Filler shared the Corrupt File Detector's resolver hole (#1000
follow-up): it called resolve_library_file_path BARE — no transfer folder, no
config_manager — so Docker/NAS users' media-server paths all resolved to None
and the scan silently skipped the entire library. Pin that the resolver now
receives the job context's search space."""

from unittest.mock import MagicMock

import core.repair_jobs.replaygain_filler as mod
from core.repair_jobs.base import JobContext
from core.repair_jobs.replaygain_filler import ReplayGainFillerJob


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_a, **_k):
        return self

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)

    def close(self):
        pass


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def _get_connection(self):
        return _FakeConn(self._rows)


def test_scan_resolves_paths_with_the_job_context(tmp_path, monkeypatch):
    import sys
    import types
    fake_rg = types.SimpleNamespace(is_ffmpeg_available=lambda: True,
                                    read_replaygain_tags=lambda p: {"track_gain": "-3.1 dB"})
    monkeypatch.setitem(sys.modules, "core.replaygain", fake_rg)

    seen = {}

    def _spy(p, **kw):
        seen.update(kw)
        return p

    monkeypatch.setattr(mod, "resolve_library_file_path", _spy)

    f = tmp_path / "01 - Track.flac"
    f.write_bytes(b"x")
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: default
    ctx = JobContext(db=_FakeDB([(1, "Track", "Artist", str(f))]),
                     transfer_folder=str(tmp_path), config_manager=cfg)
    ReplayGainFillerJob().scan(ctx)

    assert seen.get("transfer_folder") == str(tmp_path)
    assert seen.get("config_manager") is cfg
