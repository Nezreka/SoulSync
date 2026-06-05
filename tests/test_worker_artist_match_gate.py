"""Tests for the shared artist-match gate in core/worker_utils.py.

Two jobs:
  * artist_name_matches — a stricter (0.85) gate than the 0.80 used for
    album/track titles, so short-name false positives ('ODESZA'/'odessa',
    'Blance'/'Blanke', 'Lady A'/'Lady Gaga') are rejected.
  * source_id_conflict / accept_artist_match — refuse to store a source id that
    a DIFFERENTLY-named artist already holds, while still allowing a same-named
    artist (the same act indexed on two media servers) to share it.
"""

from __future__ import annotations

import pytest

from core import worker_utils as wu
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _insert(db, *, artist_id, name, **extra):
    cols = ["id", "name", "server_source"] + list(extra.keys())
    vals = [artist_id, name, "plex"] + list(extra.values())
    ph = ",".join("?" for _ in cols)
    with db._get_connection() as conn:
        conn.execute(f"INSERT INTO artists ({','.join(cols)}) VALUES ({ph})", vals)
        conn.commit()


# ---------------------------------------------------------------------------
# artist_name_matches — the 0.85 gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("ODESZA", "odessa"),
    ("Blance", "Blanke"),
    ("COLLEGE", "Colle"),
    ("Lady A", "Lady Gaga"),
    ("M&O", "M.O.P."),
])
def test_near_name_pairs_rejected_at_085(a, b):
    assert wu.artist_name_matches(a, b) is False


@pytest.mark.parametrize("a,b", [
    ("Saib", "saib."),            # punctuation only → identical
    ("-Us.", "Us"),
    ("Kendrick Lamar", "KENDRICK LAMAR"),
    ("Beyoncé", "Beyonce"),
])
def test_true_variants_still_match(a, b):
    assert wu.artist_name_matches(a, b) is True


# ---------------------------------------------------------------------------
# source_id_conflict — different name blocks, same name allowed
# ---------------------------------------------------------------------------

def test_conflict_when_different_named_artist_holds_id(db):
    _insert(db, artist_id="1", name="Kendrick Lamar", deezer_id="525046")
    # Trying to give Jorja the same id → conflict reports the holder.
    assert wu.source_id_conflict(db, "deezer_id", "525046", "2", "Jorja Smith") == "Kendrick Lamar"


def test_no_conflict_for_same_named_artist_two_servers(db):
    # Radiohead already on plex with this id; the jellyfin Radiohead (id 2) may
    # legitimately share it.
    _insert(db, artist_id="1", name="Radiohead", deezer_id="999")
    assert wu.source_id_conflict(db, "deezer_id", "999", "2", "Radiohead") is None


def test_no_conflict_when_same_row_holds_it(db):
    _insert(db, artist_id="1", name="Radiohead", deezer_id="999")
    # Re-matching the same artist to its own id is fine.
    assert wu.source_id_conflict(db, "deezer_id", "999", "1", "Radiohead") is None


def test_no_conflict_when_id_unused(db):
    assert wu.source_id_conflict(db, "deezer_id", "12345", "1", "Anyone") is None


def test_unknown_column_is_refused(db):
    assert wu.source_id_conflict(db, "evil_id", "x", "1", "Anyone") is None


# ---------------------------------------------------------------------------
# accept_artist_match — combined gate
# ---------------------------------------------------------------------------

def test_accept_rejects_name_mismatch(db):
    ok, reason = wu.accept_artist_match(db, "deezer_id", "1", "1", "ODESZA", "odessa")
    assert ok is False
    assert "name mismatch" in reason


def test_accept_rejects_id_already_claimed_by_other(db):
    _insert(db, artist_id="1", name="Kendrick Lamar", deezer_id="525046")
    ok, reason = wu.accept_artist_match(
        db, "deezer_id", "525046", "2", "Jorja Smith", "Jorja Smith"
    )
    assert ok is False
    assert "already claimed" in reason


def test_accept_passes_clean_match(db):
    ok, reason = wu.accept_artist_match(
        db, "deezer_id", "111", "1", "Kendrick Lamar", "Kendrick Lamar"
    )
    assert ok is True
    assert reason == ""
