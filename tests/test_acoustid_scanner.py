from types import SimpleNamespace

from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return None


class _FakeConnection:
    def __init__(self, rows):
        self._cursor = _FakeCursor(rows)

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def _make_context(rows):
    conn = _FakeConnection(rows)
    config_manager = SimpleNamespace(
        get=lambda key, default=None: default,
        set=lambda *args, **kwargs: None,
    )
    db = SimpleNamespace(_get_connection=lambda: conn)
    return SimpleNamespace(
        db=db,
        transfer_folder="/music",
        config_manager=config_manager,
        acoustid_client=object(),
        create_finding=None,
        report_progress=lambda **kwargs: None,
        update_progress=lambda *args, **kwargs: None,
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        sleep_or_stop=lambda *args, **kwargs: False,
    )


def test_load_db_tracks_skips_null_ids_and_normalizes_track_ids():
    job = AcoustIDScannerJob()
    context = _make_context([
        (None, "Broken Track", "Artist", "/music/broken.flac", 1, "Album", None, None),
        (42, "Good Track", "Artist", "/music/good.flac", 2, "Album", "album-thumb", "artist-thumb"),
    ])

    tracks = job._load_db_tracks(context)

    assert list(tracks.keys()) == ["42"]
    assert tracks["42"]["title"] == "Good Track"
    assert tracks["42"]["artist"] == "Artist"


def test_scan_handles_mixed_track_id_types(monkeypatch):
    job = AcoustIDScannerJob()
    context = _make_context([
        (None, "Broken Track", "Artist", "/music/broken.flac", 1, "Album", None, None),
        (42, "Good Track", "Artist", "/music/good.flac", 2, "Album", "album-thumb", "artist-thumb"),
    ])

    monkeypatch.setattr(job, "_resolve_path", lambda file_path, _context: file_path)

    scanned_track_ids = []

    def fake_scan_file(fpath, track_id, expected, acoustid_client, context, result,
                       fp_threshold, title_threshold, artist_threshold):
        scanned_track_ids.append(track_id)

    monkeypatch.setattr(job, "_scan_file", fake_scan_file)

    result = job.scan(context)

    assert result.scanned == 1
    assert scanned_track_ids == ["42"]
