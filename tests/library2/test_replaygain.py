"""Album-level ReplayGain orchestration for Library v2.

The heavy FFmpeg analysis and mutagen tag-writing (core/replaygain.py) are
injected so this test covers the orchestration: path resolution, the two-pass
album-gain computation, per-file failure handling, and stats — without running
real FFmpeg.
"""

from __future__ import annotations

from core.library2 import replaygain as RG
from core.replaygain import RG_REFERENCE_LUFS


def _seed_two_present_files(conn):
    """Give Views' two tracks a present file each; return (id1, id2, paths)."""
    rows = conn.execute(
        """SELECT t.id, t.title FROM lib2_tracks t
             JOIN lib2_albums al ON al.id=t.album_id
            WHERE al.title='Views' ORDER BY t.track_number"""
    ).fetchall()
    ids = {r["title"]: r["id"] for r in rows}
    one_dance = ids["One Dance"]  # already has /m/01.flac from the importer
    hotline = ids["Hotline Bling"]
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, format) VALUES(?, '/m/02.flac', 'flac')",
        (hotline,),
    )
    conn.commit()
    return one_dance, hotline


def _fake_analyze(lufs_by_path):
    def _analyze(path):
        return lufs_by_path[path], -1.0  # (lufs, peak_dbfs)

    return _analyze


def test_album_replaygain_computes_album_gain_and_writes_all(imported_conn):
    _seed_two_present_files(imported_conn)
    writes = []

    result = RG.analyze_album_replaygain(
        imported_conn,
        imported_conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0],
        analyze_fn=_fake_analyze({"/m/01.flac": -20.0, "/m/02.flac": -16.0}),
        write_fn=lambda path, tg, tp, ag, ap: writes.append((path, tg, ag, ap)) or True,
        resolve_fn=lambda p: p,
    )

    assert result["analyzed"] == 2
    assert result["failed"] == 0
    # mean lufs = -18 → album gain = REFERENCE - (-18)
    assert result["album_gain_db"] == RG_REFERENCE_LUFS - (-18.0)
    # both tracks written with the SAME album gain + album peak (max of -1.0)
    assert len(writes) == 2
    assert {w[2] for w in writes} == {RG_REFERENCE_LUFS - (-18.0)}
    assert {w[3] for w in writes} == {-1.0}


def test_album_replaygain_persists_tag_cache_by_file_id_not_resolved_path(
        imported_conn, monkeypatch):
    """G2: lib2_track_files.path stores the media-server view (§1 invariant);
    on a path-mapped setup (e.g. Docker: /music/... vs the local mount) the
    resolved filesystem path never matches a raw ``WHERE path=?`` lookup, so
    the tag cache would silently never refresh after an album ReplayGain
    write and the RG badge stays grey. file_id must travel with each entry
    instead of being re-looked-up by path."""
    one_dance, hotline = _seed_two_present_files(imported_conn)
    calls = []
    monkeypatch.setattr(
        "core.library2.tag_cache.read_and_persist_tag_cache",
        lambda conn, file_id, path: calls.append((file_id, path)) or True,
    )
    mapped = {"/m/01.flac": "/mapped/root/01.flac", "/m/02.flac": "/mapped/root/02.flac"}

    RG.analyze_album_replaygain(
        imported_conn,
        imported_conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0],
        analyze_fn=_fake_analyze({mapped["/m/01.flac"]: -20.0, mapped["/m/02.flac"]: -16.0}),
        write_fn=lambda *a: True,
        resolve_fn=lambda p: mapped[p],
    )

    expected_file_ids = {
        row["id"] for row in imported_conn.execute(
            "SELECT id FROM lib2_track_files WHERE track_id IN (?,?)", (one_dance, hotline)
        )
    }
    assert {c[0] for c in calls} == expected_file_ids
    assert {c[1] for c in calls} == set(mapped.values())


def test_album_replaygain_uses_per_track_gain(imported_conn):
    _seed_two_present_files(imported_conn)
    writes = {}

    RG.analyze_album_replaygain(
        imported_conn,
        imported_conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0],
        analyze_fn=_fake_analyze({"/m/01.flac": -20.0, "/m/02.flac": -16.0}),
        write_fn=lambda path, tg, tp, ag, ap: writes.__setitem__(path, tg) or True,
        resolve_fn=lambda p: p,
    )

    assert writes["/m/01.flac"] == RG_REFERENCE_LUFS - (-20.0)
    assert writes["/m/02.flac"] == RG_REFERENCE_LUFS - (-16.0)


def test_album_replaygain_skips_unresolvable_files(imported_conn):
    _seed_two_present_files(imported_conn)
    writes = []

    result = RG.analyze_album_replaygain(
        imported_conn,
        imported_conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0],
        analyze_fn=_fake_analyze({"/m/01.flac": -20.0}),
        write_fn=lambda *a: writes.append(a) or True,
        resolve_fn=lambda p: None if p == "/m/02.flac" else p,
    )

    assert result["analyzed"] == 1
    assert result["failed"] == 1
    assert len(writes) == 1
    assert any("Hotline Bling" == e["track"] for e in result["errors"])


def test_album_replaygain_records_analysis_errors(imported_conn):
    _seed_two_present_files(imported_conn)

    def _analyze(path):
        if path == "/m/02.flac":
            raise RuntimeError("ffmpeg boom")
        return -20.0, -1.0

    result = RG.analyze_album_replaygain(
        imported_conn,
        imported_conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0],
        analyze_fn=_analyze,
        write_fn=lambda *a: True,
        resolve_fn=lambda p: p,
    )

    assert result["analyzed"] == 1
    assert result["failed"] == 1
    assert any("ffmpeg boom" in e["error"] for e in result["errors"])


def test_album_replaygain_no_present_files(imported_conn):
    # Views' One Dance file exists in the seed; blank it so the album is empty.
    imported_conn.execute("DELETE FROM lib2_track_files")
    imported_conn.commit()

    result = RG.analyze_album_replaygain(
        imported_conn,
        imported_conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0],
        analyze_fn=lambda p: (-20.0, -1.0),
        write_fn=lambda *a: True,
        resolve_fn=lambda p: p,
    )

    assert result["total"] == 0
    assert result["analyzed"] == 0
    assert result["album_gain_db"] is None


def test_single_track_replaygain_writes_track_gain(imported_conn):
    track_id = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()[0]
    writes = []

    result = RG.analyze_track_replaygain(
        imported_conn,
        track_id,
        analyze_fn=lambda p: (-14.0, -0.5),
        write_fn=lambda path, tg, tp, ag, ap: writes.append((path, tg, ag)) or True,
        resolve_fn=lambda p: p,
    )

    assert result["analyzed"] is True
    assert result["track_gain_db"] == RG_REFERENCE_LUFS - (-14.0)
    # A single track has no album gain.
    assert writes[0][2] is None


def test_single_track_replaygain_reports_missing_file(imported_conn):
    track_id = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()[0]

    result = RG.analyze_track_replaygain(
        imported_conn,
        track_id,
        analyze_fn=lambda p: (-14.0, -0.5),
        write_fn=lambda *a: True,
        resolve_fn=lambda p: None,
    )

    assert result["analyzed"] is False
    assert result["error"]


def test_single_track_replaygain_fileless_track(imported_conn):
    # Legacy seed track 101 has no file.
    track_id = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=101"
    ).fetchone()[0]

    result = RG.analyze_track_replaygain(
        imported_conn,
        track_id,
        analyze_fn=lambda p: (-14.0, -0.5),
        write_fn=lambda *a: True,
        resolve_fn=lambda p: p,
    )

    assert result["analyzed"] is False


def test_album_replaygain_reports_progress(imported_conn):
    _seed_two_present_files(imported_conn)
    seen = []

    RG.analyze_album_replaygain(
        imported_conn,
        imported_conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0],
        analyze_fn=_fake_analyze({"/m/01.flac": -20.0, "/m/02.flac": -16.0}),
        write_fn=lambda *a: True,
        resolve_fn=lambda p: p,
        progress=lambda current, total, title: seen.append((current, total)),
    )

    assert seen  # progress was reported
    assert seen[-1][1] == 2  # total tracks
