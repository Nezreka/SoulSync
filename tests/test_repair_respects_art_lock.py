"""The Missing Cover Art repair must not undo a hand-picked image.

Found while closing TheHomeGuy's bug, and it is the same bug wearing a different
hat. The scan flags an album whose art is missing in the DB **or** on disk:

    db_missing = not (str(album_thumb).strip() if album_thumb else '')
    ...                                     (core/repair_jobs/missing_cover_art.py)

so an album with a locked custom cover but no ``cover.jpg`` on disk still gets
flagged — and that is a normal state, because the picker only writes cover.jpg
when it can resolve the album's folder ("no on-disk folder for album %s — DB art
updated only"). Running the repair then overwrote the user's cover with whatever
the job had found.

The apply now keeps the chosen image and pushes THAT to disk instead.
"""

from __future__ import annotations

import pytest

from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase

CUSTOM = "https://example.invalid/the-cover-he-chose.jpg"
FOUND = "https://example.invalid/whatever-the-scan-found.jpg"
ARTIST_ID = 101
ALBUM_ID = 201


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "repair-art.db"))


@pytest.fixture()
def worker(db):
    return RepairWorker(db)


def _seed(db, *, album_thumb=None, artist_thumb=None):
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO lib2_artists (id, name, name_key, image_url) "
            "VALUES (?,?,?,?)", (ARTIST_ID, 'Locked Artist', 'locked artist', artist_thumb))
        conn.execute(
            "INSERT OR REPLACE INTO lib2_albums "
            "(id, primary_artist_id, title, image_url) VALUES (?,?,?,?)",
            (ALBUM_ID, ARTIST_ID, 'Locked Album', album_thumb))
        conn.commit()
    finally:
        conn.close()


def _thumb(db, table, row_id):
    conn = db._get_connection()
    try:
        return conn.execute(f"SELECT image_url FROM {table} WHERE id = ?",
                            (row_id,)).fetchone()['image_url']
    finally:
        conn.close()


# ── album art ────────────────────────────────────────────────────────────────

def test_the_repair_keeps_a_locked_album_cover(db, worker):
    """His cover is in the DB; cover.jpg never made it to disk; the scan flags
    the album. Applying the fix must not replace his choice."""
    _seed(db)
    db.set_album_thumb_url(ALBUM_ID, CUSTOM)

    result = worker._fix_missing_cover_art('album', f'lib2:{ALBUM_ID}', None,
                                           {'found_artwork_url': FOUND})

    assert result['success'] is True
    assert _thumb(db, 'lib2_albums', ALBUM_ID) == CUSTOM, \
        "the repair job overwrote a hand-picked cover"


def test_the_repair_still_fills_art_that_is_genuinely_missing(db, worker):
    """The job's actual purpose must keep working for unlocked albums."""
    _seed(db, album_thumb=None)

    result = worker._fix_missing_cover_art('album', f'lib2:{ALBUM_ID}', None,
                                           {'found_artwork_url': FOUND})

    assert result['success'] is True
    assert _thumb(db, 'lib2_albums', ALBUM_ID) == FOUND


def test_a_sidecar_only_fix_no_longer_blanks_the_albums_art(db, worker):
    """Pre-existing bug, found while adding the lock.

    ``sidecar_from_embedded`` means "the file already has embedded art, it just
    needs a cover.jpg" — there is no artwork URL in that finding. The apply ran
    ``UPDATE albums SET thumb_url = ?`` with None regardless, so fixing a missing
    sidecar wiped the album's database art."""
    _seed(db, album_thumb="http://server/perfectly-good.jpg")

    worker._fix_missing_cover_art(
        'album', f'lib2:{ALBUM_ID}', None, {'sidecar_from_embedded': True})

    assert _thumb(db, 'lib2_albums', ALBUM_ID) == "http://server/perfectly-good.jpg", \
        "a sidecar-only repair blanked the album's art"


def test_an_unknown_album_is_still_reported_as_missing(db, worker):
    _seed(db)
    result = worker._fix_missing_cover_art('album', 'lib2:999999', None,
                                           {'found_artwork_url': FOUND})
    assert result['success'] is False
    assert 'not found' in result['error'].lower()


# ── artist art ───────────────────────────────────────────────────────────────

def test_the_repair_keeps_a_locked_artist_photo(db, worker):
    _seed(db)
    db.set_artist_thumb_url(ARTIST_ID, CUSTOM)

    result = worker._fix_artist_art(f'lib2:{ALBUM_ID}', {'found_artist_url': FOUND})

    assert result['success'] is True, "a locked photo is a no-op, not a failure"
    assert _thumb(db, 'lib2_artists', ARTIST_ID) == CUSTOM


def test_the_repair_still_fills_a_missing_artist_photo(db, worker):
    _seed(db, artist_thumb=None)

    result = worker._fix_artist_art(f'lib2:{ALBUM_ID}', {'found_artist_url': FOUND})

    assert result['success'] is True
    assert _thumb(db, 'lib2_artists', ARTIST_ID) == FOUND


def test_an_artist_that_does_not_exist_is_still_an_error(db, worker):
    """The locked-row branch must not swallow a genuine miss."""
    result = worker._fix_artist_art('lib2:999999', {'found_artist_url': FOUND})
    assert result['success'] is False
