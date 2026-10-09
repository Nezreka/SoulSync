"""applying a BPM Backfill finding puts the bpm in the file, not only the db
(discord, Specialmed: "the found BPM is not written into the files").

_fix_metadata_gap wrote tracks.bpm and stopped, so players and media servers
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
            conn.execute("INSERT OR IGNORE INTO artists (id, name) VALUES ('ar', 'A')")
            conn.execute("INSERT OR IGNORE INTO albums (id, artist_id, title) VALUES ('al', 'ar', 'B')")
            conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES ('t1', 'al', 'ar', 'Song', ?)",
                         (str(path),))
            conn.commit()
        finally:
            conn.close()
    return w, db, add_track


def _apply(w, path, bpm=127.6):
    return w._fix_metadata_gap("track", "t1", str(path), {"found_fields": {"bpm": bpm}})


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
        assert conn.execute("SELECT bpm FROM tracks WHERE id = 't1'").fetchone()[0] == pytest.approx(127.6)
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
        assert conn.execute("SELECT bpm FROM tracks WHERE id = 't1'").fetchone()[0] == pytest.approx(127.6)
    finally:
        conn.close()
