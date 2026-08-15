"""Hand-picked artwork must survive a library sync.

TheHomeGuy, on 3.2.0: he sets a custom cover in the art picker, it applies, and
then "if for some reason i need to manually sync the artist to the library, it
seems to remove the custom album art that I added". His screenshots show the
cover reverting to Navidrome's blue-vinyl placeholder.

The art picker looked like it pinned the choice, and its docstring said so — but
the pin was an accident of how OTHER code was written, not a property of the row:

    every enrichment worker fills art only WHERE thumb_url IS NULL OR = ''

so a non-empty value happened to survive *those* writers. A library sync is a
different writer with different rules. The album upsert had

    thumb_url = COALESCE(NULLIF(?, ''), thumb_url)

which only declines to write when the SERVER sends nothing — and Navidrome
always sends a cover URL, its own placeholder included. The artist upsert was
worse: a plain ``SET thumb_url = ?`` with no guard at all.

Nothing in the row said "a human chose this", so nothing could protect it.
``art_locked`` is that flag. These tests drive the REAL upserts against a REAL
database, because the bug was in SQL, and a mocked cursor would have happily
reported success while the row was overwritten.
"""

from __future__ import annotations


import pytest

from database.music_database import MusicDatabase

CUSTOM = "https://imgs.search.brave.com/three-doors-down-six-pack.jpg"
# What Navidrome hands back for an album it has no real art for. The point is
# that it is NOT empty: this is exactly the value that won before.
SERVER_PLACEHOLDER = "http://navidrome.local/rest/getCoverArt?id=al-123"


class _Obj:
    """A media-server object as the upserts consume it (duck-typed, like the
    real Plex/Jellyfin/Navidrome wrappers)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _artist(rating_key="ar-1", name="3 Doors Down", thumb=None):
    return _Obj(ratingKey=rating_key, title=name, thumb=thumb, genres=[], summary="")


def _album(rating_key="al-123", title="A Six Pack of Hits", thumb=None, year=2008):
    return _Obj(ratingKey=rating_key, title=title, year=year, thumb=thumb,
                genres=[], leafCount=6, duration=1424)


@pytest.fixture()
def db(tmp_path):
    """A real database on a throwaway path — never the live one."""
    return MusicDatabase(str(tmp_path / "art.db"))


def _seed(db, *, artist_thumb=SERVER_PLACEHOLDER, album_thumb=SERVER_PLACEHOLDER):
    db.insert_or_update_media_artist(_artist(thumb=artist_thumb), server_source='navidrome')
    db.insert_or_update_media_album(_album(thumb=album_thumb), 'ar-1', server_source='navidrome')


def _album_row(db, album_id="al-123"):
    conn = db._get_connection()
    try:
        return conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
    finally:
        conn.close()


def _artist_row(db, artist_id="ar-1"):
    conn = db._get_connection()
    try:
        return conn.execute("SELECT * FROM artists WHERE id = ?", (artist_id,)).fetchone()
    finally:
        conn.close()


# ── the schema carries the flag ──────────────────────────────────────────────

def test_both_tables_have_an_art_locked_column(db):
    """Additive migration; every existing row defaults to 0 = follow the server."""
    _seed(db)
    assert _album_row(db)['art_locked'] == 0
    assert _artist_row(db)['art_locked'] == 0


def test_the_column_is_added_even_if_the_neighbouring_migration_bails(tmp_path, monkeypatch):
    """The sync upserts REFERENCE art_locked. If the column were missing they
    would raise "no such column" for every album and artist — and the upsert's
    broad `except Exception` would swallow it and return False, silently losing
    the entire scan. That is a worse failure than the bug being fixed.

    `_ensure_core_media_schema_columns` wraps a dozen additive repairs in ONE
    try/except that logs and moves on, so the FIRST one to fail skips every
    column after it. art_locked started life at the bottom of that block — one
    unrelated ALTER failing on someone's database and the sync would break
    wholesale. It now has its own method, and this test holds them apart:
    simulate the neighbour giving up early and art_locked must still land."""
    monkeypatch.setattr(MusicDatabase, '_ensure_core_media_schema_columns',
                        lambda self, cursor: None)   # its internal except: log and move on
    broken = MusicDatabase(str(tmp_path / "broken-neighbour.db"))

    conn = broken._get_connection()
    try:
        for table in ('albums', 'artists'):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            assert 'art_locked' in cols, (
                f"{table}.art_locked was skipped because a DIFFERENT migration failed — "
                "every sync upsert would now raise 'no such column'")
    finally:
        conn.close()


# ── applying a pick locks it ─────────────────────────────────────────────────

def test_choosing_album_art_locks_it(db):
    _seed(db)
    assert db.set_album_thumb_url('al-123', CUSTOM) is True
    row = _album_row(db)
    assert row['thumb_url'] == CUSTOM
    assert row['art_locked'] == 1


def test_choosing_artist_art_locks_it(db):
    _seed(db)
    assert db.set_artist_thumb_url('ar-1', CUSTOM) is True
    row = _artist_row(db)
    assert row['thumb_url'] == CUSTOM
    assert row['art_locked'] == 1


# ── THE bug: the sync must not win ───────────────────────────────────────────

def test_a_sync_does_not_overwrite_a_chosen_album_cover(db):
    """TheHomeGuy's exact sequence: pick a cover, then re-sync the artist."""
    _seed(db)
    db.set_album_thumb_url('al-123', CUSTOM)

    # The same album comes back from Navidrome with its placeholder cover.
    db.insert_or_update_media_album(_album(thumb=SERVER_PLACEHOLDER), 'ar-1',
                              server_source='navidrome')

    assert _album_row(db)['thumb_url'] == CUSTOM, (
        "the library sync overwrote a hand-picked cover — this is the bug")


def test_a_sync_does_not_overwrite_a_chosen_artist_photo(db):
    """The artist path had no guard whatsoever, so it lost even to a NULL."""
    _seed(db)
    db.set_artist_thumb_url('ar-1', CUSTOM)

    db.insert_or_update_media_artist(_artist(thumb=SERVER_PLACEHOLDER), server_source='navidrome')

    assert _artist_row(db)['thumb_url'] == CUSTOM


def test_a_sync_cannot_blank_a_chosen_artist_photo(db):
    """`SET thumb_url = ?` with the server sending nothing used to NULL the row."""
    _seed(db)
    db.set_artist_thumb_url('ar-1', CUSTOM)

    db.insert_or_update_media_artist(_artist(thumb=None), server_source='navidrome')

    assert _artist_row(db)['thumb_url'] == CUSTOM


def test_repeated_syncs_keep_losing(db):
    """He syncs often. The lock must hold every time, not just the first."""
    _seed(db)
    db.set_album_thumb_url('al-123', CUSTOM)
    for _ in range(5):
        db.insert_or_update_media_album(_album(thumb=SERVER_PLACEHOLDER), 'ar-1',
                                  server_source='navidrome')
    assert _album_row(db)['thumb_url'] == CUSTOM


# ── and the sync must still work normally when nothing was chosen ────────────

def test_an_unlocked_album_still_follows_the_server(db):
    """The fix must not freeze art for everyone else — an untouched row still
    tracks whatever the media server reports."""
    _seed(db, album_thumb="http://navidrome.local/old.jpg")

    db.insert_or_update_media_album(_album(thumb="http://navidrome.local/new.jpg"), 'ar-1',
                              server_source='navidrome')

    assert _album_row(db)['thumb_url'] == "http://navidrome.local/new.jpg"


def test_an_unlocked_artist_still_follows_the_server(db):
    _seed(db, artist_thumb="http://navidrome.local/old-artist.jpg")

    db.insert_or_update_media_artist(_artist(thumb="http://navidrome.local/new-artist.jpg"),
                               server_source='navidrome')

    assert _artist_row(db)['thumb_url'] == "http://navidrome.local/new-artist.jpg"


def test_an_unlocked_album_keeps_its_art_when_the_server_sends_nothing(db):
    """The pre-existing COALESCE(NULLIF(...)) behaviour is preserved exactly."""
    _seed(db, album_thumb="http://navidrome.local/have.jpg")

    db.insert_or_update_media_album(_album(thumb=None), 'ar-1', server_source='navidrome')

    assert _album_row(db)['thumb_url'] == "http://navidrome.local/have.jpg"


# ── the rekey path, where the row is rebuilt under a new id ──────────────────

def test_a_ratingkey_change_does_not_unlock_album_art(db):
    """A server rescan can hand the same album a brand-new id. That path REBUILDS
    the row, so art_locked would default back to 0 and the following sync would
    overwrite the pick — the bug returning by a side door."""
    _seed(db)
    db.set_album_thumb_url('al-123', CUSTOM)

    # Same title/artist, new ratingKey, server art attached.
    db.insert_or_update_media_album(_album(rating_key='al-999', thumb=SERVER_PLACEHOLDER),
                              'ar-1', server_source='navidrome')

    row = _album_row(db, 'al-999')
    assert row is not None, "the rekey did not migrate the album"
    assert row['thumb_url'] == CUSTOM, "the rekey dropped the hand-picked cover"
    assert row['art_locked'] == 1, "the rekey unlocked the art — the next sync would wipe it"


def test_a_ratingkey_change_does_not_unlock_artist_art(db):
    _seed(db)
    db.set_artist_thumb_url('ar-1', CUSTOM)

    db.insert_or_update_media_artist(_artist(rating_key='ar-999', thumb=SERVER_PLACEHOLDER),
                               server_source='navidrome')

    row = _artist_row(db, 'ar-999')
    assert row is not None, "the rekey did not migrate the artist"
    assert row['thumb_url'] == CUSTOM
    assert row['art_locked'] == 1


def test_a_ratingkey_change_on_UNLOCKED_art_still_takes_the_server_value(db):
    """The rekey path preferred the fresh server thumb before this change, and
    must keep doing so when the user never picked anything."""
    _seed(db, album_thumb="http://navidrome.local/old.jpg")

    db.insert_or_update_media_album(_album(rating_key='al-999', thumb="http://navidrome.local/new.jpg"),
                              'ar-1', server_source='navidrome')

    assert _album_row(db, 'al-999')['thumb_url'] == "http://navidrome.local/new.jpg"


# ── the way back out ─────────────────────────────────────────────────────────

def test_releasing_the_lock_lets_the_server_win_again(db):
    """The picker can offer zero alternatives ("No alternative covers found for
    this album" — his screenshot), so without a release the lock is a one-way
    door."""
    _seed(db)
    db.set_album_thumb_url('al-123', CUSTOM)

    assert db.clear_art_lock('album', 'al-123') is True
    # The image is deliberately still there — unlocking must not blank the page.
    assert _album_row(db)['thumb_url'] == CUSTOM
    assert _album_row(db)['art_locked'] == 0

    # …and the next sync now refreshes it, which is the point.
    db.insert_or_update_media_album(_album(thumb=SERVER_PLACEHOLDER), 'ar-1',
                              server_source='navidrome')
    assert _album_row(db)['thumb_url'] == SERVER_PLACEHOLDER


def test_releasing_an_artist_lock_works_the_same(db):
    _seed(db)
    db.set_artist_thumb_url('ar-1', CUSTOM)

    assert db.clear_art_lock('artist', 'ar-1') is True
    db.insert_or_update_media_artist(_artist(thumb=SERVER_PLACEHOLDER), server_source='navidrome')
    assert _artist_row(db)['thumb_url'] == SERVER_PLACEHOLDER


def test_releasing_an_unknown_row_reports_failure(db):
    """So the endpoint can answer 404 instead of a cheerful lie."""
    _seed(db)
    assert db.clear_art_lock('album', 'nope') is False


def test_clear_art_lock_rejects_an_unknown_kind(db):
    """The table name is interpolated into the SQL, so the kind is a whitelist,
    never a caller-supplied string."""
    with pytest.raises(ValueError):
        db.clear_art_lock('tracks; DROP TABLE albums--', 'al-123')


# ── enrichment workers were already safe; prove it stays that way ────────────

def test_enrichment_still_cannot_touch_art_that_is_already_set(db):
    """Every worker fills art only WHERE thumb_url IS NULL OR = ''. That guard
    predates this fix and is what made the pin look real; it must keep holding
    so the lock is a second line of defence, not the only one."""
    _seed(db)
    db.set_album_thumb_url('al-123', CUSTOM)

    conn = db._get_connection()
    try:
        # verbatim shape of the enrichment writes (e.g. deezer_worker.py:691)
        conn.execute("UPDATE albums SET thumb_url = ? "
                     "WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')",
                     ("http://deezer/cover.jpg", 'al-123'))
        conn.commit()
    finally:
        conn.close()

    assert _album_row(db)['thumb_url'] == CUSTOM

def test_a_schema_without_the_column_still_syncs(tmp_path):
    """The lock must DEGRADE, never explode.

    The upserts reference art_locked, and SQLite fails the whole statement with
    "no such column" if it is absent — which the upsert's broad `except` turns
    into a silent `return False`, i.e. a scan that stops saving albums. That is
    strictly worse than art forgetting it was pinned.

    The full suite caught this for real: tests/database/test_album_thumb_preservation.py
    and tests/test_cover_art_targets.py build a bare albums table and drive the
    real upsert, and all 10 of them failed with exactly this error. Any database
    whose migration failed would have behaved the same way.
    """
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "bare.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE albums (id TEXT PRIMARY KEY, artist_id TEXT, title TEXT,
                    year INTEGER, thumb_url TEXT, genres TEXT, track_count INTEGER,
                    duration INTEGER, server_source TEXT, created_at TEXT, updated_at TEXT)""")
    conn.execute("INSERT INTO albums (id, artist_id, title, thumb_url, server_source) "
                 "VALUES ('al-123','ar-1','A Six Pack of Hits','http://old.jpg','navidrome')")
    conn.commit()

    bare = MusicDatabase.__new__(MusicDatabase)       # no __init__ ⇒ no migrations
    bare._get_connection = lambda: conn

    ok = bare.insert_or_update_media_album(_album(thumb=SERVER_PLACEHOLDER), 'ar-1',
                                           server_source='navidrome')

    assert ok is True, "the upsert failed outright on a schema without art_locked"
    row = conn.execute("SELECT thumb_url FROM albums WHERE id='al-123'").fetchone()
    assert row['thumb_url'] == SERVER_PLACEHOLDER, "pre-lock behaviour must be preserved"

