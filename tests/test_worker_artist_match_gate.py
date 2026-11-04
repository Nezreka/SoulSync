"""The name half of the shared artist-match gate (``core/worker_utils.py``).

``artist_name_matches`` is a stricter gate (0.85) than the 0.80 used for
album/track titles, so short-name false positives ('ODESZA'/'odessa',
'Blance'/'Blanke', 'Lady A'/'Lady Gaga') are rejected.
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


# The conflict half of this gate moved to ``worker_support.provider_id_conflict``
# / ``accept_artist_match``, which ask ``lib2_artists`` — a V2-native artist has
# no legacy twin, so the old ``SELECT name FROM artists`` saw an empty table and
# waved through exactly the collision it existed to stop. Pinned in
# ``tests/library2/test_worker_support.py``; the legacy helpers are deleted.
