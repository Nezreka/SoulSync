"""#2046: a reviewed album or artist is locked, and automation leaves it alone."""

from __future__ import annotations

import pytest

from core.library2 import locks
from core.library2.schema import ensure_library_v2_schema
from tests.lib2_seed import row_conn


@pytest.fixture(autouse=True)
def _fresh_locks():
    locks.reset_for_tests()
    yield
    locks.reset_for_tests()


@pytest.fixture
def lib(tmp_path):
    conn = row_conn(str(tmp_path / "lib2.db"))
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists(name) VALUES('A')")
    for title in ("One", "Two"):
        conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(1,?)", (title,))
    for album_id in (1, 2):
        for n in (1, 2):
            cur = conn.execute(
                "INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?,?,?)",
                (album_id, f"T{n}", n))
            conn.execute("INSERT INTO lib2_track_files(track_id, path, size) VALUES(?,?,?)",
                         (cur.lastrowid, f"/m/{album_id}/{n}.flac", 1000 + n))
    conn.commit()
    yield conn
    conn.close()


def test_a_lock_holds_every_scope_unless_told_otherwise(lib):
    state = locks.set_lock(lib, "album", 1, profile_id=1)
    assert state["locked"] is True and state["scopes"] == list(locks.SCOPES)
    assert state["changed_since_review"] is False
    assert locks.is_locked("album", 1, conn=lib) and not locks.is_locked("album", 2, conn=lib)

    state = locks.set_lock(lib, "album", 1, ["tags", "files"], review=False)
    assert state["scopes"] == ["tags", "files"]
    assert locks.is_locked("album", 1, "tags", conn=lib)
    assert not locks.is_locked("album", 1, "upgrades", conn=lib)


def test_no_scopes_left_is_unlocked(lib):
    locks.set_lock(lib, "album", 1)
    assert locks.set_lock(lib, "album", 1, [])["locked"] is False
    assert locks.get_lock(lib, "album", 1) is None


def test_unknown_things_are_refused(lib):
    with pytest.raises(locks.LockError):
        locks.set_lock(lib, "album", 999)
    with pytest.raises(locks.LockError):
        locks.set_lock(lib, "album", 1, ["everything"])
    with pytest.raises(locks.LockError):
        locks.set_lock(lib, "playlist", 1)


def test_automation_is_blocked_and_a_person_is_not(lib):
    locks.set_lock(lib, "album", 1, ["tags"])
    assert locks.blocks("album", 1, "tags", "Genre cleanup", conn=lib) is True
    assert locks.blocks("album", 1, "files", "Reorganize", conn=lib) is False
    assert locks.blocks("album", 2, "tags", "Genre cleanup", conn=lib) is False
    with locks.user_action():
        assert locks.blocks("album", 1, "tags", "Tag editor", conn=lib) is False
        with locks.automation():
            assert locks.blocks("album", 1, "tags", "Bulk retag", conn=lib) is True
        assert locks.blocks("album", 1, "tags", "Tag editor", conn=lib) is False


def test_a_web_request_is_a_person(lib):
    from flask import Flask
    locks.set_lock(lib, "album", 1)
    with Flask(__name__).test_request_context("/"):
        assert locks.acting_as_user() is True
        assert locks.blocks("album", 1, "tags", "Tag editor", conn=lib) is False
        with locks.automation():
            assert locks.blocks("album", 1, "tags", "Retag all", conn=lib) is True
    assert locks.acting_as_user() is False


def test_a_track_and_a_file_are_covered_by_their_album(lib):
    locks.set_lock(lib, "album", 1, ["tags"])
    assert locks.blocks_track(1, "tags", "Retag", conn=lib) is True
    assert locks.blocks_track(3, "tags", "Retag", conn=lib) is False
    assert locks.blocks_file("/m/1/2.flac", "tags", "Retag", conn=lib) is True
    assert locks.blocks_file("/m/2/1.flac", "tags", "Retag", conn=lib) is False
    # a download still in staging is nobody's locked album
    assert locks.blocks_file("/staging/x.flac", "tags", "Import", conn=lib) is False


def test_an_artist_lock_does_not_reach_its_albums(lib):
    locks.set_lock(lib, "artist", 1)
    assert locks.blocks("artist", 1, "metadata", "Last.fm enrichment", conn=lib) is True
    assert locks.blocks("album", 1, "metadata", "Last.fm enrichment", conn=lib) is False


def test_what_the_lock_kept_away_is_remembered_once(lib):
    locks.set_lock(lib, "album", 1)
    for _ in range(5):
        locks.blocks("album", 1, "tags", "Genre cleanup", conn=lib)
    locks.blocks("album", 1, "metadata", "Spotify enrichment", conn=lib)
    log = locks.skips(lib, "album", 1)
    assert {(s["scope"], s["what"], s["times"]) for s in log} == {
        ("tags", "Genre cleanup", 1), ("metadata", "Spotify enrichment", 1)}
    # a check never writes on the caller's connection
    assert lib.in_transaction is False
    # unlocking forgets the log
    locks.clear_lock(lib, "album", 1)
    lib.commit()
    assert locks.skips(lib, "album", 1) == []


def test_a_person_is_not_logged(lib):
    locks.set_lock(lib, "album", 1)
    with locks.user_action():
        locks.blocks("album", 1, "tags", "Tag editor", conn=lib)
    assert locks.skips(lib, "album", 1) == []


def test_files_changing_after_the_review_show(lib):
    locks.set_lock(lib, "album", 1)
    assert locks.get_lock(lib, "album", 1)["changed_since_review"] is False
    lib.execute("UPDATE lib2_track_files SET size=5 WHERE path='/m/1/1.flac'")
    assert locks.get_lock(lib, "album", 1)["changed_since_review"] is True
    # changing the switches does not count as a new review
    locks.set_lock(lib, "album", 1, ["tags"], review=False)
    assert locks.get_lock(lib, "album", 1)["changed_since_review"] is True
    # reviewing it again does
    locks.set_lock(lib, "album", 1, ["tags"])
    assert locks.get_lock(lib, "album", 1)["changed_since_review"] is False


def test_unlocked_sql_filters_by_scope(lib):
    locks.set_lock(lib, "album", 1, ["metadata"])
    lib.commit()
    albums = [r[0] for r in lib.execute(
        f"SELECT e.id FROM lib2_albums e WHERE {locks.unlocked_sql('album', 'e', 'metadata')}")]
    assert albums == [2]
    tracks = [r[0] for r in lib.execute(
        f"SELECT e.id FROM lib2_tracks e WHERE {locks.unlocked_sql('track', 'e', 'metadata')}")]
    assert tracks == [3, 4]
    every = [r[0] for r in lib.execute(
        f"SELECT e.id FROM lib2_albums e WHERE {locks.unlocked_sql('album', 'e', 'tags')}")]
    assert every == [1, 2]


def test_review_counts_and_bulk(lib):
    assert locks.artist_review_counts(lib, 1) == {"albums": 2, "reviewed": 0}
    assert locks.set_many(lib, "album", [1, 2, 999]) == 2
    assert locks.artist_review_counts(lib, 1) == {"albums": 2, "reviewed": 2}
    assert locks.set_many(lib, "album", [1], locked=False) == 1
    assert locks.artist_review_counts(lib, 1) == {"albums": 2, "reviewed": 1}


# ── enrichment ───────────────────────────────────────────────────────────────

def test_a_provider_write_leaves_a_locked_row_alone(lib):
    from core.library2.provider_writes import claim_provider_id, write_provider_enrichment

    locks.set_lock(lib, "album", 1, ["metadata"])
    for album_id in (1, 2):
        write_provider_enrichment(lib, entity_type="album", entity_id=album_id, service="deezer",
                                  payload={"fans": 9}, provider_id="dz1",
                                  backfill={"label": "Sub Pop"})
    rows = {r["id"]: (r["label"], r["external_ids"]) for r in lib.execute(
        "SELECT id, label, external_ids FROM lib2_albums")}
    assert rows[1] == (None, "{}")
    assert rows[2][0] == "Sub Pop" and "dz1" in rows[2][1]
    # a track on the locked album too
    write_provider_enrichment(lib, entity_type="track", entity_id=1, service="deezer",
                              provider_id="t1")
    assert lib.execute("SELECT external_ids FROM lib2_tracks WHERE id=1").fetchone()[0] == "{}"
    assert claim_provider_id(lib, entity_type="album", entity_id=1, service="deezer",
                             provider_id="dz9") is False
    # by hand it goes through
    with locks.user_action():
        write_provider_enrichment(lib, entity_type="album", entity_id=1, service="deezer",
                                  backfill={"label": "Matador"})
    assert lib.execute("SELECT label FROM lib2_albums WHERE id=1").fetchone()[0] == "Matador"
    assert [s["what"] for s in locks.skips(lib, "album", 1)] == ["Deezer enrichment"]


def test_the_enrichment_queue_never_offers_a_locked_row(lib):
    from core.library2 import worker_queue

    def offered(entity_type):
        worker_queue.invalidate_pending_cache()
        seen = set()
        while True:
            item = worker_queue.next_pending(lib, "deezer", entity_types=(entity_type,))
            if not item or item["id"] in seen:
                return seen
            seen.add(item["id"])
            from core.library2.provider_attempts import record_attempt
            record_attempt(lib, entity_type=entity_type, entity_id=item["id"],
                           service="deezer", status="not_found")

    locks.set_lock(lib, "album", 1, ["metadata"])
    lib.commit()
    assert offered("album") == {2}
    assert offered("track") == {3, 4}
    assert worker_queue.pending_count(lib, "deezer") >= 0


# ── tags in the files ────────────────────────────────────────────────────────

def test_a_file_is_found_under_a_different_mount(lib):
    locks.set_lock(lib, "album", 1, ["tags"])
    # the server says /m/1/2.flac, this machine sees it somewhere else
    assert locks.blocks_file("D:\\music\\1\\2.FLAC", "tags", "Retag", conn=lib) is True
    assert locks.blocks_file("/mnt/music/9/2.flac", "tags", "Retag", conn=lib) is False


def test_a_named_job_is_what_the_log_shows(lib):
    locks.set_lock(lib, "album", 1)
    with locks.doing("Genre cleanup"):
        assert locks.blocks_file("/m/1/1.flac", "tags", "Tag write", conn=lib) is True
    assert [s["what"] for s in locks.skips(lib, "album", 1)] == ["Genre cleanup"]


def test_the_tag_save_itself_refuses_a_locked_album(library, monkeypatch):
    """the backstop: whichever job reaches the save, a locked album's file is
    not written."""
    from contextlib import closing
    from mutagen.flac import FLAC
    from core.metadata import common

    db, path, _config = library
    with closing(db._get_connection()) as conn, conn:
        locks.set_lock(conn, "album", 1, ["tags"])
    monkeypatch.setattr(locks, "_open", db._get_connection)
    audio = FLAC(str(path))
    audio["genre"] = ["Krautrock"]
    assert common.save_audio_file(audio, common.get_mutagen_symbols()) is False
    assert FLAC(str(path))["genre"] == ["Rock"]
    with locks.user_action():
        assert common.save_audio_file(audio, common.get_mutagen_symbols()) is True
    assert FLAC(str(path))["genre"] == ["Krautrock"]


def test_a_retag_run_passes_over_a_locked_album_and_says_so(library, monkeypatch):
    from contextlib import closing
    from mutagen.flac import FLAC
    from core.library2 import retag

    db, path, _config = library
    monkeypatch.setattr(locks, "_open", db._get_connection)
    audio = FLAC(str(path))
    audio["genre"] = ["Krautrock"]
    audio.save()
    with closing(db._get_connection()) as conn, conn:
        locks.set_lock(conn, "album", 1, ["tags"])

    stats = retag.write_tags(db, [7], embed_cover=False)
    assert (stats["written"], stats["skipped"], stats["locked"]) == (0, 1, 1)
    assert FLAC(str(path))["genre"] == ["Krautrock"]

    # the person's own Write tags on the album page still works
    with locks.user_action():
        stats = retag.write_tags(db, [7], embed_cover=False)
    assert stats["written"] == 1 and "locked" not in stats
    assert FLAC(str(path))["genre"] == ["Rock"]


def test_a_job_never_sees_the_files_of_an_album_locked_against_it(library, monkeypatch):
    from contextlib import closing
    from core.library2.maintenance_subjects import active_album_subjects, active_file_subjects

    db, _path, config = library
    monkeypatch.setattr(locks, "_open", db._get_connection)
    with closing(db._get_connection()) as conn, conn:
        locks.set_lock(conn, "album", 1, ["tags"])

    def seen():
        return (len(active_file_subjects(db, config)), len(active_album_subjects(db, config)))

    assert seen() == (1, 1)                               # nothing running: everything
    with locks.automation(), locks.doing("Library Re-tag", scope="tags"):
        assert seen() == (0, 0)
    with locks.automation(), locks.doing("Library Reorganize", scope="files"):
        assert seen() == (1, 1)                           # a different part of the album
    with locks.automation(), locks.doing("Dead File Cleaner", scope="report"):
        assert seen() == (1, 1)                           # damage is still looked for
    with locks.user_action(), locks.doing("Library Re-tag", scope="tags"):
        assert seen() == (1, 1)
    with closing(db._get_connection()) as conn:
        assert [s["what"] for s in locks.skips(conn, "album", 1)] == ["Library Re-tag"]


def test_no_edition_is_proposed_for_a_match_locked_album(library, monkeypatch):
    from contextlib import closing
    from core.library2 import edition_review

    db, _path, config = library
    monkeypatch.setattr(locks, "_open", db._get_connection)
    with closing(db._get_connection()) as conn, conn:
        locks.set_lock(conn, "album", 1, ["match"])
    asked = []
    monkeypatch.setattr(edition_review, "album_release_ids",
                        lambda album: asked.append(album["id"]) or {}, raising=False)
    assert edition_review.propose_album_edition(db, config, 1) is None
    assert asked == []
    # and with the lock off the same call does go on to read the release ids
    with closing(db._get_connection()) as conn, conn:
        locks.clear_lock(conn, "album", 1)
    try:
        edition_review.propose_album_edition(db, config, 1)
    except Exception:  # noqa: BLE001 - how far it gets is the point
        pass
    assert asked == [1]


from tests.library2.test_metadata_consistency import (  # noqa: E402,F401 - fixtures
    _no_cover_art_artist_name_search, library,
)
