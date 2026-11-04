"""#1315 on Library v2: a soundtrack copy is the same recording as its album cut.

Upstream's Duplicate Detector learned to drop a dash-form provenance tail
(``Rabbit Run - From "8 Mile" Soundtrack``); the detector is retired here, and
the same comparison lives in the canonical-link / file-move validator. Without
the port, linking kevin2xk's pair by hand was refused with "Track titles do
not match".
"""

from __future__ import annotations

import pytest

from core.library2.duplicate_relationship import (
    DuplicateRelationshipError,
    validate_duplicate_pair,
)
from database.music_database import MusicDatabase
from tests.lib2_seed import track


@pytest.fixture()
def conn(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    connection = db._get_connection()
    yield connection
    connection.close()


def test_a_soundtrack_copy_links_to_its_album_cut(conn):
    album_cut = track(conn, "Eminem", "The Eminem Show", "Rabbit Run", duration=192_000)
    soundtrack = track(conn, "Eminem", "8 Mile", 'Rabbit Run - From "8 Mile" Soundtrack',
                       duration=193_000)
    conn.commit()

    pair = validate_duplicate_pair(conn, soundtrack, album_cut)

    assert pair["source"]["id"] == soundtrack


def test_a_parenthesized_from_stays_a_different_title(conn):
    plain = track(conn, "Taylor Swift", "1989", "Slut!", duration=180_000)
    vault = track(conn, "Taylor Swift", "1989 (TV)", "Slut! (From The Vault)", duration=180_000)
    conn.commit()

    with pytest.raises(DuplicateRelationshipError, match="titles do not match"):
        validate_duplicate_pair(conn, vault, plain)


def test_a_feat_credit_in_the_title_is_the_same_recording(conn):
    """#1568 on Library v2: one source tags the guests into the title, the
    other does not. The credit names who is on it, not which version."""
    plain = track(conn, "Eminem", "Relapse", "Crack A Bottle", duration=297_000)
    credited = track(conn, "Eminem", "Relapse (Deluxe)",
                     "Crack a Bottle (feat. Dr. Dre & 50 Cent)", duration=298_000)
    conn.commit()

    pair = validate_duplicate_pair(conn, credited, plain)

    assert pair["source"]["id"] == credited


def test_a_remix_stays_a_different_title(conn):
    plain = track(conn, "Eminem", "Relapse", "Crack A Bottle", duration=297_000)
    remix = track(conn, "Eminem", "Remixes", "Crack A Bottle (Remix)", duration=297_000)
    conn.commit()

    with pytest.raises(DuplicateRelationshipError, match="titles do not match"):
        validate_duplicate_pair(conn, remix, plain)
