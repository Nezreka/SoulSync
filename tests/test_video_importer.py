"""Video post-process / import — the Radarr/Sonarr step that organises a finished
download into the library.

Covers the decision matrix (import / upgrade-replace / reject) and the orchestration
(copy in, carry subtitles, delete a worse existing file, reclaim the source unless
it's a torrent). The filesystem is injected so it's all unit-tested without disk.
"""

from __future__ import annotations

import json
import os

from core.video import importer


# ── a recording fake filesystem ──────────────────────────────────────────────
class FakeFS:
    def __init__(self, dirs=None):
        # dirs: {dirpath: [basename, ...]} — what list_dir returns
        self.dirs = {k: list(v) for k, v in (dirs or {}).items()}
        self.made = []
        self.copied = []     # (src, dst)
        self.moved = []      # (src, dst)
        self.saved = []      # (url, dst)
        self.removed = []

    def list_dir(self, path):
        return self.dirs.get(str(path), [])

    def makedirs(self, path):
        self.made.append(str(path))

    def copy(self, src, dst):
        self.copied.append((src, dst))

    def move(self, src, dst):
        self.moved.append((src, dst))

    def save_url(self, url, dst):
        self.saved.append((url, dst))

    def remove(self, path):
        self.removed.append(path)


def _movie_dl(release, source="soulseek", size=2_000_000_000, root="/lib/movies"):
    return {
        "kind": "movie", "title": "The Matrix", "year": 1999, "source": source,
        "release_title": release, "size_bytes": size, "target_dir": root,
        "search_ctx": json.dumps({"scope": "movie", "title": "The Matrix", "year": 1999}),
    }


def _episode_dl(release, root="/lib/tv", season=1, episode=1):
    return {
        "kind": "show", "title": "Breaking Bad", "source": "soulseek",
        "release_title": release, "size_bytes": 1_000_000_000, "target_dir": root,
        "search_ctx": json.dumps({"scope": "episode", "title": "Breaking Bad",
                                  "season": season, "episode": episode, "episode_title": "Pilot"}),
    }


# ── sanity gate ───────────────────────────────────────────────────────────────
def test_rejects_non_video_and_samples():
    dl = _movie_dl("The Matrix 1999 1080p BluRay")
    assert importer.plan_import(dl, "/dl/x/matrix.nfo", list_dir=lambda d: [])["action"] == "reject"
    small = _movie_dl("The Matrix 1999 1080p BluRay", size=20_000_000)
    p = importer.plan_import(small, "/dl/x/sample-matrix.mkv", list_dir=lambda d: [])
    assert p["action"] == "reject" and "sample" in p["reason"].lower()


def test_rejects_wrong_episode():
    dl = _episode_dl("Breaking Bad S01E02 1080p WEB-DL", season=1, episode=1)
    p = importer.plan_import(dl, "/dl/x/bb.s01e02.mkv", list_dir=lambda d: [])
    assert p["action"] == "reject" and "S01E02" in p["reason"]


def test_rejects_season_pack_for_manual():
    dl = _episode_dl("Breaking Bad S01 1080p WEB-DL", season=1, episode=1)
    dl["search_ctx"] = json.dumps({"scope": "season", "title": "Breaking Bad", "season": 1})
    p = importer.plan_import(dl, "/dl/x/bb.s01.mkv", list_dir=lambda d: [])
    assert p["action"] == "reject" and "manual" in p["reason"].lower()


# ── import / upgrade / not-an-upgrade ─────────────────────────────────────────
def test_fresh_movie_import_path():
    dl = _movie_dl("The Matrix 1999 1080p BluRay x265")
    p = importer.plan_import(dl, "/dl/x/the.matrix.1999.1080p.bluray.x265.mkv", list_dir=lambda d: [])
    assert p["action"] == "import"
    assert p["dest"]["path"] == os.path.join("/lib/movies", "The Matrix (1999)",
                                             "The Matrix (1999) Bluray-1080p.mkv")


def test_upgrade_replaces_worse_existing():
    dl = _movie_dl("The Matrix 1999 2160p BluRay")           # 4K incoming
    folder = os.path.join("/lib/movies", "The Matrix (1999)")
    fs_dirs = {folder: ["The Matrix (1999) Bluray-720p.mkv"]}   # owns 720p
    p = importer.plan_import(dl, "/dl/x/matrix.2160p.mkv", list_dir=lambda d: fs_dirs.get(d, []))
    assert p["action"] == "upgrade"
    assert p["replace_path"] == os.path.join(folder, "The Matrix (1999) Bluray-720p.mkv")


def test_not_an_upgrade_is_rejected():
    dl = _movie_dl("The Matrix 1999 720p HDTV")              # worse than owned
    folder = os.path.join("/lib/movies", "The Matrix (1999)")
    fs_dirs = {folder: ["The Matrix (1999) Bluray-1080p.mkv"]}
    p = importer.plan_import(dl, "/dl/x/matrix.720p.mkv", list_dir=lambda d: fs_dirs.get(d, []))
    assert p["action"] == "reject" and "upgrade" in p["reason"].lower()


# ── ffprobe verification ──────────────────────────────────────────────────────
def test_probe_true_resolution_overrides_lying_name():
    # name claims 1080p; the file is really 720p → tag + folder use the truth
    dl = _movie_dl("The Matrix 1999 1080p BluRay")
    probe = {"ok": True, "resolution": "720p", "duration_sec": 8000, "video_codec": "x264"}
    p = importer.plan_import(dl, "/dl/x/matrix.mkv", list_dir=lambda d: [], probe=probe)
    assert p["action"] == "import"
    assert p["dest"]["path"].endswith("The Matrix (1999) Bluray-720p.mkv")


def test_probe_rejects_corrupt_file():
    dl = _movie_dl("The Matrix 1999 1080p BluRay")
    probe = {"ok": False}
    p = importer.plan_import(dl, "/dl/x/matrix.mkv", list_dir=lambda d: [], probe=probe)
    assert p["action"] == "reject" and "corrupt" in p["reason"].lower()


def test_probe_rejects_short_runtime_movie_as_sample():
    dl = _movie_dl("The Matrix 1999 1080p BluRay")
    probe = {"ok": True, "resolution": "1080p", "duration_sec": 120}   # 2 minutes
    p = importer.plan_import(dl, "/dl/x/matrix.mkv", list_dir=lambda d: [], probe=probe)
    assert p["action"] == "reject" and "sample" in p["reason"].lower()


def test_probe_resolution_drives_upgrade_decision():
    # name says 1080p but the file is truly 2160p → beats an owned 1080p copy
    dl = _movie_dl("The Matrix 1999 1080p BluRay")
    folder = os.path.join("/lib/movies", "The Matrix (1999)")
    fs_dirs = {folder: ["The Matrix (1999) Bluray-1080p.mkv"]}
    probe = {"ok": True, "resolution": "2160p", "duration_sec": 8000}
    p = importer.plan_import(dl, "/dl/x/matrix.mkv", list_dir=lambda d: fs_dirs.get(d, []), probe=probe)
    assert p["action"] == "upgrade"


def test_run_import_uses_prober_and_can_reject():
    dl = _movie_dl("The Matrix 1999 1080p BluRay")
    fs = FakeFS()
    patch = importer.run_import(dl, "/dl/x/matrix.mkv", fs=fs,
                                prober=lambda p: {"ok": False})
    assert patch["status"] == "import_failed" and not fs.copied


# ── orchestration via the fake fs ─────────────────────────────────────────────
def test_run_import_copies_and_reclaims_source():
    dl = _movie_dl("The Matrix 1999 1080p BluRay")
    fs = FakeFS()
    patch = importer.run_import(dl, "/dl/x/matrix.mkv", fs=fs)
    assert patch["status"] == "completed"
    assert patch["dest_path"].endswith(os.path.join("The Matrix (1999)", "The Matrix (1999) Bluray-1080p.mkv"))
    assert fs.copied and fs.copied[0][0] == "/dl/x/matrix.mkv"
    assert "/dl/x/matrix.mkv" in fs.removed          # soulseek source reclaimed


def test_run_import_keeps_torrent_source():
    dl = _movie_dl("The Matrix 1999 1080p BluRay", source="torrent")
    fs = FakeFS()
    importer.run_import(dl, "/dl/x/matrix.mkv", fs=fs)
    assert "/dl/x/matrix.mkv" not in fs.removed      # torrent left seeding


def test_run_import_carries_subtitles_and_deletes_old_on_upgrade():
    dl = _movie_dl("The Matrix 1999 2160p BluRay")
    folder = os.path.join("/lib/movies", "The Matrix (1999)")
    fs = FakeFS(dirs={
        "/dl/x": ["matrix.mkv", "matrix.en.srt", "unrelated.srt"],
        folder: ["The Matrix (1999) Bluray-720p.mkv"],
    })
    patch = importer.run_import(dl, "/dl/x/matrix.mkv", fs=fs)
    assert patch["status"] == "completed"
    # the matching sibling sub is carried + renamed to the dest stem, .en preserved
    subs = [d for _s, d in fs.copied if d.endswith(".en.srt")]
    assert subs and subs[0].endswith("The Matrix (1999) Bluray-2160p.en.srt")
    # the worse 720p copy is deleted
    assert os.path.join(folder, "The Matrix (1999) Bluray-720p.mkv") in fs.removed


# ── manual placement (force + override) ───────────────────────────────────────
def test_force_bypasses_sample_and_files_to_override_identity():
    # a small "sample" file the auto path would reject — forced to a chosen movie
    dl = _movie_dl("sample.mkv", size=20_000_000)
    override = {"scope": "movie", "title": "Blade Runner", "year": 1982, "target_dir": "/lib/movies"}
    p = importer.plan_import(dl, "/dl/x/sample.mkv", list_dir=lambda d: [],
                             force=True, override=override)
    assert p["action"] == "import"
    assert p["dest"]["path"] == os.path.join("/lib/movies", "Blade Runner (1982)",
                                             "Blade Runner (1982).mkv")


def test_force_reidentifies_wrong_episode_to_the_right_one():
    dl = _episode_dl("Show S01E02 1080p WEB-DL", season=1, episode=2)
    override = {"scope": "episode", "title": "Show", "season": 3, "episode": 7,
                "episode_title": "Reckoning", "target_dir": "/lib/tv"}
    p = importer.plan_import(dl, "/dl/x/show.mkv", list_dir=lambda d: [],
                             force=True, override=override)
    assert p["action"] == "import"
    # re-identified to S03E07, but the file keeps its real quality tag from the release
    assert p["dest"]["path"] == os.path.join("/lib/tv", "Show", "Season 03",
                                             "Show - S03E07 - Reckoning WEBDL-1080p.mkv")


def test_force_replaces_existing_regardless_of_quality():
    # forcing a 720p over an owned 1080p still places it (the user decided)
    dl = _movie_dl("The Matrix 1999 720p HDTV")
    folder = os.path.join("/lib/movies", "The Matrix (1999)")
    fs_dirs = {folder: ["The Matrix (1999) Bluray-1080p.mkv"]}
    override = {"scope": "movie", "title": "The Matrix", "year": 1999, "target_dir": "/lib/movies"}
    p = importer.plan_import(dl, "/dl/x/m.mkv", list_dir=lambda d: fs_dirs.get(d, []),
                             force=True, override=override)
    assert p["action"] == "upgrade"


def test_run_import_reject_remembers_the_file_location():
    dl = _movie_dl("The Matrix 1999 1080p BluRay")
    fs = FakeFS()
    patch = importer.run_import(dl, "/dl/x/readme.nfo", fs=fs)   # non-video → reject
    assert patch["status"] == "import_failed"
    assert patch["dest_path"] == "/dl/x/readme.nfo"             # so manual import can find it


# ── organisation settings drive behaviour ─────────────────────────────────────
def test_custom_template_changes_the_path():
    dl = _movie_dl("The Matrix 1999 1080p BluRay")
    settings = {"movie_template": "$year/$title/$title $resolution"}
    p = importer.plan_import(dl, "/dl/x/matrix.mkv", list_dir=lambda d: [], settings=settings)
    assert p["dest"]["path"] == os.path.join("/lib/movies", "1999", "The Matrix", "The Matrix 1080p.mkv")


def test_replace_disabled_rejects_instead_of_upgrading():
    dl = _movie_dl("The Matrix 1999 2160p BluRay")
    folder = os.path.join("/lib/movies", "The Matrix (1999)")
    fs_dirs = {folder: ["The Matrix (1999) Bluray-720p.mkv"]}
    p = importer.plan_import(dl, "/dl/x/m.mkv", list_dir=lambda d: fs_dirs.get(d, []),
                             settings={"replace_existing": False})
    assert p["action"] == "reject" and "replace is turned off" in p["reason"].lower()


def test_move_mode_moves_and_does_not_reclaim():
    dl = _movie_dl("The Matrix 1999 1080p BluRay")
    fs = FakeFS()
    importer.run_import(dl, "/dl/x/matrix.mkv", fs=fs, settings={"transfer_mode": "move"})
    assert fs.moved and fs.moved[0][0] == "/dl/x/matrix.mkv"
    assert not fs.copied and not fs.removed     # moved, so no copy + no source reclaim


def test_carry_subtitles_toggle_off():
    dl = _movie_dl("The Matrix 1999 1080p BluRay")
    fs = FakeFS(dirs={"/dl/x": ["matrix.mkv", "matrix.en.srt"]})
    importer.run_import(dl, "/dl/x/matrix.mkv", fs=fs, settings={"carry_subtitles": False})
    assert not any(d.endswith(".srt") for _s, d in fs.copied)


# (artwork/NFO sidecars moved out of run_import into core/video/sidecars.py — see
#  tests/test_video_sidecars.py. run_import is now purely the file mover.)


def test_run_import_reject_leaves_file_and_flags_manual():
    dl = _movie_dl("The Matrix 1999 1080p BluRay")
    fs = FakeFS()
    patch = importer.run_import(dl, "/dl/x/readme.nfo", fs=fs)   # not a video file
    assert patch["status"] == "import_failed" and patch["error"]
    assert not fs.copied and not fs.removed          # nothing touched on disk
