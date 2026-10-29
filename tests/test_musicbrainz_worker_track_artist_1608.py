"""#1608 (cremonies): the musicbrainz worker asked for a soundtrack track by the
library (album) artist. 'Tulou Tagaloa' by Lin-Manuel Miranda finds nothing,
by Olivia Foa'i it's score 100. the track's own artist sits in
lib2_track_artists and must be read by the native queue. now it's tried first, the album
artist stays the fallback.

real tmp db for the queue query, fake mb service, no network.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core import musicbrainz_worker as mbw
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _seed(db, track_artist):
    from core.library2.provider_attempts import record_attempt
    from tests.lib2_seed import track

    with db._get_connection() as conn:
        track_id = track(conn, "Lin-Manuel Miranda", "Moana (Deluxe)",
                         "Tulou Tagaloa", duration=52000, credit=track_artist)
        # Queue bookkeeping lives in the provider ledger, including the guest.
        for row in conn.execute("SELECT id FROM lib2_artists"):
            record_attempt(conn, entity_type="artist", entity_id=row[0],
                           service="musicbrainz", status="matched")
        for row in conn.execute("SELECT id FROM lib2_albums"):
            record_attempt(conn, entity_type="album", entity_id=row[0],
                           service="musicbrainz", status="matched")
        conn.commit()
    return track_id


def _worker(db, answers):
    """answers: artist name -> match_recording result."""
    service = MagicMock()
    service._artist_row_mbid.side_effect = lambda name: {"Lin-Manuel Miranda": "mbid-lmm"}.get(name)
    service.match_recording.side_effect = lambda title, artist, **kw: answers.get(artist)
    with patch.object(mbw, "MusicBrainzService", return_value=service):
        worker = mbw.MusicBrainzWorker(db)
    return worker, service


def _asked(service):
    return [(c.args[1], c.kwargs.get("artist_mbid")) for c in service.match_recording.call_args_list]


def test_the_tracks_own_artist_is_asked_first(db):
    track_id = _seed(db, "Olivia Foa'i")
    worker, service = _worker(db, {"Olivia Foa'i": {"mbid": "rec-tulou"}})

    item = worker._get_next_item()
    assert item["type"] == "track" and item["track_artist"] == "Olivia Foa'i"
    worker._process_item(item)

    assert _asked(service) == [("Olivia Foa'i", None)]   # not pinned to lin-manuel's mbid
    service.update_track_mbid.assert_called_once_with(
        track_id, "rec-tulou", "matched", recording_disambiguation=None)


def test_falls_back_to_the_album_artist_when_the_track_artist_finds_nothing(db):
    track_id = _seed(db, "Olivia Foa'i")
    worker, service = _worker(db, {"Lin-Manuel Miranda": {"mbid": "rec-lmm"}})

    worker._process_item(worker._get_next_item())

    assert _asked(service) == [("Olivia Foa'i", None), ("Lin-Manuel Miranda", "mbid-lmm")]
    service.update_track_mbid.assert_called_once_with(
        track_id, "rec-lmm", "matched", recording_disambiguation=None)


@pytest.mark.parametrize("track_artist", [None, "", "lin-manuel miranda"])
def test_a_normal_album_track_asks_once_like_before(db, track_artist):
    track_id = _seed(db, track_artist)
    worker, service = _worker(db, {})

    worker._process_item(worker._get_next_item())

    assert _asked(service) == [("Lin-Manuel Miranda", "mbid-lmm")]
    service.update_track_mbid.assert_called_once_with(track_id, None, "not_found")


def test_track_match_artists_order():
    assert mbw.track_match_artists("Olivia Foa'i", "Lin-Manuel Miranda") == ["Olivia Foa'i", "Lin-Manuel Miranda"]
    assert mbw.track_match_artists(None, "Weezer") == ["Weezer"]
    assert mbw.track_match_artists("WEEZER", "Weezer") == ["Weezer"]
    assert mbw.track_match_artists("Solo", None) == ["Solo"]
