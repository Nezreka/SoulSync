"""the background loudness measurer (core/library2/loudness_worker.py):
files whose tags carry ReplayGain are read, never measured; the rest are
measured an album at a time and the album gets its album gain; failures
are counted and given up on; a played song's album jumps the queue."""

from __future__ import annotations

from core import loudness as L
from core.library2 import loudness_worker as W


def _views(conn):
    """Views' two tracks with a file each: (album_id, [(track_id, file_id, path)])"""
    album = conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0]
    rows = conn.execute("SELECT id, title FROM lib2_tracks WHERE album_id=? ORDER BY track_number",
                        (album,)).fetchall()
    hotline = [r["id"] for r in rows if r["title"] == "Hotline Bling"][0]
    conn.execute("INSERT INTO lib2_track_files(track_id, path, format) VALUES(?, '/m/02.flac', 'flac')", (hotline,))
    conn.commit()
    files = []
    for r in rows:
        f = conn.execute("SELECT id, path FROM lib2_track_files WHERE track_id=? ORDER BY id LIMIT 1",
                         (r["id"],)).fetchone()
        files.append((r["id"], f["id"], f["path"]))
    return album, files


def _no_tags(monkeypatch, tagged=None):
    tagged = tagged or {}
    monkeypatch.setattr("core.replaygain.read_replaygain_tags", lambda path: tagged.get(path, {}))


def test_an_album_is_measured_and_given_its_album_gain(legacy_db, imported_conn, monkeypatch):
    album, files = _views(imported_conn)
    _no_tags(monkeypatch)
    loud = {"/m/01.flac": {"integrated_lufs": -10.0, "true_peak_db": -1.0},
            "/m/02.flac": {"integrated_lufs": -14.0, "true_peak_db": -3.0}}
    out = W.measure_album(legacy_db, album, measure=lambda p: loud[p], resolve=lambda p: p, reference=-18.0)
    assert out["measured"] == 2 and out["failed"] == 0
    got = L.for_files(imported_conn, [f[1] for f in files], reference=-18.0)
    first, second = got[files[0][1]], got[files[1][1]]
    assert first["track_gain"] == -8.0 and second["track_gain"] == -4.0
    assert first["source"] == "measured"
    # one album gain for both, from the energy mean, and the album's loudest peak
    assert first["album_gain"] == second["album_gain"]
    assert -7.0 < first["album_gain"] < -6.0
    assert first["album_peak"] == second["album_peak"] == round(10 ** (-1 / 20), 6)


def test_a_file_whose_tags_have_gain_is_never_measured(legacy_db, imported_conn, monkeypatch):
    album, files = _views(imported_conn)
    _no_tags(monkeypatch, {"/m/01.flac": {"track_gain": "-6.00 dB", "track_peak": "0.9"}})
    measured = []

    def measure(p):
        measured.append(p)
        return {"integrated_lufs": -16.0, "true_peak_db": -2.0}

    out = W.measure_album(legacy_db, album, measure=measure, resolve=lambda p: p, reference=-18.0)
    assert measured == ["/m/02.flac"] and out["from_tags"] == 1
    got = L.for_files(imported_conn, [f[1] for f in files], reference=-18.0)
    assert got[files[0][1]]["track_gain"] == -6.0 and got[files[0][1]]["album_gain"] is not None
    # the file read is kept: the next pass skips it
    assert W.next_backlog_file(imported_conn, 0) is None or \
        W.next_backlog_file(imported_conn, 0)["id"] not in {f[1] for f in files}


def test_failures_are_counted_then_left_alone(legacy_db, imported_conn, monkeypatch):
    album, files = _views(imported_conn)
    _no_tags(monkeypatch)

    def broken(p):
        raise RuntimeError("ffmpeg couldn't read it")

    for _ in range(W.MAX_ATTEMPTS):
        W.measure_album(legacy_db, album, measure=broken, resolve=lambda p: p, reference=-18.0)
    row = imported_conn.execute(f"SELECT attempts, source, error FROM {L.TABLE} WHERE file_id=?",
                                (files[0][1],)).fetchone()
    assert (row["attempts"], row["source"]) == (W.MAX_ATTEMPTS, "failed") and "ffmpeg" in row["error"]
    calls = []
    W.measure_album(legacy_db, album, measure=lambda p: calls.append(p), resolve=lambda p: p, reference=-18.0)
    assert calls == []
    # a failure isn't loudness: the player gets nothing for it
    assert L.for_files(imported_conn, [files[0][1]], reference=-18.0) == {}


def test_a_file_out_of_reach_is_skipped_for_the_next_pass(legacy_db, imported_conn, monkeypatch):
    album, files = _views(imported_conn)
    _no_tags(monkeypatch)
    out = W.measure_album(legacy_db, album, measure=lambda p: {"integrated_lufs": -9.0, "true_peak_db": 0.0},
                          resolve=lambda p: None, reference=-18.0)
    assert out == {"measured": 0, "from_tags": 0, "failed": 0, "album": 0}
    assert W.next_backlog_file(imported_conn, 0) is not None


def test_the_backlog_walks_by_id_and_skips_tagged_and_measured(imported_conn):
    rows = imported_conn.execute("SELECT id FROM lib2_track_files ORDER BY id").fetchall()
    ids = [r["id"] for r in rows]
    assert W.next_backlog_file(imported_conn, 0)["id"] == ids[0]
    imported_conn.execute("UPDATE lib2_track_files SET tags_json='{\"replaygain_track_gain\": \"-5 dB\"}' "
                          "WHERE id=?", (ids[0],))
    imported_conn.commit()
    nxt = W.next_backlog_file(imported_conn, 0)
    assert nxt is None or nxt["id"] != ids[0]
    assert W.next_backlog_file(imported_conn, ids[-1]) is None


def test_a_played_song_jumps_the_queue_and_the_worker_pauses(legacy_db):
    worker = W.LoudnessWorker(legacy_db, None, ffmpeg_ok=lambda: True)
    worker.bump(5, 50)
    worker.bump(6, 60)
    worker.bump(5, 51)
    assert worker._next() == (5, 51)
    assert worker._next() == (6, 60)
    worker.pause()
    assert worker.paused and worker.status()["paused"]
    worker.resume()
    assert not worker.paused


def test_the_worker_is_paused_by_a_scan_like_the_others(legacy_db, monkeypatch):
    from api import database_admin
    for name in [n for n in vars(database_admin) if n.startswith("_") and n.endswith("_worker")
                 and n != "_loudness_worker" and getattr(database_admin, n) is None]:
        monkeypatch.setattr(database_admin, name, lambda: None)
    worker = W.LoudnessWorker(legacy_db, None)
    monkeypatch.setattr(W, "_worker", worker)
    assert database_admin._background_workers()["loudness"] is worker
