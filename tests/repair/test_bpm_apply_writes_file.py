"""applying a BPM Backfill finding puts the bpm in the file, not only the db
(discord, Specialmed: "the found BPM is not written into the files").

_fix_metadata_gap wrote lib2_tracks.bpm and stopped, so players and media servers
never saw it, though the job's help text said file tags got updated. these
apply a finding against a real database and real mp3/flac/m4a files and read
the tag back with mutagen.
"""

import os
import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf
from mutagen import File as MutagenFile

from core.repair_worker import RepairWorker
from tests.lib2_seed import file_track

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Cfg:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _tone(seconds=1.0, sr=22050):
    return (0.1 * np.sin(2 * np.pi * 440 * np.arange(int(seconds * sr)) / sr)).astype(np.float32), sr


def _mp3(path):
    y, sr = _tone()
    sf.write(str(path), y, sr, format="MP3")


def _flac(path):
    y, sr = _tone()
    sf.write(str(path), y, sr, format="FLAC")


def _m4a(path):
    ffmpeg = shutil.which("ffmpeg") or os.path.join(ROOT, "tools", "ffmpeg")
    if not os.path.isfile(ffmpeg):
        pytest.skip("no ffmpeg to make an m4a")
    subprocess.run([ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:a", "aac", str(path)], check=True)


def _read_bpm(path):
    audio = MutagenFile(str(path))
    if path.suffix == ".mp3":
        return str(audio.tags["TBPM"].text[0])
    if path.suffix == ".flac":
        return audio["bpm"][0]
    return str(audio["tmpo"][0])


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music.db"))
    import database.music_database as mdb
    monkeypatch.setattr(mdb, "_database_instances", {})
    db = mdb.get_database()
    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w.transfer_folder = str(tmp_path)
    w._config_manager = _Cfg()

    def add_track(path):
        conn = db._get_connection()
        try:
            conn.execute("INSERT OR IGNORE INTO lib2_artists (id, name) VALUES (1, 'A')")
            conn.execute("INSERT OR IGNORE INTO lib2_albums (id, primary_artist_id, title) VALUES (1, 1, 'B')")
            file_track(conn, 1, 1, 'Song', str(path))
            conn.commit()
        finally:
            conn.close()
    return w, db, add_track


def _apply(w, path, bpm=127.6):
    return w._fix_metadata_gap("track", "lib2:1", str(path), {"found_fields": {"bpm": bpm}})


@pytest.mark.parametrize("ext, make", [(".mp3", _mp3), (".flac", _flac), (".m4a", _m4a)])
def test_applied_bpm_lands_in_the_file_and_the_db(worker, tmp_path, ext, make):
    w, db, add_track = worker
    path = tmp_path / ("song" + ext)
    make(path)
    add_track(path)

    out = _apply(w, path)

    assert out["success"], out
    assert "written to the file" in out["message"]
    assert _read_bpm(path) == "128"                     # rounded, not cut to 127
    conn = db._get_connection()
    try:
        assert conn.execute("SELECT bpm FROM lib2_tracks WHERE id = 1").fetchone()[0] == pytest.approx(127.6)
    finally:
        conn.close()


def test_only_the_bpm_tag_changes(worker, tmp_path):
    w, _, add_track = worker
    path = tmp_path / "song.flac"
    _flac(path)
    audio = MutagenFile(str(path))
    audio["title"] = ["Leuchtturm"]
    audio["artist"] = ["Nena"]
    audio.save()
    add_track(path)

    _apply(w, path)

    audio = MutagenFile(str(path))
    assert audio["title"] == ["Leuchtturm"] and audio["artist"] == ["Nena"]
    assert audio["bpm"] == ["128"]


def test_bpm_tags_turned_off_leaves_the_file_alone(worker, tmp_path):
    w, _, add_track = worker
    w._config_manager = _Cfg(**{"deezer.tags.bpm": False})
    path = tmp_path / "song.flac"
    _flac(path)
    add_track(path)

    out = _apply(w, path)

    assert out["success"]
    assert "turned off" in out["message"] or "are off" in out["message"]
    assert "bpm" not in MutagenFile(str(path))


def test_an_unreachable_file_still_saves_the_bpm_and_says_so(worker, tmp_path):
    w, db, add_track = worker
    gone = tmp_path / "nowhere" / "song.flac"
    add_track(gone)

    out = _apply(w, gone)

    assert out["success"]
    assert "not reachable" in out["message"]
    conn = db._get_connection()
    try:
        assert conn.execute("SELECT bpm FROM lib2_tracks WHERE id = 1").fetchone()[0] == pytest.approx(127.6)
    finally:
        conn.close()


def test_manual_bpm_override_survives_applying_a_provider_finding(worker, tmp_path):
    from core.library2.metadata_overrides import get_field_overrides, set_field_override

    w, db, add_track = worker
    path = tmp_path / "song.flac"
    _flac(path)
    add_track(path)
    with db._get_connection() as conn:
        set_field_override(conn, entity_type="track", entity_id=1, field_name="bpm", value=95.0)
        conn.commit()
    out = _apply(w, path)
    assert out["success"], out
    assert _read_bpm(path) == "95"
    with db._get_connection() as conn:
        assert conn.execute("SELECT bpm FROM lib2_tracks WHERE id=1").fetchone()[0] == pytest.approx(127.6)
        assert get_field_overrides(conn, entity_type="track", entity_id=1)["bpm"].value == 95.0


def test_hand_tagged_file_is_unchanged_even_for_an_old_finding(worker, tmp_path):
    w, db, add_track = worker
    path = tmp_path / "song.flac"
    _flac(path)
    add_track(path)
    db.record_manual_metadata_file(str(path))
    before = path.read_bytes()
    out = _apply(w, path)
    assert out["success"], out
    assert "hand-tagged" in out["message"]
    assert path.read_bytes() == before


def test_a_finding_cannot_write_a_different_tracks_file(worker, tmp_path):
    w, _, add_track = worker
    actual = tmp_path / "actual.flac"
    unrelated = tmp_path / "unrelated.flac"
    _flac(actual)
    _flac(unrelated)
    add_track(actual)
    before = unrelated.read_bytes()
    out = _apply(w, unrelated)
    assert not out["success"]
    assert unrelated.read_bytes() == before


def test_mapped_library_file_uses_the_common_path_resolver(worker, tmp_path, monkeypatch):
    w, _, add_track = worker
    actual = tmp_path / "song.flac"
    _flac(actual)
    stored = "/container/music/song.flac"
    add_track(stored)
    monkeypatch.setattr("core.repair_worker._resolve_file_path", lambda path, *a, **kw: str(actual) if path == stored else None)
    out = _apply(w, stored)
    assert out["success"], out
    assert _read_bpm(actual) == "128"


def test_bpm_apply_cannot_write_a_file_from_another_library(worker, tmp_path, monkeypatch):
    from core.library_scope import library_scope

    w, _, add_track = worker
    actual = tmp_path / "song.flac"
    _flac(actual)
    add_track(actual)
    monkeypatch.setattr("core.library_scope.SCOPE_PARKED", False)
    monkeypatch.setattr("core.library_scope.any_own_library_exists", lambda: True)
    before = actual.read_bytes()
    with library_scope(2):
        out = _apply(w, actual)
    assert not out["success"]
    assert actual.read_bytes() == before
