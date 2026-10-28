"""Missing Lyrics maintenance job + lyrics_client check-only seam (Sokhi).

Mirrors the Cover Art Filler: scan only flags tracks LRClib actually has
lyrics for (Option A — instrumentals never flagged), and applying writes the
.lrc via the shared LyricsClient.
"""

from __future__ import annotations

import os
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.lyrics_client import LyricsClient
from core.repair_jobs.missing_lyrics import MissingLyricsJob, _has_lrc_sidecar


# ── lyrics_client.has_remote_lyrics (check-only seam) ────────────────────────

def _client_with_api(api):
    c = LyricsClient.__new__(LyricsClient)
    c.api = api
    return c


def test_has_remote_lyrics_true_when_synced():
    api = MagicMock()
    api.get_lyrics.return_value = SimpleNamespace(synced_lyrics="[00:01]hi", plain_lyrics=None)
    c = _client_with_api(api)
    assert c.has_remote_lyrics("Song", "Artist", "Album", 200) is True


def test_has_remote_lyrics_true_when_plain_only_via_search():
    api = MagicMock()
    api.get_lyrics.return_value = None
    api.search_lyrics.return_value = [SimpleNamespace(synced_lyrics=None, plain_lyrics="words")]
    c = _client_with_api(api)
    assert c.has_remote_lyrics("Song", "Artist") is True


def test_has_remote_lyrics_false_when_none():
    api = MagicMock()
    api.get_lyrics.return_value = None
    api.search_lyrics.return_value = []
    assert _client_with_api(api).has_remote_lyrics("Instrumental", "Artist") is False


def test_has_remote_lyrics_unknown_when_no_api():
    # #1879 tri-state: no API client is an UNKNOWN, not a definitive
    # negative — "we couldn't ask" must not read as "no lyrics exist".
    c = LyricsClient.__new__(LyricsClient)
    c.api = None
    assert c.has_remote_lyrics("Song", "Artist") is None


# ── sidecar detection ────────────────────────────────────────────────────────

def test_has_lrc_sidecar(tmp_path):
    audio = tmp_path / "track.flac"
    audio.write_bytes(b"x")
    assert _has_lrc_sidecar(str(audio)) is False
    (tmp_path / "track.lrc").write_text("[00:01]hi")
    assert _has_lrc_sidecar(str(audio)) is True


# ── the scan (Option A: only flag fixable tracks) ────────────────────────────

class _DB:
    def __init__(self, rows):
        self._rows = rows

    def _get_connection(self):
        cur = MagicMock()
        cur.execute.return_value = None
        cur.fetchone.return_value = [len(self._rows)]
        cur.fetchall.return_value = self._rows
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn


def _ctx(db, findings):
    return SimpleNamespace(
        db=db,
        config_manager=SimpleNamespace(get=lambda k, d=None: d),
        check_stop=lambda: False, wait_if_paused=lambda: False,
        update_progress=lambda *a, **k: None, report_progress=lambda *a, **k: None,
        create_finding=lambda **kw: (findings.append(kw) or True),
    )


def test_scan_flags_only_tracks_with_available_lyrics(tmp_path, monkeypatch):
    # Two tracks, neither has a .lrc. LRClib has lyrics for the first, not the second.
    t1 = tmp_path / "song.flac"; t1.write_bytes(b"x")
    t2 = tmp_path / "instrumental.flac"; t2.write_bytes(b"x")
    rows = [
        (1, "Song", "Artist", "Album", str(t1), 200),
        (2, "Interlude", "Artist", "Album", str(t2), 60),
    ]
    fake_client = SimpleNamespace(
        api=object(),
        has_remote_lyrics=lambda title, artist, album, dur: title == "Song",
    )
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)

    findings = []
    result = MissingLyricsJob().scan(_ctx(_DB(rows), findings))

    assert result.findings_created == 1
    assert findings[0]["entity_type"] == "track"
    assert findings[0]["finding_type"] == "missing_lyrics"
    assert findings[0]["details"]["track_title"] == "Song"   # the instrumental was skipped


def test_scan_converts_duration_ms_to_seconds(tmp_path, monkeypatch):
    # tracks.duration is milliseconds; LRClib wants seconds. The scan must
    # convert before querying (215000ms → 215s) and store seconds in the finding.
    t1 = tmp_path / "song.flac"; t1.write_bytes(b"x")
    rows = [(1, "Song", "Artist", "Album", str(t1), 215000)]   # 215000 ms = 215 s
    seen = {}
    fake_client = SimpleNamespace(
        api=object(),
        has_remote_lyrics=lambda title, artist, album, dur: seen.update(dur=dur) or True)
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)

    findings = []
    MissingLyricsJob().scan(_ctx(_DB(rows), findings))
    assert seen["dur"] == 215                       # converted to seconds
    assert findings[0]["details"]["duration"] == 215


def test_scan_skips_tracks_that_already_have_lrc(tmp_path, monkeypatch):
    t1 = tmp_path / "song.flac"; t1.write_bytes(b"x")
    (tmp_path / "song.lrc").write_text("[00:01]hi")   # already has lyrics
    rows = [(1, "Song", "Artist", "Album", str(t1), 200)]
    fake_client = SimpleNamespace(api=object(),
                                  has_remote_lyrics=lambda *a, **k: True)
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)

    findings = []
    result = MissingLyricsJob().scan(_ctx(_DB(rows), findings))
    assert result.findings_created == 0
    assert findings == []


def test_scan_resolves_db_path_before_lrc_check(tmp_path, monkeypatch):
    """#955: the .lrc check must run on the RESOLVED on-disk path, not the raw DB path. A path-mapped
    (docker) user stores e.g. /data/... but the file lives elsewhere, with the .lrc next to the REAL
    file. The scan must resolve + skip it — checking the raw path flagged every such track as missing
    even though the sidecar was right there."""
    real_audio = tmp_path / "08. Tomorrow (Album Version).flac"; real_audio.write_bytes(b"x")
    (tmp_path / "08. Tomorrow (Album Version).lrc").write_text("[00:01]hi")   # sidecar by the REAL file
    db_path = "/data/Sean Kingston/Tomorrow (2009)/08. Tomorrow (Album Version).flac"   # not real here
    rows = [(626, "Tomorrow", "Sean Kingston", "Tomorrow", db_path, 200000)]
    # resolver maps the stored container path -> the real file on disk
    monkeypatch.setattr("core.repair_jobs.missing_lyrics.resolve_library_file_path",
                        lambda p, **k: str(real_audio) if p == db_path else None)
    # LRClib WOULD have lyrics — so without the fix (raw-path check) this gets wrongly flagged.
    fake_client = SimpleNamespace(api=object(), has_remote_lyrics=lambda *a, **k: True)
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)

    findings = []
    result = MissingLyricsJob().scan(_ctx(_DB(rows), findings))
    assert result.skipped == 1            # resolved -> .lrc found -> skipped
    assert result.findings_created == 0
    assert findings == []                 # NOT flagged despite LRClib having lyrics


def test_scan_noops_when_lrclib_disabled(monkeypatch):
    db = _DB([(1, "Song", "Artist", "Album", "/x.flac", 200)])
    ctx = _ctx(db, [])
    ctx.config_manager = SimpleNamespace(
        get=lambda k, d=None: False if k == 'metadata_enhancement.lrclib_enabled' else d)
    result = MissingLyricsJob().scan(ctx)
    assert result.scanned == 0 and result.findings_created == 0


# ── _fix_missing_lyrics apply handler ────────────────────────────────────────

def test_fix_missing_lyrics_calls_create_lrc(tmp_path, monkeypatch):
    from core.repair_worker import RepairWorker
    audio = tmp_path / "song.flac"; audio.write_bytes(b"x")

    w = RepairWorker.__new__(RepairWorker)
    w.transfer_folder = str(tmp_path)
    w._config_manager = SimpleNamespace(get=lambda k, d=None: d)

    calls = {}
    fake_client = SimpleNamespace(
        create_lrc_file=lambda path, title, artist, album_name=None, duration_seconds=None:
            calls.update(path=path, title=title, artist=artist) or True)
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)
    # _resolve_file_path: the file is already real, so identity is fine.
    monkeypatch.setattr("core.repair_worker._resolve_file_path",
                        lambda raw, *a, **k: raw)

    res = w._fix_missing_lyrics("track", "1", None, {
        "file_path": str(audio), "track_title": "Song", "artist": "Artist",
        "album_title": "Album", "duration": 200})
    assert res["success"] is True and res["action"] == "applied_lyrics"
    assert calls["title"] == "Song" and calls["path"] == str(audio)


def test_fix_missing_lyrics_missing_file(tmp_path, monkeypatch):
    from core.repair_worker import RepairWorker
    w = RepairWorker.__new__(RepairWorker)
    w.transfer_folder = str(tmp_path)
    w._config_manager = SimpleNamespace(get=lambda k, d=None: d)
    monkeypatch.setattr("core.repair_worker._resolve_file_path", lambda raw, *a, **k: raw)
    res = w._fix_missing_lyrics("track", "1", None, {"file_path": str(tmp_path / "gone.flac")})
    assert res["success"] is False


# ── retag apply_track_plans lyrics_action ────────────────────────────────────

def test_apply_track_plans_lyrics_action(tmp_path, monkeypatch):
    from core.repair_jobs import library_retag
    audio = tmp_path / "t.flac"; audio.write_bytes(b"x")

    monkeypatch.setattr(library_retag, "write_tags_to_file",
                        lambda *a, **k: {"success": True}, raising=False)
    seen = {}
    fake_client = SimpleNamespace(
        create_lrc_file=lambda path, title, artist, album_name=None, duration_seconds=None:
            seen.update(title=title) or True)
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)

    plans = [{"file_path": str(audio), "db_data": {},
              "lyrics_meta": {"title": "Song", "artist": "Artist", "album": "Album"}}]
    res = library_retag.apply_track_plans(plans, lyrics_action=True)
    assert res["lyrics_written"] == 1 and seen["title"] == "Song"


def test_apply_track_plans_lyrics_never_writes_tags(tmp_path, monkeypatch):
    # The lyrics query must come from lyrics_meta, NOT db_data — so an
    # unmatched track (db_data={}) gets lyrics fetched but NO tags written.
    from core.repair_jobs import library_retag
    audio = tmp_path / "t.flac"; audio.write_bytes(b"x")
    written = []
    monkeypatch.setattr("core.tag_writer.write_tags_to_file",
                        lambda fp, db_data, **k: written.append(db_data) or {"success": True})
    monkeypatch.setattr("core.lyrics_client.lyrics_client",
                        SimpleNamespace(create_lrc_file=lambda *a, **k: True))

    plans = [{"file_path": str(audio), "db_data": {},
              "lyrics_meta": {"title": "Song", "artist": "Artist", "album": "Al"}}]
    res = library_retag.apply_track_plans(plans, lyrics_action=True)
    assert res["lyrics_written"] == 1
    # write_tags_to_file is never called for lyrics-only plans — no tags written at all.
    assert written == []


def test_apply_track_plans_no_lyrics_when_disabled(tmp_path, monkeypatch):
    from core.repair_jobs import library_retag
    audio = tmp_path / "t.flac"; audio.write_bytes(b"x")
    monkeypatch.setattr(library_retag, "write_tags_to_file",
                        lambda *a, **k: {"success": True}, raising=False)
    called = []
    fake_client = SimpleNamespace(create_lrc_file=lambda *a, **k: called.append(1) or True)
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)

    plans = [{"file_path": str(audio), "db_data": {"title": "Song"}}]
    res = library_retag.apply_track_plans(plans, lyrics_action=False)
    assert res["lyrics_written"] == 0 and called == []


# ── #1902: pending-finding skip + remembered 404s ──────────────────────────

def _real_db(tmp_path, tracks, findings_rows=()):
    """A file DB with the tracks/artists/albums/repair_findings schema, so
    every _get_connection is its own connection (the scan closes them)."""
    path = str(tmp_path / "music.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE albums (id INTEGER PRIMARY KEY, title TEXT);
        CREATE TABLE tracks (id INTEGER PRIMARY KEY, title TEXT,
                             artist_id INTEGER, album_id INTEGER,
                             file_path TEXT, duration INTEGER);
        CREATE TABLE repair_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT,
            finding_type TEXT, severity TEXT, status TEXT,
            entity_type TEXT, entity_id TEXT, file_path TEXT, title TEXT,
            description TEXT, details_json TEXT DEFAULT '{}',
            updated_at TIMESTAMP);
        INSERT INTO artists VALUES (1, 'Artist');
        INSERT INTO albums VALUES (1, 'Album');
    """)
    for t in tracks:
        conn.execute("INSERT INTO tracks VALUES (?, ?, 1, 1, ?, ?)", t)
    for f in findings_rows:
        conn.execute(
            "INSERT INTO repair_findings (job_id, finding_type, severity, "
            "status, entity_type, entity_id, file_path, title) "
            "VALUES (?, ?, 'info', ?, 'track', ?, ?, ?)", f)
    conn.commit()
    conn.close()

    def connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    return SimpleNamespace(_get_connection=connect)


def _real_ctx(db, findings, **settings):
    cfg = {'repair.jobs.missing_lyrics.settings': settings}
    return SimpleNamespace(
        db=db,
        config_manager=SimpleNamespace(get=lambda k, d=None: cfg.get(k, d)),
        check_stop=lambda: False, wait_if_paused=lambda: False,
        update_progress=lambda *a, **k: None, report_progress=lambda *a, **k: None,
        create_finding=lambda **kw: (findings.append(kw) or True),
    )


def _probe_client(status_by_title):
    """A check_remote_lyrics client recording every probe by title."""
    calls = []

    def check(title, artist, album, dur):
        calls.append(title)
        status = status_by_title.get(title, 'absent')
        return SimpleNamespace(
            status=status, reason='', error_kind='not_found' if status == 'absent' else 'timeout')

    return SimpleNamespace(api=object(), check_remote_lyrics=check, calls=calls)


def test_scan_skips_track_with_pending_finding(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    audio2 = tmp_path / "b.flac"; audio2.write_bytes(b"x")
    db = _real_db(
        tmp_path,
        [(1, "PendingSong", str(audio), 200000), (2, "NewSong", str(audio2), 200000)],
        findings_rows=[('missing_lyrics', 'missing_lyrics', 'pending', '1', str(audio), 'x')])
    client = _probe_client({'PendingSong': 'found', 'NewSong': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)

    findings = []
    result = MissingLyricsJob().scan(_real_ctx(db, findings))
    assert client.calls == ["NewSong"]          # the pending track was never probed
    assert result.findings_created == 1
    # Only the new track goes through create_finding; the pending
    # track's row is refreshed by a bounded UPDATE (asserted in the
    # entity-only test below), never re-filed.
    assert [f["details"]["track_title"] for f in findings] == ["NewSong"]
    # The pending track still counts in the run totals (scanned + skipped),
    # so totals and the scope estimate keep describing the whole library.
    assert (result.scanned, result.skipped) == (2, 1)


def test_scan_skips_pending_finding_matched_by_file_path(tmp_path, monkeypatch):
    # The worker dedups on (entity, entity_id) OR file_path: a pending
    # finding filed under a stale track id for the same file must also
    # suppress the probe.
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(
        tmp_path, [(7, "Song", str(audio), 200000)],
        findings_rows=[('missing_lyrics', 'missing_lyrics', 'pending', '999', str(audio), 'x')])
    client = _probe_client({'Song': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)

    findings = []
    result = MissingLyricsJob().scan(_real_ctx(db, findings))
    assert client.calls == []
    assert (result.scanned, result.skipped, result.findings_created) == (1, 1, 0)
    # The refresh also repairs the row's stale entity_id column (999)
    # to the track it matched by path.
    conn = db._get_connection()
    entity_id = conn.execute(
        "SELECT entity_id FROM repair_findings WHERE status = 'pending'").fetchone()[0]
    conn.close()
    assert entity_id == "7"


@pytest.mark.parametrize('status', ['dismissed', 'resolved'])
def test_scan_probes_track_with_non_pending_finding(tmp_path, monkeypatch, status):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(
        tmp_path, [(1, "Song", str(audio), 200000)],
        findings_rows=[('missing_lyrics', 'missing_lyrics', status, '1', str(audio), 'x')])
    client = _probe_client({'Song': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)

    findings = []
    MissingLyricsJob().scan(_real_ctx(db, findings))
    assert client.calls == ["Song"]


def _no_findings_db(tmp_path, tracks):
    """A file DB with NO repair_findings table (very old schema), holding
    (id, title, file_path, duration_ms) tracks."""
    path = str(tmp_path / "music.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE albums (id INTEGER PRIMARY KEY, title TEXT);
        CREATE TABLE tracks (id INTEGER PRIMARY KEY, title TEXT,
                             artist_id INTEGER, album_id INTEGER,
                             file_path TEXT, duration INTEGER);
        INSERT INTO artists VALUES (1, 'Artist');
        INSERT INTO albums VALUES (1, 'Album');
    """)
    for t in tracks:
        conn.execute("INSERT INTO tracks VALUES (?, ?, 1, 1, ?, ?)", t)
    conn.commit(); conn.close()
    return SimpleNamespace(_get_connection=lambda: sqlite3.connect(path))


def test_scan_probes_when_no_repair_findings_table(tmp_path, monkeypatch):
    # Very old schemas: the pending check must degrade, not fail the scan.
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _no_findings_db(tmp_path, [(1, "Song", str(audio), 200000)])
    client = _probe_client({'Song': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)

    findings = []
    result = MissingLyricsJob().scan(_real_ctx(db, findings))
    assert client.calls == ["Song"]
    assert result.findings_created == 1


def test_absent_is_probed_every_scan_by_default(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)

    MissingLyricsJob().scan(_real_ctx(db, []))
    MissingLyricsJob().scan(_real_ctx(db, []))
    assert client.calls == ["Instrumental", "Instrumental"]


def test_absent_is_remembered_when_opted_in(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)

    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True)  # noqa: E731
    a = MissingLyricsJob().scan(ctx())
    b = MissingLyricsJob().scan(ctx())
    assert client.calls == ["Instrumental"]      # second scan spent no request
    assert (a.scanned, a.skipped) == (b.scanned, b.skipped)


def test_unknown_is_never_remembered(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Song", str(audio), 200000)])
    client = _probe_client({'Song': 'unknown'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)

    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True)  # noqa: E731
    MissingLyricsJob().scan(ctx())
    MissingLyricsJob().scan(ctx())
    assert client.calls == ["Song", "Song"]


def test_remembered_track_is_asked_again_after_recheck_window(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    # First answer absent (remembered), then lyrics appear; with a 0-day
    # window the next scan must ask again and surface the finding.
    client.check_remote_lyrics = lambda title, artist, album, dur: (
        client.calls.append(title) or
        SimpleNamespace(status='absent', reason='', error_kind='not_found'))
    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True, recheck_after_days=0)  # noqa: E731
    MissingLyricsJob().scan(ctx())
    client.check_remote_lyrics = lambda title, artist, album, dur: (
        client.calls.append(title) or
        SimpleNamespace(status='found', reason='', error_kind=''))
    findings = []
    result = MissingLyricsJob().scan(_real_ctx(db, findings, remember_empty_lookups=True, recheck_after_days=0))
    assert client.calls == ["Instrumental", "Instrumental"]
    assert result.findings_created == 1


def test_changed_track_is_asked_again(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent', 'Instrumental (Live)': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)

    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True)  # noqa: E731
    MissingLyricsJob().scan(ctx())
    conn = db._get_connection()
    conn.execute("UPDATE tracks SET title = 'Instrumental (Live)' WHERE id = 1")
    conn.commit(); conn.close()
    MissingLyricsJob().scan(ctx())
    assert client.calls == ["Instrumental", "Instrumental (Live)"]


def test_has_remote_only_client_absent_remembered_none_not(tmp_path, monkeypatch):
    # The fallback probe path (no check_remote_lyrics): False is a
    # definitive absent and is remembered; None is unknown and is not.
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    audio2 = tmp_path / "b.flac"; audio2.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000),
                             (2, "Mystery", str(audio2), 200000)])
    calls = []
    fake = SimpleNamespace(
        api=object(),
        has_remote_lyrics=lambda title, artist, album, dur: calls.append(title) or (False if title == "Instrumental" else None))
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake)

    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True)  # noqa: E731
    MissingLyricsJob().scan(ctx())
    MissingLyricsJob().scan(ctx())
    assert calls == ["Instrumental", "Mystery", "Mystery"]


def test_memory_survives_an_early_stop(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    audio2 = tmp_path / "b.flac"; audio2.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000),
                             (2, "Other", str(audio2), 200000)])
    client = _probe_client({'Instrumental': 'absent', 'Other': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)

    stopping = _real_ctx(db, [], remember_empty_lookups=True)
    stopping.check_stop = lambda: len(client.calls) >= 1
    MissingLyricsJob().scan(stopping)
    # The first track's absent answer was committed on close despite the
    # early stop, so a fresh scan does not re-probe it.
    MissingLyricsJob().scan(_real_ctx(db, [], remember_empty_lookups=True))
    assert client.calls == ["Instrumental", "Other"]


def test_periodic_commit_persists_absents_before_the_scan_ends(tmp_path, monkeypatch):
    tracks = []
    for n in range(1, 31):
        f = tmp_path / f"t{n}.flac"; f.write_bytes(b"x")
        tracks.append((n, f"Track{n}", str(f), 200000))
    db = _real_db(tmp_path, tracks)
    client = _probe_client({})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)

    seen_mid_scan = {}

    def check_stop():
        if len(client.calls) == 26 and 'count' not in seen_mid_scan:
            conn = db._get_connection()
            seen_mid_scan['count'] = conn.execute(
                "SELECT COUNT(*) FROM repair_empty_lookups").fetchone()[0]
            conn.close()
            return True
        return False

    ctx = _real_ctx(db, [], remember_empty_lookups=True)
    ctx.check_stop = check_stop
    MissingLyricsJob().scan(ctx)
    # By the 26th probe, the first 25 absents were already committed —
    # not merely buffered until close().
    assert seen_mid_scan['count'] >= 25


def test_stale_memory_is_forgotten_when_lyrics_appear(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Song", str(audio), 200000)])
    client = _probe_client({'Song': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True)  # noqa: E731
    MissingLyricsJob().scan(ctx())
    # Backdate the remembered answer past the 7-day window.
    conn = db._get_connection()
    conn.execute("UPDATE repair_empty_lookups SET checked_at = checked_at - 8 * 86400")
    conn.commit(); conn.close()
    client.check_remote_lyrics = lambda title, artist, album, dur: (
        client.calls.append(title) or
        SimpleNamespace(status='found', reason='', error_kind=''))
    findings = []
    result = MissingLyricsJob().scan(_real_ctx(db, findings, remember_empty_lookups=True))
    assert result.findings_created == 1
    conn = db._get_connection()
    left = conn.execute("SELECT COUNT(*) FROM repair_empty_lookups").fetchone()[0]
    conn.close()
    assert left == 0


def test_prune_drops_memory_for_deleted_tracks(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True)  # noqa: E731
    MissingLyricsJob().scan(ctx())
    conn = db._get_connection()
    conn.execute("DELETE FROM tracks WHERE id = 1")
    conn.commit(); conn.close()
    MissingLyricsJob().scan(ctx())
    conn = db._get_connection()
    left = conn.execute("SELECT COUNT(*) FROM repair_empty_lookups").fetchone()[0]
    conn.close()
    assert left == 0


def test_garbage_recheck_days_falls_back_and_memory_still_works(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True, recheck_after_days='bogus')  # noqa: E731
    MissingLyricsJob().scan(ctx())
    MissingLyricsJob().scan(ctx())
    assert client.calls == ["Instrumental"]


def test_string_false_toggle_does_not_enable_memory(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    ctx = lambda: _real_ctx(db, [], remember_empty_lookups='false')  # noqa: E731
    MissingLyricsJob().scan(ctx())
    MissingLyricsJob().scan(ctx())
    assert client.calls == ["Instrumental", "Instrumental"]


def test_absent_remembered_asserts_exact_counts(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True)  # noqa: E731
    a = MissingLyricsJob().scan(ctx())
    b = MissingLyricsJob().scan(ctx())
    assert (a.scanned, a.skipped, a.findings_created) == (1, 1, 0)
    assert (b.scanned, b.skipped, b.findings_created) == (1, 1, 0)


# ── round-2 review: isolating each arm / interaction ───────────────────────

def test_scan_skips_pending_finding_matched_by_entity_only(tmp_path, monkeypatch):
    # Entity arm isolated: the finding's file_path is stale/different, so
    # only the entity_id key can suppress the probe.
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(
        tmp_path, [(1, "Song", str(audio), 200000)],
        findings_rows=[('missing_lyrics', 'missing_lyrics', 'pending', '1', '/old/gone.flac', 'x')])
    client = _probe_client({'Song': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    findings = []
    result = MissingLyricsJob().scan(_real_ctx(db, findings))
    assert client.calls == []
    # No new finding is filed — and the pending row itself was
    # refreshed in place: its file_path COLUMN (stale '/old/gone.flac')
    # and its details_json now carry the track's current data.
    assert result.findings_created == 0
    assert findings == []
    conn = db._get_connection()
    row = conn.execute(
        "SELECT file_path, details_json FROM repair_findings WHERE status = 'pending'").fetchone()
    conn.close()
    assert row[0] == str(audio)
    assert '"track_title": "Song"' in row[1]


@pytest.mark.parametrize('job_id,finding_type', [
    ('other_job', 'missing_lyrics'),
    ('missing_lyrics', 'missing_cover_art'),
])
def test_pending_finding_from_another_job_or_type_does_not_suppress(tmp_path, monkeypatch, job_id, finding_type):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(
        tmp_path, [(1, "Song", str(audio), 200000)],
        findings_rows=[(job_id, finding_type, 'pending', '1', str(audio), 'x')])
    client = _probe_client({'Song': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    findings = []
    MissingLyricsJob().scan(_real_ctx(db, findings))
    assert client.calls == ["Song"]


def test_file_path_change_alone_is_asked_again(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    moved = tmp_path / "moved.flac"; moved.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True)  # noqa: E731
    MissingLyricsJob().scan(ctx())
    conn = db._get_connection()
    conn.execute("UPDATE tracks SET file_path = ? WHERE id = 1", (str(moved),))
    conn.commit(); conn.close()
    MissingLyricsJob().scan(ctx())
    assert client.calls == ["Instrumental", "Instrumental"]


def test_prune_preserves_a_pending_tracks_memory_entry(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True)  # noqa: E731
    MissingLyricsJob().scan(ctx())               # absent -> remembered
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, severity, status, "
        "entity_type, entity_id, file_path, title) VALUES "
        "('missing_lyrics', 'missing_lyrics', 'info', 'pending', 'track', '1', ?, 'x')",
        (str(audio),))
    conn.commit(); conn.close()
    MissingLyricsJob().scan(ctx())               # pending-skipped, completed -> prune
    conn = db._get_connection()
    left = conn.execute("SELECT COUNT(*) FROM repair_empty_lookups").fetchone()[0]
    conn.execute("DELETE FROM repair_findings")
    conn.commit(); conn.close()
    assert left == 1                             # prune kept the pending track's entry
    MissingLyricsJob().scan(ctx())               # ...so it is still not re-probed
    assert client.calls == ["Instrumental"]


def test_periodic_commit_counts_remembers_not_rows(tmp_path, monkeypatch):
    # Sidecars on the index-multiple rows (25, 50) must not starve the
    # periodic commit: it counts remembered answers, not row indexes.
    tracks = []
    for n in range(1, 61):
        f = tmp_path / f"t{n}.flac"; f.write_bytes(b"x")
        if n in (25, 50):
            (tmp_path / f"t{n}.lrc").write_text("[00:01]hi")
        tracks.append((n, f"Track{n}", str(f), 200000))
    db = _real_db(tmp_path, tracks)
    client = _probe_client({})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    seen = {}

    def check_stop():
        if len(client.calls) == 40 and 'count' not in seen:
            conn = db._get_connection()
            seen['count'] = conn.execute(
                "SELECT COUNT(*) FROM repair_empty_lookups").fetchone()[0]
            conn.close()
            return True
        return False

    ctx = _real_ctx(db, [], remember_empty_lookups=True)
    ctx.check_stop = check_stop
    MissingLyricsJob().scan(ctx)
    assert seen['count'] >= 25


def test_infinite_recheck_days_falls_back_to_default(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    MissingLyricsJob().scan(_real_ctx(db, [], remember_empty_lookups=True))
    conn = db._get_connection()
    conn.execute("UPDATE repair_empty_lookups SET checked_at = checked_at - 8 * 86400")
    conn.commit(); conn.close()
    # With inf trusted literally the backdated answer would still be
    # remembered; falling back to 7 days means it is asked again.
    MissingLyricsJob().scan(_real_ctx(db, [], remember_empty_lookups=True, recheck_after_days=float('inf')))
    assert client.calls == ["Instrumental", "Instrumental"]


def test_negative_recheck_days_falls_back_to_default(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True, recheck_after_days=-5)  # noqa: E731
    MissingLyricsJob().scan(ctx())
    MissingLyricsJob().scan(ctx())
    # A negative window trusted literally (=0 days) would re-probe at
    # once; the 7-day fallback trusts the fresh answer.
    assert client.calls == ["Instrumental"]


def test_int_toggle_does_not_enable_memory(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=1)  # noqa: E731
    MissingLyricsJob().scan(ctx())
    MissingLyricsJob().scan(ctx())
    assert client.calls == ["Instrumental", "Instrumental"]


def test_pending_fallback_logs_a_warning(tmp_path, monkeypatch, caplog):
    import logging
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    # No repair_findings table at all -> the pending-key load fails ->
    # the scan continues without the skip, and says so in the log.
    db = _no_findings_db(tmp_path, [(1, "Song", str(audio), 200000)])
    client = _probe_client({'Song': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    findings = []
    with caplog.at_level(logging.WARNING):
        result = MissingLyricsJob().scan(_real_ctx(db, findings))
    assert any('pending-finding check unavailable' in r.message for r in caplog.records)
    assert result.findings_created == 1          # the fallback scan still works
    assert client.calls == ["Song"]


# ── Claude gate: lifecycle + refresh semantics ─────────────────────────────

def test_pending_finding_with_wrong_entity_type_does_not_suppress(tmp_path, monkeypatch):
    # The entity arm requires entity_type='track' (the file_path arm
    # does not — and here the path differs, so neither arm may fire).
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Song", str(audio), 200000)])
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, severity, status, "
        "entity_type, entity_id, file_path, title) VALUES "
        "('missing_lyrics', 'missing_lyrics', 'info', 'pending', 'album', '1', '/elsewhere.flac', 'x')")
    conn.commit(); conn.close()
    client = _probe_client({'Song': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    MissingLyricsJob().scan(_real_ctx(db, []))
    assert client.calls == ["Song"]


def test_early_stop_does_not_prune_memory(tmp_path, monkeypatch):
    a = tmp_path / "a.flac"; a.write_bytes(b"x")
    b = tmp_path / "b.flac"; b.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Gone", str(a), 200000), (2, "Stay", str(b), 200000)])
    client = _probe_client({'Gone': 'absent', 'Stay': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True)  # noqa: E731
    MissingLyricsJob().scan(ctx())               # both remembered
    conn = db._get_connection()
    conn.execute("DELETE FROM tracks WHERE id = 1")
    conn.commit(); conn.close()
    stopping = _real_ctx(db, [], remember_empty_lookups=True)
    stopping.check_stop = lambda: True
    MissingLyricsJob().scan(stopping)            # stopped: no prune
    conn = db._get_connection()
    keys = [r[0] for r in conn.execute("SELECT key FROM repair_empty_lookups ORDER BY key")]
    conn.close()
    assert keys == ["1", "2"]                    # the deleted track's entry survived
    MissingLyricsJob().scan(ctx())               # completed scan: prune runs
    conn = db._get_connection()
    keys = [r[0] for r in conn.execute("SELECT key FROM repair_empty_lookups ORDER BY key")]
    conn.close()
    assert keys == ["2"]


def test_remembered_skip_does_not_reset_the_unknown_streak(tmp_path, monkeypatch):
    files = {}
    tracks = []
    for n, title in enumerate(["U1", "U2", "R", "U3", "U4", "U5", "U6"], start=1):
        f = tmp_path / f"t{n}.flac"; f.write_bytes(b"x"); files[title] = f
        tracks.append((n, title, str(f), 200000))
    db = _real_db(tmp_path, tracks)
    statuses = {t: 'unknown' for t in files}
    statuses['R'] = 'absent'
    client = _probe_client(statuses)
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True)  # noqa: E731
    MissingLyricsJob().scan(ctx())               # R is remembered; streak never reaches 5
    assert 'U6' in client.calls
    client.calls.clear()
    result = MissingLyricsJob().scan(ctx())
    # Scan 2: U1,U2 (streak 2), R remembered (must NOT reset), U3,U4,U5
    # reach 5 consecutive unknown PROBES -> early stop before U6.
    assert result.stopped_early
    assert client.calls == ["U1", "U2", "U3", "U4", "U5"]


def test_memory_and_prune_on_a_db_without_repair_findings(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _no_findings_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True)  # noqa: E731
    MissingLyricsJob().scan(ctx())
    MissingLyricsJob().scan(ctx())
    assert client.calls == ["Instrumental"]      # remembered despite no findings table
    conn = db._get_connection()
    conn.execute("DELETE FROM tracks WHERE id = 1")
    conn.commit(); conn.close()
    MissingLyricsJob().scan(ctx())               # completed scan prunes the entry
    conn = db._get_connection()
    left = conn.execute("SELECT COUNT(*) FROM repair_empty_lookups").fetchone()[0]
    conn.close()
    assert left == 0


def test_expired_absent_is_remembered_again_with_a_fresh_timestamp(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    ctx = lambda: _real_ctx(db, [], remember_empty_lookups=True)  # noqa: E731
    MissingLyricsJob().scan(ctx())
    conn = db._get_connection()
    conn.execute("UPDATE repair_empty_lookups SET checked_at = checked_at - 8 * 86400")
    conn.commit(); conn.close()
    MissingLyricsJob().scan(ctx())               # expired -> probed -> absent again
    assert client.calls == ["Instrumental", "Instrumental"]
    conn = db._get_connection()
    checked_at = conn.execute("SELECT checked_at FROM repair_empty_lookups").fetchone()[0]
    conn.close()
    assert checked_at > time.time() - 120        # re-remembered with a fresh stamp
    MissingLyricsJob().scan(ctx())
    assert client.calls == ["Instrumental", "Instrumental"]


def test_bool_recheck_days_falls_back_to_default(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "Instrumental", str(audio), 200000)])
    client = _probe_client({'Instrumental': 'absent'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    MissingLyricsJob().scan(_real_ctx(db, [], remember_empty_lookups=True))
    conn = db._get_connection()
    conn.execute("UPDATE repair_empty_lookups SET checked_at = checked_at - 3 * 86400")
    conn.commit(); conn.close()
    # True read as 1.0 day would re-probe a 3-day-old answer; the
    # 7-day fallback still trusts it.
    MissingLyricsJob().scan(_real_ctx(db, [], remember_empty_lookups=True, recheck_after_days=True))
    assert client.calls == ["Instrumental"]


def test_finding_cleared_mid_scan_is_not_refiled(tmp_path, monkeypatch):
    # The pending keys are loaded at scan start; if the user clears the
    # finding before the refresh runs, the bounded UPDATE touches zero
    # rows — it must NOT file a new, unverified finding.
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(
        tmp_path, [(1, "Song", str(audio), 200000)],
        findings_rows=[('missing_lyrics', 'missing_lyrics', 'pending', '1', str(audio), 'x')])
    client = _probe_client({'Song': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)

    def clearing_resolver(path, **kw):
        conn = db._get_connection()
        conn.execute("DELETE FROM repair_findings")
        conn.commit(); conn.close()
        return None

    monkeypatch.setattr(
        "core.repair_jobs.missing_lyrics.resolve_library_file_path", clearing_resolver)
    findings = []
    result = MissingLyricsJob().scan(_real_ctx(db, findings))
    assert client.calls == []                    # still never probed
    assert findings == []                        # nothing re-filed
    assert result.findings_created == 0
    conn = db._get_connection()
    left = conn.execute("SELECT COUNT(*) FROM repair_findings").fetchone()[0]
    conn.close()
    assert left == 0


def test_pending_track_with_sidecar_is_skipped_by_the_sidecar(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    (tmp_path / "a.lrc").write_text("[00:01]hi")
    db = _real_db(
        tmp_path, [(1, "Song", str(audio), 200000)],
        findings_rows=[('missing_lyrics', 'missing_lyrics', 'pending', '1', str(audio), 'x')])
    client = _probe_client({'Song': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    findings = []
    result = MissingLyricsJob().scan(_real_ctx(db, findings))
    assert client.calls == []
    assert findings == []
    assert (result.scanned, result.skipped) == (1, 1)


def test_refresh_updates_only_the_best_matching_row(tmp_path, monkeypatch):
    # Two pending rows could match one track (one by entity, one by
    # path, filed for a different track): only the worker-dedup winner
    # (entity match first) is refreshed — updating both would merge
    # two tracks' findings into duplicates of one track.
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(
        tmp_path, [(1, "Song", str(audio), 200000)],
        findings_rows=[
            ('missing_lyrics', 'missing_lyrics', 'pending', '1', '/old.flac', 'winner'),
            ('missing_lyrics', 'missing_lyrics', 'pending', '5', str(audio), 'loser'),
        ])
    client = _probe_client({'Song': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    MissingLyricsJob().scan(_real_ctx(db, []))
    conn = db._get_connection()
    rows = conn.execute(
        "SELECT entity_id, file_path FROM repair_findings ORDER BY id").fetchall()
    conn.close()
    assert (rows[0][0], rows[0][1]) == ('1', str(audio))   # winner refreshed
    assert (rows[1][0], rows[1][1]) == ('5', str(audio))   # loser untouched


def test_refresh_overwrites_stale_saved_details(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(tmp_path, [(1, "New Title", str(audio), 200000)])
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, severity, status, "
        "entity_type, entity_id, file_path, title, details_json) VALUES "
        "('missing_lyrics', 'missing_lyrics', 'info', 'pending', 'track', '1', ?, "
        "'Missing lyrics: Old Title', '{\"track_title\": \"Old Title\"}')", (str(audio),))
    conn.commit(); conn.close()
    client = _probe_client({'New Title': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    MissingLyricsJob().scan(_real_ctx(db, []))
    conn = db._get_connection()
    title, details = conn.execute(
        "SELECT title, details_json FROM repair_findings").fetchone()
    conn.close()
    assert title == 'Missing lyrics: New Title'
    assert '"track_title": "New Title"' in details


def test_early_stop_still_logs_the_pending_skip_count(tmp_path, monkeypatch, caplog):
    import logging
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    audio2 = tmp_path / "b.flac"; audio2.write_bytes(b"x")
    db = _real_db(
        tmp_path,
        [(1, "PendingSong", str(audio), 200000), (2, "Other", str(audio2), 200000)],
        findings_rows=[('missing_lyrics', 'missing_lyrics', 'pending', '1', str(audio), 'x')])
    client = _probe_client({'PendingSong': 'found', 'Other': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    ctx = _real_ctx(db, [])
    calls = {'n': 0}

    def check_stop():
        calls['n'] += 1
        return calls['n'] > 1

    ctx.check_stop = check_stop
    with caplog.at_level(logging.INFO):
        MissingLyricsJob().scan(ctx)
    assert any('tracks not probed' in r.message for r in caplog.records)


def test_unchanged_pending_row_is_not_rewritten(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(
        tmp_path, [(1, "Song", str(audio), 200000)],
        findings_rows=[('missing_lyrics', 'missing_lyrics', 'pending', '1', str(audio), 'x')])
    client = _probe_client({'Song': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    MissingLyricsJob().scan(_real_ctx(db, []))   # first scan refreshes the row
    conn = db._get_connection()
    conn.execute("UPDATE repair_findings SET updated_at = '2000-01-01 00:00:00'")
    conn.commit(); conn.close()
    result = MissingLyricsJob().scan(_real_ctx(db, []))
    conn = db._get_connection()
    stamp = conn.execute("SELECT updated_at FROM repair_findings").fetchone()[0]
    conn.close()
    # Second scan changed nothing, so the row was not rewritten (its
    # updated_at sentinel survives) — and the pending track counts as
    # scanned+skipped, not as a dedup-skipped finding.
    assert stamp == '2000-01-01 00:00:00'
    assert (result.scanned, result.skipped, result.findings_skipped_dedup) == (1, 1, 0)


def test_pending_finding_with_empty_file_path_does_not_suppress(tmp_path, monkeypatch):
    # An empty-string path identifies nothing: a pending finding filed
    # under a stale id with file_path='' must not suppress the probe.
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(
        tmp_path, [(1, "Song", str(audio), 200000)],
        findings_rows=[('missing_lyrics', 'missing_lyrics', 'pending', '999', '', 'x')])
    client = _probe_client({'Song': 'found'})
    monkeypatch.setattr("core.lyrics_client.lyrics_client", client)
    MissingLyricsJob().scan(_real_ctx(db, []))
    assert client.calls == ["Song"]


def test_refresh_never_touches_a_non_pending_row(tmp_path, monkeypatch):
    audio = tmp_path / "a.flac"; audio.write_bytes(b"x")
    db = _real_db(
        tmp_path, [(1, "Song", str(audio), 200000)],
        findings_rows=[('missing_lyrics', 'missing_lyrics', 'resolved', '1', str(audio), 'keep me')])
    job = MissingLyricsJob()
    ctx = _real_ctx(db, [])
    payload = job._finding_args(1, "Song", "Artist", "Album", str(audio), 200)
    job._refresh_pending_findings(ctx, [payload])
    conn = db._get_connection()
    title = conn.execute("SELECT title FROM repair_findings").fetchone()[0]
    conn.close()
    assert title == 'keep me'
