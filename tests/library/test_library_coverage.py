"""library coverage: each music folder the media server reports, whether
SoulSync scans it, why not, and how much of it really reads. Real files, a
real catalogue table and its real index; fake media server clients."""

from __future__ import annotations

import os
import random
import sqlite3

import pytest

from core.library import coverage
from core.library2.media_mappings import ensure_media_mapping_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    for table in ("lib2_artists", "lib2_albums", "lib2_tracks"):
        c.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    ensure_media_mapping_schema(c.cursor())
    c.execute("CREATE TABLE lib2_track_files (id INTEGER PRIMARY KEY, track_id INTEGER,"
              " path TEXT NOT NULL, size INTEGER)")
    yield c
    c.close()


def _add(conn, kind, paths, start=1):
    for i, p in enumerate(paths, start):
        conn.execute(
            "INSERT INTO lib2_media_server_mappings(entity_type, entity_id, server_source,"
            " server_id, server_path) VALUES('track', ?, ?, ?, ?)", (i, kind, str(i), p))


def _album(root, artist, album, n=3, data=b"audio"):
    out = []
    for t in range(1, n + 1):
        f = root / artist / album / f"{t:02d} - Song.flac"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        out.append(f)
    return out


def _server_paths(server_root, local_root, files):
    return [server_root + "/" + str(f.relative_to(local_root)).replace(os.sep, "/") for f in files]


def _env(**kw):
    kw.setdefault("library_dirs", [])
    return coverage.Env(**kw)


# ---------------------------------------------------------------- the index
def test_counting_and_sampling_use_the_index_not_a_scan(conn):
    _add(conn, "plex", [f"/data/music/A{i}/B/{i}.flac" for i in range(50)])
    low, high = coverage.folder_range("/data/music")
    for sql in (
        "SELECT COUNT(*) FROM lib2_media_server_mappings WHERE entity_type = 'track'"
        " AND server_source = ? AND server_path >= ? AND server_path < ?",
        "SELECT id, entity_id, server_path FROM lib2_media_server_mappings WHERE"
        " entity_type = 'track' AND server_source = ? AND server_path >= ? AND server_path < ?"
        " ORDER BY server_path LIMIT 1",
    ):
        plan = " ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql,
                                                         ("plex", low, high)))
        assert "idx_lib2_media_mappings_track_path" in plan, plan
        assert "TEMP B-TREE" not in plan, plan


def test_folder_range_is_on_a_folder_boundary(conn):
    _add(conn, "plex", ["/music/A/B/1.flac", "/musicvideos/A/B/1.mkv", "/music2/x/y/z.flac"])
    assert coverage.count_tracks(conn, "plex", "/music") == 1
    assert coverage.count_tracks(conn, "plex", "/music/") == 1
    assert coverage.count_tracks(conn, "plex") == 3
    assert coverage.count_tracks(conn, "jellyfin") == 0


def test_windows_server_folders_range_on_backslashes(conn):
    _add(conn, "plex", ["D:\\Music\\A\\B\\1.flac", "D:\\Music2\\A\\B\\1.flac"])
    assert coverage.count_tracks(conn, "plex", "D:\\Music") == 1


def test_sampling_spreads_and_caps(conn):
    _add(conn, "plex", [f"/m/{a}{i:03d}/Album/1.flac" for i in range(300) for a in "AMZ"[:1]]
         + [f"/m/Z{i:03d}/Album/1.flac" for i in range(300)], start=1)
    got = coverage.sample_tracks(conn, "plex", "/m", 40, random.Random(1))
    assert len(got) == 40
    assert len({g[0] for g in got}) == 40
    # both halves of the folder are in the sample, not only the first 40
    assert any(g[2].startswith("/m/A") for g in got) and any(g[2].startswith("/m/Z") for g in got)


def test_a_small_folder_is_sampled_whole(conn):
    _add(conn, "plex", ["/m/A/B/1.flac", "/m/A/B/2.flac", "/m/C/D/3.flac", "/other/x/y/z.flac"])
    got = coverage.sample_tracks(conn, "plex", "/m", 40, random.Random(2))
    assert [g[2] for g in got] == ["/m/A/B/1.flac", "/m/A/B/2.flac", "/m/C/D/3.flac"]


def test_sampling_a_big_library_is_quick(conn):
    import time
    rows = [("track", i, "plex", str(i), f"/data/music/Artist{i % 5000:05d}/Album{i % 7}/{i}.flac")
            for i in range(1, 200_001)]
    conn.executemany("INSERT INTO lib2_media_server_mappings(entity_type, entity_id,"
                     " server_source, server_id, server_path) VALUES(?,?,?,?,?)", rows)
    started = time.monotonic()
    got = coverage.sample_tracks(conn, "plex", "/data/music", 40, random.Random(3))
    took = time.monotonic() - started
    assert len(got) == 40
    assert took < 1.0, took


# ---------------------------------------------------------------- one file
def test_probe_file_reasons(tmp_path):
    f = tmp_path / "a.flac"
    f.write_bytes(b"12345")
    assert coverage.probe_file(str(f)) == "ok"
    assert coverage.probe_file(str(f), 5) == "ok"
    assert coverage.probe_file(str(f), 9) == "size"
    assert coverage.probe_file(str(tmp_path / "nope.flac")) == "missing"
    assert coverage.probe_file(str(tmp_path)) == "not_a_file"


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs posix permissions and a non-root user")
def test_probe_file_permission(tmp_path):
    f = tmp_path / "locked.flac"
    f.write_bytes(b"x")
    f.chmod(0)
    try:
        if os.access(str(f), os.R_OK):
            pytest.skip("this filesystem ignores permissions")
        assert coverage.probe_file(str(f)) == "permission"
    finally:
        f.chmod(0o644)


# ---------------------------------------------------------------- one folder
def test_a_library_folder_under_its_own_name_is_covered(conn, tmp_path):
    lib = tmp_path / "music"
    files = _album(lib, "Beck", "Guero")
    _add(conn, "jellyfin", _server_paths(str(lib), lib, files))
    got = coverage.check_folder(conn, "jellyfin", str(lib), _env(library_dirs=[str(lib)]))
    assert got["status"] == "covered" and got["found_by"] == "same"
    assert got["readable_pct"] == 100.0 and got["reachable_tracks"] == 3


def test_a_different_name_is_found_by_suffix_and_covered(conn, tmp_path):
    lib = tmp_path / "srv" / "Music"
    files = _album(lib, "Beck", "Guero")
    _add(conn, "plex", _server_paths("/mnt/user/Music", lib, files))
    got = coverage.check_folder(conn, "plex", "/mnt/user/Music", _env(library_dirs=[str(lib)]))
    assert got["status"] == "covered" and got["found_by"] == "suffix"
    assert got["local_folder"] == str(lib)
    assert got["readable"] == 3


def test_suffix_needs_real_files_not_only_a_name(conn, tmp_path):
    lib = tmp_path / "srv" / "Music"
    lib.mkdir(parents=True)
    _add(conn, "plex", ["/mnt/user/Music/Beck/Guero/01 - Song.flac"])
    got = coverage.check_folder(conn, "plex", "/mnt/user/Music", _env(library_dirs=[str(lib)]))
    assert got["status"] == "not_mounted" and got["local_folder"] is None


def test_mounted_but_not_a_library_folder(conn, tmp_path):
    lib = tmp_path / "Transfer"
    lib.mkdir()
    other = tmp_path / "archive"
    files = _album(other, "Beck", "Guero")
    _add(conn, "plex", _server_paths(str(other), other, files))
    got = coverage.check_folder(conn, "plex", str(other), _env(library_dirs=[str(lib)]))
    assert got["status"] == "not_library" and got["local_folder"] == str(other)
    assert got["readable"] == 0 and got["reasons"] == {"not_library": 3}
    assert got["reachable_tracks"] == 0


def test_partly_covered_when_a_library_folder_sits_inside(conn, tmp_path):
    root = tmp_path / "media"
    flac = _album(root / "lossless", "Beck", "Guero")
    mp3 = _album(root / "lossy", "Beck", "Guero")
    _add(conn, "plex", _server_paths(str(root), root, flac + mp3))
    got = coverage.check_folder(conn, "plex", str(root),
                                _env(library_dirs=[str(root / "lossless")]))
    assert got["status"] == "partly_covered"
    assert got["readable"] == 3 and got["reasons"]["not_library"] == 3


def test_not_mounted_says_so_with_a_volume_line_in_docker(conn):
    _add(conn, "plex", ["/mnt/disk7/Music/Beck/Guero/01.flac"])
    got = coverage.check_folder(conn, "plex", "/mnt/disk7/Music",
                                _env(library_dirs=["/nowhere"], docker=True))
    assert got["status"] == "not_mounted"
    assert got["volume_example"] == "- /where/music/is/on/the/host:/music"
    assert got["examples"][0]["reason"] == "not_mounted"


def test_no_volume_line_outside_docker(conn):
    _add(conn, "plex", ["/mnt/disk7/Music/Beck/Guero/01.flac"])
    got = coverage.check_folder(conn, "plex", "/mnt/disk7/Music", _env())
    assert "volume_example" not in got


def test_missing_files_and_size_mismatch_are_explained(conn, tmp_path):
    lib = tmp_path / "music"
    files = _album(lib, "Beck", "Guero", data=b"12345")
    paths = _server_paths(str(lib), lib, files) + [str(lib) + "/Beck/Guero/99 - Gone.flac"]
    _add(conn, "plex", paths)
    # the catalogue says track 1's file is 9 bytes: a different file now
    conn.execute("INSERT INTO lib2_track_files(track_id, path, size) VALUES(1, ?, 9)",
                 (str(files[0]),))
    got = coverage.check_folder(conn, "plex", str(lib), _env(library_dirs=[str(lib)]))
    assert got["reasons"] == {"ok": 2, "size": 1, "missing": 1}
    assert {e["reason"] for e in got["examples"]} == {"size", "missing"}
    assert all(e["why"] for e in got["examples"])
    assert got["readable_pct"] == 50.0


def test_override_wins_and_a_broken_one_is_reported(conn, tmp_path):
    lib = tmp_path / "x" / "tunes"
    files = _album(lib, "Beck", "Guero")
    _add(conn, "plex", _server_paths("/share/collection", lib, files))
    env = _env(library_dirs=[str(lib)],
               overrides=[{"server_prefix": "/share/collection", "local_prefix": str(lib)}])
    got = coverage.check_folder(conn, "plex", "/share/collection", env)
    assert got["found_by"] == "override" and got["status"] == "covered"
    env = _env(library_dirs=[str(lib)],
               overrides=[{"server_prefix": "/share/collection", "local_prefix": "/gone"}])
    got = coverage.check_folder(conn, "plex", "/share/collection", env)
    assert got["broken_override"] == "/gone" and got["status"] == "not_mounted"


def test_learned_from_the_catalogue_pairs(conn, tmp_path):
    lib = tmp_path / "x" / "tunes"
    files = _album(lib, "Beck", "Guero")
    _add(conn, "plex", _server_paths("/share/collection", lib, files))
    for i, f in enumerate(files, 1):
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?, ?)", (i, str(f)))
    got = coverage.check_folder(conn, "plex", "/share/collection", _env(library_dirs=[str(lib)]))
    assert got["found_by"] == "learned" and got["local_folder"] == str(lib)


def test_learned_folder_needs_agreement():
    pairs = [("/a/x/B/A/1.flac", "/s/B/A/1.flac"), ("/b/y/B/A/2.flac", "/s/B/A/2.flac"),
             ("/c/z/B/A/3.flac", "/s/B/A/3.flac")]
    assert coverage.learned_folder(pairs, "/s", lambda p: True) is None
    agree = [(f"/srv/m/B/A/{i}.flac", f"/s/B/A/{i}.flac") for i in range(3)]
    assert coverage.learned_folder(agree, "/s", lambda p: True) == "/srv/m"
    assert coverage.learned_folder(agree, "/s", lambda p: False) is None


# ---------------------------------------------------------------- the server
class FakePlex:
    def get_music_library_folders(self):
        return [{"library": "Music", "folders": ["/data/music", "/data/archive"]},
                {"library": "Kids", "folders": ["/data/kids"]}]


class FakeJellyfin:
    def get_music_library_folders(self):
        return [{"library": "Music", "folders": ["/media/music"]}]


class FakeNavidrome:
    """navidrome's api names its folders but not their paths"""

    def get_music_folders(self):
        return [{"title": "Music", "key": "1"}]


class Broken:
    def get_music_library_folders(self):
        raise ConnectionError("down")


def test_server_folders_from_fake_clients():
    assert coverage.server_folders(FakePlex()) == [
        {"library": "Music", "folder": "/data/music"},
        {"library": "Music", "folder": "/data/archive"},
        {"library": "Kids", "folder": "/data/kids"}]
    assert coverage.server_folders(FakeJellyfin()) == [{"library": "Music", "folder": "/media/music"}]
    assert coverage.server_folders(FakeNavidrome()) == []
    assert coverage.server_folders(Broken()) == []


def test_navidrome_folders_are_inferred_from_its_paths(conn):
    _add(conn, "navidrome", [f"/music/A{i}/B/{i}.flac" for i in range(60)]
         + [f"/music2/lossless/A{i}/B/{i}.flac" for i in range(40)])
    got = coverage.infer_folders(conn, "navidrome", random.Random(4))
    assert got == ["/music", "/music2/lossless"]


def test_an_inferred_folder_inside_another_is_the_same_library(conn):
    # a deeper layout under /music reads as /music/Various, not a second library
    _add(conn, "navidrome", [f"/music/A{i}/B/{i}.flac" for i in range(60)]
         + [f"/music/Various/A{i}/B/{i}.flac" for i in range(40)])
    assert coverage.infer_folders(conn, "navidrome", random.Random(5)) == ["/music"]


def test_navidrome_relative_paths_are_flagged(conn, tmp_path):
    _add(conn, "navidrome", [f"A{i}/B/{i}.flac" for i in range(10)])
    result = coverage.run_check(conn, "navidrome", FakeNavidrome(), _env(), seed=1)
    assert result["relative_paths"] is True and result["folders"] == []


def test_run_check_overall_share(conn, tmp_path):
    lib = tmp_path / "data" / "music"
    files = _album(lib, "Beck", "Guero", n=6)
    _add(conn, "plex", _server_paths(str(lib), lib, files)
         + [f"/data/archive/A/B/{i}.flac" for i in range(3)]
         + ["/data/kids/A/B/1.flac", "/loose/A/B/1.flac"], start=1)

    class Plex:
        def get_music_library_folders(self):
            return [{"library": "Music", "folders": [str(lib), "/data/archive"]},
                    {"library": "Kids", "folders": ["/data/kids"]}]

    result = coverage.run_check(conn, "plex", Plex(), _env(library_dirs=[str(lib)]), seed=1)
    assert result["total_tracks"] == 11 and result["outside_folders"] == 1
    assert result["reachable_tracks"] == 6
    assert result["reachable_pct"] == round(600 / 11, 1)
    statuses = {f["server_folder"]: f["status"] for f in result["folders"]}
    assert statuses == {str(lib): "covered", "/data/archive": "not_mounted",
                        "/data/kids": "not_mounted"}
    assert result["folders_from"] == "server"


def test_run_check_falls_back_to_inference_when_the_server_is_down(conn, tmp_path):
    _add(conn, "plex", [f"/data/music/A{i}/B/{i}.flac" for i in range(10)])
    result = coverage.run_check(conn, "plex", Broken(), _env(), seed=1)
    assert result["folders_from"] == "inferred"
    assert [f["server_folder"] for f in result["folders"]] == ["/data/music"]


def test_remembered_result_round_trip():
    coverage.forget()
    assert coverage.last_result() is None
    coverage.remember({"server": "plex", "reachable_pct": 10.0})
    assert coverage.last_result()["reachable_pct"] == 10.0
    coverage.forget()
    assert coverage.last_result() is None


# ------------------------------------------------- a linux path on a windows host
def test_a_linux_server_path_is_never_the_same_folder_on_windows(conn, monkeypatch):
    """Plex on linux says /mnt/musicBackup; SoulSync on Windows reads that as
    E:\\mnt\\musicBackup, and a stray folder there was taken for the library
    (0 of 40 files opened). on Windows such a path is never 'same'."""
    from core.library import path_mappings
    monkeypatch.setattr(path_mappings, "foreign_rooted",
                        lambda p, nt=None: path_mappings.__dict__["_real_foreign"](p, nt=True))
    env = _env(isdir=lambda _p: True)
    local, how, _ = coverage.locate_folder(conn, "plex", "/mnt/musicBackup", env, [])
    assert how != "same"


def test_on_linux_the_same_path_still_counts(conn):
    env = _env(isdir=lambda p: p == "/mnt/musicBackup")
    local, how, _ = coverage.locate_folder(conn, "plex", "/mnt/musicBackup", env, [])
    assert (local, how) == ("/mnt/musicBackup", "same")
