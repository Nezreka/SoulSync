"""SoulID kinded_v2 — the generator, the batch minting, and the cutover.

kinded_v2 (core/soulid_worker.SOULID_VERSION) replaced the pre-v2 scheme:

* the artist id was derived from a provider artist id found by a fuzzy
  Deezer/iTunes search, with library-dependent fallbacks — two installs
  routinely derived different ids for the same artist. v2 mints the
  artist id from the normalized name alone;
* the hash input was the parts concatenated with no separator and no
  entity kind, so an album and a song with the same artist + title
  shared an id. v2 hashes a positional, kinded, versioned input;
* the normalizer deleted every character outside [a-z0-9], hashing
  non-Latin names to '' or fragments. v2 keeps Unicode letters/digits.

The cutover migrations park every pre-v2 id in the *_legacy columns and
NULL the live columns so the worker re-mints. These tests pin all of it.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from core.soulid_worker import SOULID_VERSION, SoulIDWorker, generate_soul_id


class _DB:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        return sqlite3.connect(self.path)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "soulid.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE artists(id TEXT PRIMARY KEY, name TEXT, soul_id TEXT,
                             soul_id_path TEXT, soul_id_legacy TEXT,
                             updated_at TIMESTAMP);
        CREATE TABLE albums(id TEXT PRIMARY KEY, artist_id TEXT, title TEXT,
                            soul_id TEXT, soul_id_legacy TEXT,
                            updated_at TIMESTAMP);
        CREATE TABLE tracks(id TEXT PRIMARY KEY, artist_id TEXT, album_id TEXT,
                            title TEXT, soul_id TEXT, soul_id_legacy TEXT,
                            album_soul_id TEXT, album_soul_id_legacy TEXT,
                            updated_at TIMESTAMP);
        CREATE TABLE track_downloads(id TEXT PRIMARY KEY, soul_id TEXT);
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.commit()
    conn.close()
    return _DB(path)


def _worker(db):
    worker = SoulIDWorker.__new__(SoulIDWorker)
    worker.db = db
    worker.batch_size = 100
    worker.should_stop = False
    worker.current_item = None
    worker.stats = {'artists_processed': 0, 'albums_processed': 0,
                    'tracks_processed': 0, 'errors': 0, 'pending': 0}
    return worker


def _rows(db, sql, params=()):
    conn = db._get_connection()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _exec(db, sql, params=()):
    conn = db._get_connection()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


# ── the generator ─────────────────────────────────────────────────────────

def test_ids_are_deterministic_and_well_formed():
    a = generate_soul_id("album", "Beyoncé", "Renaissance")
    b = generate_soul_id("album", "Beyoncé", "Renaissance")
    assert a == b
    assert re.fullmatch(r"soul_[0-9a-f]{16}", a)


def test_accents_do_not_change_the_id():
    assert generate_soul_id("artist", "Beyoncé") == \
        generate_soul_id("artist", "Beyonce")


def test_the_kind_is_part_of_the_id():
    """Pre-v2, an album and a song with the same artist + title hashed to
    the same id. They must not any more."""
    assert generate_soul_id("album", "Michael Jackson", "Thriller") != \
        generate_soul_id("track", "Michael Jackson", "Thriller")


def test_parts_cannot_bleed_into_each_other():
    """("AB", "C") and ("A", "BC") were the same concatenated input."""
    assert generate_soul_id("album", "AB", "C") != \
        generate_soul_id("album", "A", "BC")


def test_non_latin_names_mint_real_ids():
    assert generate_soul_id("artist", "Кино")
    assert generate_soul_id("artist", "米津玄師")
    # Two different Cyrillic albums by one artist are different albums.
    assert generate_soul_id("album", "Кино", "Звезда по имени Солнце") != \
        generate_soul_id("album", "Кино", "Группа крови")


def test_a_part_that_normalizes_to_nothing_yields_no_id():
    # Whitespace alone has no fallback content either — no id.
    assert generate_soul_id("artist", "   ") == ""
    assert generate_soul_id("album", "Кино", "   ") == ""


def test_symbol_only_names_still_mint_one_deterministic_id():
    """The band "!!!" and Sigur Rós' "( )" have no letters to hash, but
    they must not become placeholders the export hides forever: the
    normalizer falls back to the raw text, so every install agrees."""
    assert generate_soul_id("artist", "!!!")
    assert generate_soul_id("artist", "!!!") == \
        generate_soul_id("artist", "!!!")
    assert generate_soul_id("album", "Sigur Rós", "( )")


def test_marks_that_are_part_of_the_letter_survive():
    """Accents come off Latin/Greek/Cyrillic bases, but a kana dakuten,
    a Devanagari vowel sign and a Thai tone are part of the letter —
    stripping them collides different words onto one id."""
    from core.soulid_worker import normalize_for_soul_id
    assert normalize_for_soul_id("が") == "が"
    assert normalize_for_soul_id("が") != normalize_for_soul_id("か")
    assert normalize_for_soul_id("हिन्दी") == "हिन्दी"
    assert normalize_for_soul_id("ที่") == "ที่"
    assert generate_soul_id("artist", "バンド") != \
        generate_soul_id("artist", "ハント")


def test_optional_vowel_marks_and_invisibles_come_off():
    """The other side of the mark rule: niqqud and harakat are OPTIONAL
    vowel marks (most Hebrew/Arabic text is written without them), and
    invisible format chars are source noise. Keeping any of them would
    split one artist into two ids."""
    from core.soulid_worker import normalize_for_soul_id
    assert normalize_for_soul_id("שָׁלוֹם") == normalize_for_soul_id("שלום")
    assert normalize_for_soul_id("مُحَمَّد") == normalize_for_soul_id("محمد")
    assert normalize_for_soul_id("שלום\u200f") == normalize_for_soul_id("שלום")
    # Hamza folds onto its carrier — a decision, not an accident:
    # sources disagree on hamza spelling constantly.
    assert normalize_for_soul_id("أحمد") == normalize_for_soul_id("احمد")
    # A parenthetical suffix must not leak into the symbol fallback.
    assert normalize_for_soul_id("!!! (Deluxe)") == \
        normalize_for_soul_id("!!!")
    # Nor may an invisible format char, and an invisibles-only name
    # has no content to fall back to.
    assert normalize_for_soul_id("!!!\u200f") == normalize_for_soul_id("!!!")
    assert generate_soul_id("artist", "​") == ""


def test_wrong_arity_and_unknown_kind_are_errors():
    with pytest.raises(ValueError):
        generate_soul_id("album", "only one part")
    with pytest.raises(ValueError):
        generate_soul_id("bogus", "x")


# ── batch minting ─────────────────────────────────────────────────────────

def test_artists_mint_from_the_name_alone_in_a_batch(db):
    _exec(db, "INSERT INTO artists(id, name) VALUES('a1', 'Rone')")
    _exec(db, "INSERT INTO artists(id, name) VALUES('a2', 'Кино')")
    _exec(db, "INSERT INTO artists(id, name) VALUES('a3', '!!!')")
    _exec(db, "INSERT INTO artists(id, name) VALUES('a4', '   ')")

    assert _worker(db)._process_artists() == 4

    rows = dict((r[0], (r[1], r[2])) for r in _rows(
        db, "SELECT id, soul_id, soul_id_path FROM artists"))
    assert rows["a1"] == (generate_soul_id("artist", "Rone"), "name")
    # A Cyrillic name gets a real id under v2, not a placeholder.
    assert rows["a2"] == (generate_soul_id("artist", "Кино"), "name")
    # Even a symbol-only name mints a real id (raw-text fallback).
    assert rows["a3"] == (generate_soul_id("artist", "!!!"), "name")
    # Only a name with no content at all falls back to the placeholder,
    # and a placeholder is not a derivation: its path stays NULL.
    assert rows["a4"] == ("soul_unnamed_a4", None)


def test_albums_and_tracks_mint_kinded_ids(db):
    _exec(db, "INSERT INTO artists(id, name) VALUES('a1', 'Michael Jackson')")
    _exec(db, "INSERT INTO albums(id, artist_id, title) "
              "VALUES('al1', 'a1', 'Thriller')")
    _exec(db, "INSERT INTO tracks(id, artist_id, album_id, title) "
              "VALUES('t1', 'a1', 'al1', 'Thriller')")

    worker = _worker(db)
    assert worker._process_albums() == 1
    assert worker._process_tracks() == 1

    (album_id,), = _rows(db, "SELECT soul_id FROM albums")
    (song_id, album_track_id), = _rows(
        db, "SELECT soul_id, album_soul_id FROM tracks")
    assert album_id == generate_soul_id("album", "Michael Jackson", "Thriller")
    assert song_id == generate_soul_id("track", "Michael Jackson", "Thriller")
    assert album_track_id == generate_soul_id(
        "album_track", "Michael Jackson", "Thriller", "Thriller")
    assert len({album_id, song_id, album_track_id}) == 3


def test_a_track_with_no_album_gets_a_song_id_and_no_album_id(db):
    _exec(db, "INSERT INTO artists(id, name) VALUES('a1', 'Rone')")
    _exec(db, "INSERT INTO tracks(id, artist_id, album_id, title) "
              "VALUES('t1', 'a1', NULL, 'Orinoco')")

    assert _worker(db)._process_tracks() == 1

    (song_id, album_id), = _rows(
        db, "SELECT soul_id, album_soul_id FROM tracks")
    assert song_id == generate_soul_id("track", "Rone", "Orinoco")
    assert album_id is None


# ── the cutover migrations ────────────────────────────────────────────────

def test_artist_cutover_parks_the_old_id_and_clears_for_remint(db):
    _exec(db, "INSERT INTO artists(id, name, soul_id, soul_id_path) "
              "VALUES('a1', 'Rone', 'soul_old', 'canonical')")

    assert _worker(db)._migrate_artist_soul_ids() is True

    (soul_id, path, legacy), = _rows(
        db, "SELECT soul_id, soul_id_path, soul_id_legacy FROM artists")
    assert soul_id is None
    assert path is None
    assert legacy == 'soul_old'
    (flag,), = _rows(
        db, "SELECT value FROM metadata WHERE key='soulid_artist_version'")
    assert flag == SOULID_VERSION


def test_cutover_runs_once_and_never_overwrites_the_parked_id(db):
    _exec(db, "INSERT INTO artists(id, name, soul_id, soul_id_path) "
              "VALUES('a1', 'Rone', 'soul_old', 'album')")
    worker = _worker(db)
    worker._migrate_artist_soul_ids()

    # A row minted after the cutover must survive a second run untouched.
    _exec(db, "UPDATE artists SET soul_id='soul_new' WHERE id='a1'")
    worker._migrate_artist_soul_ids()

    (soul_id, legacy), = _rows(
        db, "SELECT soul_id, soul_id_legacy FROM artists")
    assert soul_id == 'soul_new'
    assert legacy == 'soul_old'


def test_an_unflagged_rerun_still_never_overwrites_the_parked_id(db):
    """The flag gate is the fast path, not the safety: if the flag row is
    lost (restored backup, pruned metadata) the re-run must still treat
    the parked id as sacred."""
    _exec(db, "INSERT INTO artists(id, name, soul_id, soul_id_path) "
              "VALUES('a1', 'Rone', 'soul_old', 'album')")
    worker = _worker(db)
    worker._migrate_artist_soul_ids()

    _exec(db, "DELETE FROM metadata WHERE key='soulid_artist_version'")
    _exec(db, "UPDATE artists SET soul_id='soul_other' WHERE id='a1'")
    assert worker._migrate_artist_soul_ids() is True

    (soul_id, legacy), = _rows(
        db, "SELECT soul_id, soul_id_legacy FROM artists")
    assert legacy == 'soul_old'
    assert soul_id is None  # reset again for re-mint


def test_placeholder_ids_are_reset_but_never_parked(db):
    """soul_unnamed_<pk> embeds the LOCAL row pk — parked, it would hand
    peers a bridge id that names a different row on their install."""
    _exec(db, "INSERT INTO artists(id, name, soul_id, soul_id_path) "
              "VALUES('a1', '!!!', 'soul_unnamed_a1', NULL)")

    assert _worker(db)._migrate_artist_soul_ids() is True

    (soul_id, legacy), = _rows(
        db, "SELECT soul_id, soul_id_legacy FROM artists")
    assert soul_id is None
    assert legacy is None


def test_album_cutover_parks_and_clears(db):
    _exec(db, "INSERT INTO albums(id, artist_id, title, soul_id) "
              "VALUES('al1', 'a1', 'Thriller', 'soul_old')")

    _worker(db)._migrate_album_soul_ids()

    (soul_id, legacy), = _rows(db, "SELECT soul_id, soul_id_legacy FROM albums")
    assert soul_id is None
    assert legacy == 'soul_old'


def test_track_cutover_parks_both_ids_and_leaves_provenance_history(db):
    _exec(db, "INSERT INTO tracks(id, artist_id, title, soul_id, album_soul_id) "
              "VALUES('t1', 'a1', 'Thriller', 'soul_old', 'soul_old_album')")
    _exec(db, "INSERT INTO track_downloads(id, soul_id) VALUES('d1', 'soul_old')")

    assert _worker(db)._migrate_track_soul_ids() is True

    (soul_id, legacy, album_id, album_legacy), = _rows(
        db, "SELECT soul_id, soul_id_legacy, album_soul_id, "
           "album_soul_id_legacy FROM tracks")
    assert soul_id is None and album_id is None
    assert legacy == 'soul_old'
    assert album_legacy == 'soul_old_album'
    # Download provenance is history (already-downloaded lookups read it);
    # nothing copies it back into tracks, so the cutover leaves it alone.
    (prov,), = _rows(db, "SELECT soul_id FROM track_downloads")
    assert prov == 'soul_old'


def test_track_cutover_with_only_the_album_id_set(db):
    _exec(db, "INSERT INTO tracks(id, artist_id, title, album_soul_id) "
              "VALUES('t1', 'a1', 'Thriller', 'soul_old_album')")

    assert _worker(db)._migrate_track_soul_ids() is True

    (soul_id, legacy, album_id, album_legacy), = _rows(
        db, "SELECT soul_id, soul_id_legacy, album_soul_id, "
           "album_soul_id_legacy FROM tracks")
    assert album_id is None
    assert album_legacy == 'soul_old_album'
    assert soul_id is None and legacy is None


@pytest.fixture
def bare_db(tmp_path):
    """A schema WITHOUT the legacy columns — the partial-init state the
    cutover must refuse to run against."""
    path = str(tmp_path / "soulid_bare.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE artists(id TEXT PRIMARY KEY, name TEXT, soul_id TEXT,
                             soul_id_path TEXT, updated_at TIMESTAMP);
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.commit()
    conn.close()
    return _DB(path)


def test_cutover_refuses_to_reset_when_the_legacy_column_is_missing(bare_db):
    """Fail-closed: parking is the only copy of the old ids, so a missing
    legacy column means abort WITHOUT the flag — a later start retries."""
    _exec(bare_db, "INSERT INTO artists(id, name, soul_id, soul_id_path) "
                   "VALUES('a1', 'Rone', 'soul_old', 'canonical')")

    assert _worker(bare_db)._migrate_artist_soul_ids() is False

    (soul_id, path), = _rows(
        bare_db, "SELECT soul_id, soul_id_path FROM artists")
    assert soul_id == 'soul_old'
    assert path == 'canonical'
    assert _rows(bare_db, "SELECT key FROM metadata") == []


def test_db_init_migration_parks_before_it_clears(tmp_path):
    """The ancient 'soulid_v2_migration' block in _add_soul_id_columns
    clears artist soul_ids at DB init, before the worker cutover can
    park. On a DB that predates both the flag and the legacy column it
    must park first — the column snapshot is refreshed after the ALTER,
    or the park guard would see the pre-ALTER schema and skip."""
    from database.music_database import MusicDatabase

    path = str(tmp_path / "ancient.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE artists(id TEXT PRIMARY KEY, soul_id TEXT);
        CREATE TABLE albums(id TEXT PRIMARY KEY, soul_id TEXT);
        CREATE TABLE tracks(id TEXT PRIMARY KEY, soul_id TEXT,
                            album_soul_id TEXT);
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO artists VALUES('a1', 'soul_old');
        INSERT INTO artists VALUES('a2', 'soul_unnamed_a2');
    """)
    conn.commit()

    db = MusicDatabase.__new__(MusicDatabase)
    db._add_soul_id_columns(conn.cursor())
    conn.commit()

    rows = dict((r[0], (r[1], r[2])) for r in conn.execute(
        "SELECT id, soul_id, soul_id_legacy FROM artists"))
    assert rows["a1"] == (None, "soul_old")
    assert rows["a2"] == (None, None)  # placeholders are not parked
    conn.close()


# ── the cutover on a real-sized library (review fix) ──────────────────────
# One UPDATE over the whole tracks table held the write lock for 7 minutes
# on a 311k-track library (measured on a copy, WSL): every other writer in
# the app waited or failed with "database is locked" on the first start
# after the upgrade. The cutover now commits in rowid chunks.

def _tracks(db, n):
    for i in range(1, n + 1):
        _exec(db, "INSERT INTO tracks(id, title, soul_id, album_soul_id) "
                  "VALUES(?, ?, ?, ?)", (f"t{i}", f"T{i}", f"soul_old{i}", f"soul_alb{i}"))


def test_track_cutover_commits_in_chunks_and_lets_writers_in(db, monkeypatch):
    import core.soulid_worker as sw
    _tracks(db, 5)
    worker = _worker(db)
    worker.cutover_chunk = 2
    writes = []

    def other_writer(_s):
        # a second connection with no busy wait: it only gets in if the
        # cutover released the lock between chunks
        c = sqlite3.connect(db.path, timeout=0)
        try:
            c.execute("INSERT INTO metadata(key, value) VALUES(?, 'x')", (f"w{len(writes)}",))
            c.commit()
            writes.append(1)
        finally:
            c.close()

    monkeypatch.setattr(sw.time, "sleep", other_writer)
    assert worker._migrate_track_soul_ids() is True
    assert len(writes) == 3  # 5 rows in chunks of 2
    rows = _rows(db, "SELECT soul_id, soul_id_legacy, album_soul_id, album_soul_id_legacy "
                     "FROM tracks ORDER BY id")
    assert all(r[0] is None and r[2] is None for r in rows)
    assert sorted(r[1] for r in rows) == sorted(f"soul_old{i}" for i in range(1, 6))
    assert sorted(r[3] for r in rows) == sorted(f"soul_alb{i}" for i in range(1, 6))


def test_an_interrupted_cutover_resumes_without_losing_an_id(db, monkeypatch):
    import core.soulid_worker as sw
    _tracks(db, 5)
    worker = _worker(db)
    worker.cutover_chunk = 2

    def stop_after_first_chunk(_s):
        worker.should_stop = True

    monkeypatch.setattr(sw.time, "sleep", stop_after_first_chunk)
    assert worker._migrate_track_soul_ids() is False
    assert _rows(db, "SELECT key FROM metadata WHERE key='soulid_track_version'") == []
    done = _rows(db, "SELECT id, soul_id, soul_id_legacy FROM tracks WHERE soul_id IS NULL")
    assert done and all(legacy for _id, _sid, legacy in done)  # every reset row is parked

    worker.should_stop = False
    monkeypatch.setattr(sw.time, "sleep", lambda _s: None)
    assert worker._migrate_track_soul_ids() is True
    rows = dict((r[0], r[1]) for r in _rows(db, "SELECT id, soul_id_legacy FROM tracks"))
    assert rows == {f"t{i}": f"soul_old{i}" for i in range(1, 6)}


def test_a_table_whose_cutover_failed_mints_nothing(db):
    """a v2 id minted before the cutover lands would be parked as the
    'legacy' id by the retry, so that table waits."""
    _exec(db, "INSERT INTO artists(id, name) VALUES('a1', 'Rone')")
    worker = _worker(db)
    worker._cut_over = {"artists": False, "albums": True, "tracks": True}
    assert worker._mint_round() == 0
    assert _rows(db, "SELECT soul_id FROM artists") == [(None,)]
    worker._cut_over["artists"] = True
    assert worker._mint_round() == 1
