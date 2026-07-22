"""#1051 — Disc # is editable like Track #/Title, and the enhanced view no longer
drops tracks that collide on disc:track when a multi-disc album's tags all claim
disc 1.

Two parts:
  * DB: disc_number joins the track editable-fields whitelist (behavioral test).
  * Frontend: the enhanced-view render Map keys owned tracks by unique id (never
    by disc:track slot), and the Disc column is wired for inline edit. Source-guard
    asserts — library.js is vanilla JS with no JS test runner in this repo.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from database.music_database import MusicDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent
_LIBRARY_JS = (_ROOT / "webui" / "static" / "library.js").read_text(encoding="utf-8")


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
    assert 'rowsBySlot.set(`owned:${track.id}`, track)' in _LIBRARY_JS
    assert 'ownedSlots.add(_trackSlotKey(track))' in _LIBRARY_JS
    # Missing-track merge now consults the slot SET, not the id-keyed row Map.
    assert '!ownedSlots.has(key)' in _LIBRARY_JS


# ---------------------------------------------------------------------------
# Disc inline-edit wiring (Part B) — source guards
# ---------------------------------------------------------------------------

def test_disc_column_is_editable_and_wired():
    assert "discTd.className = 'col-disc' + (admin ? ' editable' : '')" in _LIBRARY_JS
    assert "startInlineEdit(cell, 'track', track.id, 'disc_number'" in _LIBRARY_JS
    assert "['track_number', 'disc_number', 'bpm'].includes(field)" in _LIBRARY_JS
    assert "field === 'track_number' || field === 'disc_number'" in _LIBRARY_JS


def test_disc_not_editable_on_missing_rows():
    # Disc # only applies to a real owned file — a phantom "Missing" row must not
    # be disc-editable (mirrors the title cell).
    assert "if (track._missingExpected) discTd.classList.remove('editable')" in _LIBRARY_JS
