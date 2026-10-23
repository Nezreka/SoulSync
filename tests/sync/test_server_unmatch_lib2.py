"""Server playlist unmatch on the Library v2 catalogue.

A durable manual match names a catalogue track; the compare view and the
unmatch speak the server's id. The unmatch translates the stored id before it
compares, so a match made in the manual-match tool is dropped too, and a
catalogue id that happens to equal some other server id is left alone.
"""

from __future__ import annotations

import pytest

from core.sync.match_overrides import reject_match
from database.music_database import MusicDatabase
from tests import lib2_seed


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "unmatch.db"))


def _server_track(db, title, server_id):
    with db._get_connection() as conn:
        track_id = lib2_seed.track(conn, "TAYA.", "Fauna", title,
                                   path=f"/music/{title}.flac",
                                   server_source="navidrome", server_id=server_id)
        conn.commit()
    return track_id


def test_a_catalogue_match_on_that_server_track_is_dropped(db):
    native = _server_track(db, "Sanctuary", "77")
    assert str(native) != "77"
    db.save_manual_library_match(1, "spotify", "sp1", str(native), server_source="navidrome",
                                 library_track_id_kind="lib2")

    assert reject_match(db, 1, "navidrome", "sp1", "77")
    assert db.find_manual_library_match_by_source_track_id(1, "sp1", "navidrome") is None


def test_a_catalogue_id_equal_to_another_server_id_is_kept(db):
    # catalogue track 1 is server track 900; the user unmatches server track "1"
    native = _server_track(db, "Sanctuary", "900")
    db.save_manual_library_match(1, "spotify", "sp1", str(native), server_source="navidrome",
                                 library_track_id_kind="lib2")

    reject_match(db, 1, "navidrome", "sp1", str(native))
    assert db.find_manual_library_match_by_source_track_id(1, "sp1", "navidrome") is not None
