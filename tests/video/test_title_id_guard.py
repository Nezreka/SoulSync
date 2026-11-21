"""a title id the naming template uses but the title has no stored value for
($imdbid on a show enrichment never matched) leaves the file alone: mass
rename and the naming check don't strip the '{imdb-tt...}' its folder
already carries. the naming check says so with one finding per title, and
approving it re-queues the title's TMDB enrichment."""

from __future__ import annotations

import os

import pytest

from core.video import organization
from core.video.file_ops import expected_path
from database.video_database import VideoDatabase

_SHOW_TPL = "$series ($year) {imdb-$imdbid}/Season $season/$series ($year) - S$seasonE$episode"
_MOVIE_TPL = "$title ($year) {imdb-$imdbid}/$title ($year)"


def test_missing_ids_only_for_an_id_the_template_names():
    s = {"episode_template": _SHOW_TPL, "movie_template": _MOVIE_TPL}
    assert organization.missing_ids("episode", {"imdbid": None}, s) == ["imdbid"]
    assert organization.missing_ids("episode", {"imdbid": ""}, s) == ["imdbid"]
    assert organization.missing_ids("episode", {"imdbid": "tt0105559"}, s) == []
    assert organization.missing_ids("movie", {"imdbid": None}, s) == ["imdbid"]
    assert organization.missing_ids("movie", {"imdbid": "tt0113277"}, s) == []
    assert organization.missing_ids("youtube", {}, s) == []
    # a template without ids never waits on one
    plain = {"episode_template": "$series/Season $season/$series - S$seasonE$episode"}
    assert organization.missing_ids("episode", {}, plain) == []


def test_each_id_token_is_checked_on_its_own():
    tmdb = {"episode_template": "$series {tmdb-$tmdbid}/$series - S$seasonE$episode"}
    assert organization.missing_ids("episode", {"tmdbid": None, "imdbid": "tt1"}, tmdb) == ["tmdbid"]
    assert organization.missing_ids("episode", {"tmdbid": 43004}, tmdb) == []
    tvdb = {"episode_template": "$series {tvdb-${tvdbid}}/$series - S$seasonE$episode"}
    assert organization.missing_ids("episode", {"tvdbid": None}, tvdb) == ["tvdbid"]
    assert organization.missing_ids("episode", {"tvdbid": 80009}, tvdb) == []


def test_movies_never_wait_on_a_tvdb_id():
    # movies carry no tvdb id, so a movie template naming one can't hold every movie back
    s = {"movie_template": "$title ($year) {tvdb-$tvdbid}/$title ($year)"}
    assert organization.missing_ids("movie", {"imdbid": None, "tmdbid": None}, s) == []


def test_naming_check_leaves_a_show_without_its_imdb_id_alone(tmp_path):
    root = tmp_path / "anime"
    real = root / "Tenchi Muyo! (1992) {imdb-tt0105559}" / "Season 01" / "Tenchi Muyo! (1992) - S01E01.mkv"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"x")
    row = {"scope": "episode", "title": "Tenchi Muyo!", "year": 1992, "season": 1, "episode": 1,
           "tmdb_id": 43004, "imdb_id": None, "tvdb_id": 80009}
    s = {"episode_template": _SHOW_TPL}
    assert expected_path(row, str(real), [str(root)], s) is None
    # once the id is stored the file checks against the full name again
    row["imdb_id"] = "tt0105559"
    assert expected_path(row, str(real), [str(root)], s) == str(real)


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_mass_rename_keeps_the_imdb_tag_of_an_unmatched_movie(db, tmp_path, monkeypatch):
    from api import video as videoapi
    from core.video import mass_rename
    movies = tmp_path / "Movies"
    folder = movies / "Heat (1995) {imdb-tt0113277}"
    folder.mkdir(parents=True)
    real = folder / "Heat (1995) [x].mkv"
    real.write_bytes(b"x" * 900)
    db.set_setting("movies_path", str(movies))
    organization.save(db, {"movie_template": _MOVIE_TPL})
    db.upsert_movie("plex", {"server_id": "p1", "title": "Heat", "year": 1995,
                             "file": {"relative_path": str(real), "size_bytes": 900}})
    monkeypatch.setattr(videoapi, "_video_db", db)

    first = mass_rename.preview()
    assert first["entries"] == [] and first["held"] == 1   # held back, and counted

    # matched: the stray '[x]' goes and the folder keeps its tag
    conn = db._get_connection()
    conn.execute("UPDATE movies SET imdb_id='tt0113277'")
    conn.commit()
    conn.close()
    proposed = mass_rename.preview()["entries"]
    assert [os.path.relpath(e["proposed"], movies) for e in proposed] == [
        os.path.join("Heat (1995) {imdb-tt0113277}", "Heat (1995).mkv")]


# ── the naming check says so ─────────────────────────────────────────────────

def _seed_show_files(db, tmp_path, n=2, imdb=None):
    """an owned show in the TV folder whose folder carries its imdb tag"""
    tv = tmp_path / "TV"
    db.set_setting("tv_path", str(tv))
    conn = db._get_connection()
    conn.execute("INSERT INTO shows (server_source, server_id, title, year, tmdb_id, imdb_id) "
                 "VALUES ('plex', 'sv-tenchi', 'Tenchi Muyo! Ryo-Ohki', 1992, 43004, ?)", (imdb,))
    show_id = conn.execute("SELECT id FROM shows WHERE server_id='sv-tenchi'").fetchone()[0]
    season_id = conn.execute("INSERT INTO seasons (show_id, season_number) VALUES (?, 1)",
                             (show_id,)).lastrowid
    for ep in range(1, n + 1):
        real = (tv / "Tenchi Muyo! Ryo-Ohki (1992) {imdb-tt0105559}" / "Season 01"
                / ("Tenchi Muyo! Ryo-Ohki (1992) - S01E%02d [x].mkv" % ep))
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_bytes(b"x" * 32)
        eid = conn.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, has_file) "
                           "VALUES (?, ?, 1, ?, 1)", (show_id, season_id, ep)).lastrowid
        conn.execute("INSERT INTO media_files (episode_id, relative_path, size_bytes) VALUES (?,?,?)",
                     (eid, str(real), 32))
    conn.commit()
    conn.close()
    organization.save(db, {"episode_template": _SHOW_TPL})
    return show_id


def _pending(db, kind):
    return [f for f in db.repair_get_findings(status="pending")["items"] if f["finding_type"] == kind]


def test_the_naming_check_raises_one_finding_per_title_waiting_on_an_id(db, tmp_path):
    from core.video.repair.worker import VideoRepairWorker
    show_id = _seed_show_files(db, tmp_path, n=3)
    worker = VideoRepairWorker(db)
    worker._run_job("naming_conformance", forced=True)
    assert _pending(db, "naming_mismatch") == []          # nothing proposes stripping the tag
    held = _pending(db, "naming_id_missing")
    assert len(held) == 1
    d = held[0]["details"]
    assert (d["kind"], d["title_id"], d["missing"], d["files"]) == ("show", show_id, ["imdbid"], 3)
    assert "no IMDb id stored" in held[0]["title"]


def test_approving_it_requeues_tmdb_enrichment_and_the_id_retires_it(db, tmp_path):
    from core.video.repair.worker import VideoRepairWorker
    show_id = _seed_show_files(db, tmp_path, n=1)
    conn = db._get_connection()
    conn.execute("UPDATE shows SET tmdb_match_status='matched', tmdb_last_attempted=CURRENT_TIMESTAMP, "
                 "tvdb_match_status='matched', tvdb_last_attempted=CURRENT_TIMESTAMP WHERE id=?", (show_id,))
    conn.commit()
    conn.close()
    worker = VideoRepairWorker(db)
    worker._run_job("naming_conformance", forced=True)
    finding = _pending(db, "naming_id_missing")[0]
    res = worker.fix_finding(finding["id"])
    assert res["success"] is True and res["action"] == "queued"
    assert "TMDB and TVDB" in res["message"]
    conn = db._get_connection()
    status = conn.execute("SELECT tmdb_match_status, tvdb_match_status FROM shows WHERE id=?",
                          (show_id,)).fetchone()
    assert tuple(status) == (None, None)                   # back in both enrichment queues
    # enrichment fills the id: the hold retires and the files are checked again
    conn.execute("UPDATE shows SET imdb_id='tt0105559' WHERE id=?", (show_id,))
    conn.commit()
    conn.close()
    worker._run_job("naming_conformance", forced=True)
    assert _pending(db, "naming_id_missing") == []
    assert [f["details"]["expected_path"].endswith("S01E01.mkv") for f in _pending(db, "naming_mismatch")] == [True]


def test_a_title_that_carries_its_year_is_not_labelled_with_it_twice(db, tmp_path):
    from core.video.repair.worker import VideoRepairWorker
    show_id = _seed_show_files(db, tmp_path, n=1)
    conn = db._get_connection()
    conn.execute("UPDATE shows SET title='The Body (2026)', year=2026 WHERE id=?", (show_id,))
    conn.commit()
    conn.close()
    organization.save(db, {"episode_template": "$series {imdb-$imdbid}/Season $season/$series - S$seasonE$episode"})
    VideoRepairWorker(db)._run_job("naming_conformance", forced=True)
    held = _pending(db, "naming_id_missing")
    assert [h["title"].split(" — ")[0] for h in held] == ["The Body (2026)"]
    assert "Manage panel, under Matches" in held[0]["description"]
