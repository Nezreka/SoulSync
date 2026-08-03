"""#1051 — Disc # is editable like Track #/Title, and the enhanced view no longer
drops tracks that collide on disc:track when a multi-disc album's tags all claim
disc 1.

Two parts:
  * DB: disc_number joins the track editable-fields whitelist (behavioral test).
  * Frontend: the enhanced-view render Map keys owned tracks by unique id (never
    by disc:track slot), and the Disc column is wired for inline edit. These
    source-guard asserts followed the code from library.js into the React
    artist-detail modules when the page was ported; the TS side has its own
    vitest coverage, and these stay as the cross-language pin on #1051.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from database.music_database import MusicDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent
_ARTIST_DETAIL = _ROOT / "webui" / "src" / "routes" / "artist-detail"
_ALBUM_TS = (_ARTIST_DETAIL / "-artist-detail.enhanced-album.ts").read_text(encoding="utf-8")
_TABLE_TSX = (_ARTIST_DETAIL / "-ui" / "enhanced-track-table.tsx").read_text(encoding="utf-8")


@pytest.fixture()
def db():
    d = MusicDatabase(os.path.join(tempfile.mkdtemp(), "t.db"))
    conn = d._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name) VALUES ('AR1','Art')")
    cur.execute("INSERT INTO albums (id, artist_id, title) VALUES ('A1','AR1','Alb')")
    cur.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, disc_number) "
                "VALUES ('T1','A1','AR1','Song',3,1)")
    conn.commit()
    conn.close()
    return d


# ---------------------------------------------------------------------------
# DB whitelist (Part B)
# ---------------------------------------------------------------------------

def test_disc_number_is_editable(db):
    res = db.update_track_fields('T1', {'disc_number': 2})
    assert res['success'] and 'disc_number' in res['updated_fields']
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT disc_number FROM tracks WHERE id='T1'")
    assert cur.fetchone()['disc_number'] == 2
    conn.close()


def test_non_whitelisted_field_still_ignored(db):
    res = db.update_track_fields('T1', {'disc_number': 4, 'bogus_field': 'x'})
    assert 'disc_number' in res['updated_fields']
    assert 'bogus_field' not in res['updated_fields']


def test_disc_number_in_whitelist_constant():
    assert 'disc_number' in MusicDatabase.TRACK_EDITABLE_FIELDS


# ---------------------------------------------------------------------------
# Enhanced-view collision fix (Part A) — source guards
# ---------------------------------------------------------------------------

def test_owned_tracks_keyed_by_id_not_slot():
    # The render Map must key owned tracks by their unique id so two tracks that
    # collapse to the same disc:track slot never overwrite each other.
    assert 'rows.set(`owned:${track.id}`, track)' in _ALBUM_TS
    assert 'ownedSlots.add(trackSlotKey(track))' in _ALBUM_TS
    # Missing-track merge consults the slot SET, not the id-keyed row Map.
    assert '!ownedSlots.has(key)' in _ALBUM_TS


# ---------------------------------------------------------------------------
# Disc inline-edit wiring (Part B) — source guards
# ---------------------------------------------------------------------------

def test_disc_column_is_editable_and_wired():
    assert 'className={`col-disc${editable}`}' in _TABLE_TSX
    assert 'field="disc_number"' in _TABLE_TSX
    assert "NUMERIC_EDIT_FIELDS.includes(field)" in _ALBUM_TS
    assert "field === 'track_number' || field === 'disc_number'" in _ALBUM_TS


def test_disc_not_editable_on_missing_rows():
    # Disc # only applies to a real owned file — a phantom "Missing" row must not
    # be disc-editable (mirrors the title cell).
    # React derives it once: a missing row is never editable, disc included.
    assert "const editable = isAdmin && !missing ? ' editable' : '';" in _TABLE_TSX
    assert "_missingExpected" in _TABLE_TSX
