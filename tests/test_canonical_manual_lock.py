"""#758 — a manual album match pins (and LOCKS) the canonical album version, so
re-resolution / the auto canonical job can't drag it back to the deluxe edition.

Two seams:
  - should_pin_manual_canonical (pure): when a manual match should pin canonical.
  - set_album_canonical / get_album_canonical (DB): the lock can't be overwritten
    by an auto write, but a new manual write still wins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.metadata.canonical_version import (
    CANONICAL_ALBUM_SOURCES,
    should_pin_manual_canonical,
)
from database.music_database import MusicDatabase


# ---------------------------------------------------------------------------
# should_pin_manual_canonical — pure
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('source', ['spotify', 'itunes', 'deezer', 'discogs', 'hydrabase'])
def test_pins_album_on_recognised_source(source):
    assert should_pin_manual_canonical('album', source) is True


@pytest.mark.parametrize('entity', ['artist', 'track'])
def test_does_not_pin_non_album(entity):
    assert should_pin_manual_canonical(entity, 'spotify') is False


@pytest.mark.parametrize('source', ['lastfm', 'genius', 'musicbrainz', 'audiodb', 'tidal'])
def test_does_not_pin_source_canonical_cant_read(source):
    # No album-version data the canonical tools read → nothing to pin.
    assert should_pin_manual_canonical('album', source) is False


def test_sources_stay_in_sync_with_album_id_columns():
    # The set must mirror the canonical reader's column map; if a source is
    # added there, this fails until CANONICAL_ALBUM_SOURCES is updated.
    from core.library_reorganize import _ALBUM_ID_COLUMNS
    assert CANONICAL_ALBUM_SOURCES == set(_ALBUM_ID_COLUMNS)


# ---------------------------------------------------------------------------
# set_album_canonical / get_album_canonical — the lock (DB)
# ---------------------------------------------------------------------------

def _insert_album(db, album_id):
    conn = db._get_connection()
    conn.execute("INSERT OR IGNORE INTO artists (id, name) VALUES ('ar1', 'Artist')")
    conn.execute("INSERT INTO albums (id, artist_id, title) VALUES (?, 'ar1', 'Album')", (album_id,))
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path: Path) -> MusicDatabase:
    d = MusicDatabase(database_path=str(tmp_path / "ml.db"))
    _insert_album(d, 'al1')
    return d


def test_manual_lock_set_and_read(db):
    assert db.set_album_canonical('al1', 'spotify', 'REG', 1.0, locked=True) is True
    c = db.get_album_canonical('al1')
    assert c['source'] == 'spotify' and c['album_id'] == 'REG' and c['locked'] is True


def test_auto_cannot_overwrite_manual_lock(db):
    db.set_album_canonical('al1', 'spotify', 'REG', 1.0, locked=True)
    # The auto resolve job tries to re-pin the deluxe — must be refused.
    assert db.set_album_canonical('al1', 'spotify', 'DELUXE', 0.9, locked=False) is False
    c = db.get_album_canonical('al1')
    assert c['album_id'] == 'REG' and c['locked'] is True  # unchanged


def test_new_manual_match_overrides_existing_pin(db):
    db.set_album_canonical('al1', 'spotify', 'OLD', 0.8, locked=False)  # auto pin
    # User manually picks a different edition — manual always wins.
    assert db.set_album_canonical('al1', 'itunes', 'NEW', 1.0, locked=True) is True
    c = db.get_album_canonical('al1')
    assert c['source'] == 'itunes' and c['album_id'] == 'NEW' and c['locked'] is True


def test_auto_overwrites_auto(db):
    db.set_album_canonical('al1', 'spotify', 'A', 0.8, locked=False)
    assert db.set_album_canonical('al1', 'spotify', 'B', 0.9, locked=False) is True
    assert db.get_album_canonical('al1')['album_id'] == 'B'


def test_unresolved_album_returns_none(db):
    _insert_album(db, 'al2')
    assert db.get_album_canonical('al2') is None
