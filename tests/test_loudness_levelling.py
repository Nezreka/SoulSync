"""loudness levelling: ReplayGain tags read and written for every format
(Opus as R128, RFC 7845), the one client shape, and the gain rule the web
player, the Android app and the server share (core/loudness.py)."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from core import loudness as L
from core import replaygain as RG

FIXTURES = Path(__file__).parent / "fixtures" / "audio"


def _copy(tmp_path, name):
    dst = tmp_path / name
    shutil.copyfile(FIXTURES / name, dst)
    return str(dst)


# ── tags in files ────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["silence.mp3", "silence.flac", "silence.m4a", "silence.ogg"])
def test_replaygain_round_trips_for_each_format(tmp_path, name):
    path = _copy(tmp_path, name)
    assert RG.write_replaygain_tags(path, -7.31, -0.5, -8.02, -0.2)
    got = RG.read_replaygain_tags(path)
    assert got["track_gain"] == "-7.31 dB" and got["album_gain"] == "-8.02 dB"
    assert float(got["track_peak"]) == pytest.approx(10 ** (-0.5 / 20), abs=1e-6)
    assert float(got["album_peak"]) == pytest.approx(10 ** (-0.2 / 20), abs=1e-6)
    shaped = L.from_tags(got)
    assert shaped["track_gain"] == -7.31 and shaped["album_gain"] == -8.02


def test_opus_gets_r128_not_replaygain(tmp_path):
    from mutagen.oggopus import OggOpus
    path = _copy(tmp_path, "silence.opus")
    assert RG.write_replaygain_tags(path, -7.31, -0.5, -8.02, -0.2)
    raw = OggOpus(path)
    # Q7.8 relative to -23 LUFS: -7.31 dB at -18 is -12.31 dB at -23
    assert raw["R128_TRACK_GAIN"] == [str(round(-12.31 * 256))]
    assert raw["R128_ALBUM_GAIN"] == [str(round(-13.02 * 256))]
    assert "REPLAYGAIN_TRACK_GAIN" not in raw and "REPLAYGAIN_ALBUM_GAIN" not in raw
    # peaks have no R128 tag: kept for clipping prevention
    assert float(raw["REPLAYGAIN_TRACK_PEAK"][0]) == pytest.approx(10 ** (-0.5 / 20), abs=1e-6)
    got = RG.read_replaygain_tags(path)
    assert got["track_gain"] == "-7.31 dB" and got["album_gain"] == "-8.02 dB"


def test_opus_old_replaygain_is_replaced_and_r128_wins_on_read(tmp_path):
    from mutagen.oggopus import OggOpus
    path = _copy(tmp_path, "silence.opus")
    raw = OggOpus(path)
    raw["REPLAYGAIN_TRACK_GAIN"] = ["-3.00 dB"]
    raw["REPLAYGAIN_ALBUM_GAIN"] = ["-4.00 dB"]
    raw.save()
    # read both: the old form alone still reads
    assert RG.read_replaygain_tags(path)["track_gain"] == "-3.00 dB"
    raw = OggOpus(path)
    raw["R128_TRACK_GAIN"] = [str(-2560)]          # -10 dB at -23 = -5 dB at -18
    raw.save()
    assert RG.read_replaygain_tags(path)["track_gain"] == "-5.00 dB"
    assert RG.read_replaygain_tags(path)["album_gain"] == "-4.00 dB"
    # a write takes the old gains off, so no player applies a gain twice
    assert RG.write_replaygain_tags(path, -6.0, -1.0, -6.5, -1.0)
    raw = OggOpus(path)
    assert "REPLAYGAIN_TRACK_GAIN" not in raw and "REPLAYGAIN_ALBUM_GAIN" not in raw
    assert RG.read_replaygain_tags(path)["track_gain"] == "-6.00 dB"


def test_a_track_only_opus_write_keeps_the_album_gain(tmp_path):
    from mutagen.oggopus import OggOpus
    path = _copy(tmp_path, "silence.opus")
    assert RG.write_replaygain_tags(path, -7.0, -1.0, -8.0, -1.0)
    assert RG.write_replaygain_tags(path, -6.0, -1.0)
    raw = OggOpus(path)
    assert raw["R128_ALBUM_GAIN"] == [str(round(-13.0 * 256))]
    assert RG.read_replaygain_tags(path)["track_gain"] == "-6.00 dB"


def test_r128_conversion_edges():
    assert RG.rg_gain_to_r128(0.0) == -1280
    assert RG.r128_to_rg_gain("-1280") == 0.0
    assert RG.r128_to_rg_gain(" 256 ") == 6.0
    assert RG.rg_gain_to_r128(500) == 32767 and RG.rg_gain_to_r128(-500) == -32768
    for junk in (None, "", "abc", "1.5", "99999"):
        assert RG.r128_to_rg_gain(junk) is None
    # a target other than -18 (#1060) rides along both ways
    assert RG.r128_to_rg_gain(str(RG.rg_gain_to_r128(-3.37))) == pytest.approx(-3.37, abs=0.01)


def test_the_tag_reader_feeds_the_tag_cache(tmp_path):
    """a rescan reads RG back (so nothing is measured twice), R128 included"""
    from core.tag_writer import read_file_tags
    path = _copy(tmp_path, "silence.opus")
    RG.write_replaygain_tags(path, -9.5, -1.0, -10.0, -0.5)
    tags = read_file_tags(path)
    assert tags["replaygain_track_gain"] == "-9.50 dB"
    from core.library2.tag_cache import normalized_tag_snapshot
    snap = normalized_tag_snapshot(tags)
    assert L.from_tags(snap)["track_gain"] == -9.5 and L.from_tags(snap)["album_gain"] == -10.0


def test_ebur128_summary_gives_loudness_peak_and_range():
    out = ("[Parsed_ebur128_0 @ 0x1] t: 0.4 M: -70.0 S: -70.0 I: -70.0 LUFS LRA: 0.0 LU\n"
           "[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n    I:         -14.3 LUFS\n"
           "    Threshold: -24.6 LUFS\n\n  Loudness range:\n    LRA:         6.2 LU\n"
           "    Threshold: -34.4 LUFS\n    LRA low:   -19.1 LUFS\n    LRA high:  -12.9 LUFS\n\n"
           "  True peak:\n    Peak:        -0.4 dBFS\n")
    assert RG._parse_ebur128(out) == {"integrated_lufs": -14.3, "true_peak_db": -0.4, "lra": 6.2}
    assert RG._parse_ebur128("nothing here") is None


def test_measure_loudness_runs_low_priority_on_one_audio_stream(monkeypatch):
    seen = {}

    class Done:
        returncode = 0
        stderr = "Summary:\n I: -23.5 LUFS\n LRA: 12.0 LU\n Peak: -3.0 dBFS\n"

    def fake_run(cmd, **kw):
        seen["cmd"], seen["timeout"] = cmd, kw.get("timeout")
        return Done()

    monkeypatch.setattr(RG.subprocess, "run", fake_run)
    monkeypatch.setattr(RG, "_low_priority_prefix", lambda: ["nice", "-n", "19"])
    got = RG.measure_loudness("/v/film.mkv", audio_stream=0, timeout=900)
    assert got == {"integrated_lufs": -23.5, "true_peak_db": -3.0, "lra": 12.0}
    cmd = seen["cmd"]
    assert cmd[:3] == ["nice", "-n", "19"] and seen["timeout"] == 900
    assert "-vn" in cmd and cmd[cmd.index("-map") + 1] == "0:a:0"
    assert cmd[cmd.index("-threads") + 1] == "1"

    class Silent(Done):
        stderr = "Summary:\n I: -70.0 LUFS\n Peak: -90.0 dBFS\n"

    monkeypatch.setattr(RG.subprocess, "run", lambda cmd, **kw: Silent())
    with pytest.raises(RuntimeError):
        RG.measure_loudness("/v/silence.flac")


# ── the shape ────────────────────────────────────────────────────────────

def test_parsing_is_forgiving_but_refuses_junk():
    assert L.parse_gain("-7.31 dB") == -7.31
    assert L.parse_gain("+1.5") == 1.5
    assert L.parse_gain("-3,20 dB") == -3.2
    assert L.parse_gain(2) == 2.0
    for junk in (None, "", "loud", "+52.00 dB", True):
        assert L.parse_gain(junk) is None
    assert L.parse_peak("0.988123") == 0.988123
    assert L.parse_peak("1.05") == 1.05
    for junk in (None, "", "0", "-1", "99", "peak"):
        assert L.parse_peak(junk) is None


def test_tags_come_first_and_a_measurement_fills_what_they_lack():
    tags = {"track_gain": -5.0, "track_peak": 0.9, "album_gain": None, "album_peak": None}
    measured = {"track_gain": -9.0, "track_peak": 0.5, "album_gain": -7.0, "album_peak": 0.95}
    values, source = L.merge(tags, measured)
    assert values == {"track_gain": -5.0, "track_peak": 0.9, "album_gain": -7.0, "album_peak": 0.95}
    assert source == "tags"
    values, source = L.merge({k: None for k in tags}, measured)
    assert values == measured and source == "measured"
    assert L.public({k: None for k in tags}, -18.0, None) is None


def _files_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE lib2_track_files (id INTEGER PRIMARY KEY, track_id INTEGER, path TEXT,
                    size INTEGER, tags_json TEXT, import_status TEXT, file_state TEXT,
                    quality_tier TEXT, bitrate INTEGER)""")
    L.ensure_schema(conn)
    return conn


def test_for_files_reads_the_tag_cache_then_the_measurement():
    conn = _files_db()
    conn.execute("INSERT INTO lib2_track_files (id, track_id, path, size, tags_json) VALUES "
                 "(1, 10, '/a.flac', 5, '{\"replaygain_track_gain\": \"-6.50 dB\", "
                 "\"replaygain_track_peak\": \"0.900000\"}')")
    conn.execute("INSERT INTO lib2_track_files (id, track_id, path, size, tags_json) VALUES "
                 "(2, 11, '/b.flac', 5, '{}'), (3, 12, '/c.flac', 5, 'not json')")
    conn.execute(f"INSERT INTO {L.TABLE} (file_id, track_id, track_gain, track_peak, album_gain, album_peak, "
                 "reference_lufs, source) VALUES (2, 11, -4.0, 0.7, -5.0, 0.8, -14.0, 'measured')")
    got = L.for_files(conn, [1, 2, 3], reference=-18.0)
    assert got[1] == {"track_gain": -6.5, "track_peak": 0.9, "album_gain": None, "album_peak": None,
                      "reference_lufs": -18.0, "source": "tags"}
    # measured against -14 once: moved to today's -18
    assert got[2]["track_gain"] == -8.0 and got[2]["album_gain"] == -9.0 and got[2]["source"] == "measured"
    assert 3 not in got


def test_for_file_reads_an_uncached_file_once_and_keeps_it(tmp_path):
    conn = _files_db()
    path = _copy(tmp_path, "silence.flac")
    RG.write_replaygain_tags(path, -5.0, -1.0)
    conn.execute("INSERT INTO lib2_track_files (id, track_id, path, size, tags_json) VALUES (7, 70, ?, 1, '{}')",
                 (path,))
    got = L.for_file(conn, 7, path, reference=-18.0)
    assert got["track_gain"] == -5.0 and got["source"] == "tags"
    assert conn.execute(f"SELECT source FROM {L.TABLE} WHERE file_id = 7").fetchone()[0] == "tags"
    # the next play doesn't open the file
    assert L.for_file(conn, 7, "/nowhere.flac", reference=-18.0)["track_gain"] == -5.0


def test_another_file_at_the_row_drops_the_old_measurement():
    conn = _files_db()
    conn.execute("INSERT INTO lib2_track_files (id, track_id, path, size, tags_json) VALUES (1, 1, '/a', 5, '{}')")
    conn.execute(f"INSERT INTO {L.TABLE} (file_id, track_gain, source) VALUES (1, -3, 'measured')")
    conn.execute("UPDATE lib2_track_files SET tags_json = '{\"x\": 1}' WHERE id = 1")
    assert conn.execute(f"SELECT COUNT(*) FROM {L.TABLE}").fetchone()[0] == 1
    conn.execute("UPDATE lib2_track_files SET path = '/a-upgraded' WHERE id = 1")
    assert conn.execute(f"SELECT COUNT(*) FROM {L.TABLE}").fetchone()[0] == 0
    conn.execute(f"INSERT INTO {L.TABLE} (file_id, track_gain, source) VALUES (1, -3, 'measured')")
    conn.execute("DELETE FROM lib2_track_files WHERE id = 1")
    assert conn.execute(f"SELECT COUNT(*) FROM {L.TABLE}").fetchone()[0] == 0


def test_for_tracks_picks_the_file_the_player_plays():
    conn = _files_db()
    conn.execute("INSERT INTO lib2_track_files (id, track_id, path, size, tags_json, quality_tier, bitrate) VALUES "
                 "(1, 5, '/a.mp3', 1, '{\"replaygain_track_gain\": \"-2 dB\"}', 'lossy', 320), "
                 "(2, 5, '/a.flac', 1, '{\"replaygain_track_gain\": \"-3 dB\"}', 'lossless', 900), "
                 "(3, 6, '/b.mp3', 1, '{}', 'lossy', 320)")
    got = L.for_tracks(conn, [5, 6, 99], reference=-18.0)
    assert got[5]["track_gain"] == -3.0 and got[6] is None and got[99] is None


def test_headers_carry_the_same_numbers():
    h = L.headers({"track_gain": -7.31, "track_peak": 0.988, "album_gain": None, "album_peak": None,
                   "reference_lufs": -18.0})
    assert h == {"X-Loudness-Reference": "-18.00", "X-ReplayGain-Track-Gain": "-7.31",
                 "X-ReplayGain-Track-Peak": "0.988000"}
    assert L.headers(None) == {}


# ── the rule every player applies ───────────────────────────────────────

LOUD = {"track_gain": -7.0, "track_peak": 0.5, "album_gain": -9.0, "album_peak": 0.6, "reference_lufs": -18.0}


@pytest.mark.parametrize("mode,album_ctx,want", [
    ("off", True, 0.0), ("track", True, -7.0), ("album", False, -9.0),
    ("smart", True, -9.0), ("smart", False, -7.0),
])
def test_level_by_mode(mode, album_ctx, want):
    assert L.level_db(LOUD, mode=mode, album_context=album_ctx, target=-18.0) == want


def test_level_target_preamp_and_a_hotter_library():
    assert L.level_db(LOUD, mode="track", album_context=False, target=-14.0) == -3.0
    assert L.level_db(LOUD, mode="track", album_context=False, target=-18.0, preamp=2.5) == -4.5
    assert L.level_db({**LOUD, "reference_lufs": -14.0}, mode="track", album_context=False, target=-18.0) == -11.0


def test_level_falls_back_between_album_and_track():
    assert L.level_db({**LOUD, "album_gain": None}, mode="album", album_context=True, target=-18.0) == -7.0
    assert L.level_db({**LOUD, "track_gain": None}, mode="track", album_context=False, target=-18.0) == -9.0


def test_level_peak_limiting():
    quiet = {**LOUD, "track_gain": 8.0, "track_peak": 0.5}
    assert L.level_db(quiet, mode="track", album_context=False, target=-18.0) == pytest.approx(6.02, abs=0.01)
    assert L.level_db(quiet, mode="track", album_context=False, target=-18.0, prevent_clipping=False) == 8.0
    no_peak = {**LOUD, "track_gain": 4.0, "track_peak": None}
    assert L.level_db(no_peak, mode="track", album_context=False, target=-18.0) == 0.0
    assert L.level_db({**no_peak, "track_gain": -4.0}, mode="track", album_context=False, target=-18.0) == -4.0


def test_level_unknown_is_turned_down_never_up():
    assert L.level_db(None, mode="smart", album_context=False, target=-18.0) == -6.0
    assert L.level_db(None, mode="smart", album_context=False, target=-14.0) == -2.0
    assert L.level_db(None, mode="smart", album_context=False, target=-10.0, preamp=6.0) == 0.0
    assert L.level_db(None, mode="off", album_context=False, target=-18.0) == 0.0


def test_album_loudness_is_the_length_weighted_energy_mean():
    assert L.album_loudness([-10.0, -10.0]) == -10.0
    # a loud long song outweighs a quiet interlude
    assert L.album_loudness([-8.0, -30.0], [540, 40]) == pytest.approx(-8.3, abs=0.05)
    assert L.album_loudness([-8.0, -30.0]) == pytest.approx(-11.0, abs=0.05)
    assert L.album_loudness([]) is None


# ── the profile setting ──────────────────────────────────────────────────

def test_player_settings_hold_levelling(tmp_path):
    from core.player import settings as S

    class Db:
        database_path = str(tmp_path / "p.db")

        def _get_connection(self):
            return sqlite3.connect(self.database_path)

    db = Db()
    got = S.get(db, 1)
    assert got["loudness_mode"] == "smart" and got["loudness_target"] == -18.0
    assert got["loudness_preamp"] == 0.0 and got["loudness_prevent_clipping"] is True
    assert got["video_loudness"] is False
    out = S.put(db, 1, {"loudness_mode": "album", "loudness_target": -14, "loudness_preamp": 1.3,
                        "video_loudness": True})
    assert out["loudness_mode"] == "album" and out["loudness_target"] == -14.0
    assert out["loudness_preamp"] == 1.5           # to the half dB
    for bad in ({"loudness_mode": "loud"}, {"loudness_target": -40}, {"loudness_preamp": 20},
                {"loudness_preamp": True}, {"video_loudness": "yes"}):
        with pytest.raises(S.SettingsError):
            S.put(db, 1, bad)
