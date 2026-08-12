"""#1051 — Disc # is editable like Track #/Title, and the enhanced view no longer
drops tracks that collide on disc:track when a multi-disc album's tags all claim
disc 1.

Two parts:
  * DB: disc_number joins the track editable-fields whitelist (behavioral test).
  * Frontend: the source-guard half retired with the artist-detail page it
    guarded (§50.4.4.24) — the page is deleted, and Library V2 has its own
    coverage for the same behaviour.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from database.music_database import MusicDatabase



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
