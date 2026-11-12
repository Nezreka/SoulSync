"""#2055: what kind of release an album is, kept for the artist page's
sections (Live, Soundtracks, ...), and never in the column path templates read.

No network: the MusicBrainz client is a stub.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from core.library2 import mb_reconcile as R
from core.library2 import release_group_types as T
from database.music_database import MusicDatabase
from tests import lib2_seed

GROUP = "f17d521f-f8e9-41d8-9b0e-e270d5d905ed"
OTHER = "0a1b2c3d-0000-4000-8000-000000000001"


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _album(db, title="Roseland NYC Live", group=GROUP):
    with db._get_connection() as conn:
        lib2_seed.track(conn, "Portishead", title, "Humming")
        album_id = conn.execute("SELECT id FROM lib2_albums WHERE title=?", (title,)).fetchone()[0]
        conn.execute("UPDATE lib2_albums SET musicbrainz_release_group_id=? WHERE id=?",
                     (group, album_id))
        conn.execute(
            "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
            "SELECT id, primary_artist_id, 'primary' FROM lib2_albums WHERE id=?", (album_id,))
        conn.commit()
    return album_id


def _row(db, album_id):
    with db._get_connection() as conn:
        return dict(conn.execute(
            "SELECT release_group_types, secondary_types, musicbrainz_release_group_id "
            "FROM lib2_albums WHERE id=?", (album_id,)).fetchone())


def _known(db, album_id):
    row = _row(db, album_id)
    return T.stored_types(row["release_group_types"], row["musicbrainz_release_group_id"])


# --- the value -------------------------------------------------------------


def test_group_types_reads_what_the_group_says_lower_cased():
    assert T.group_types({"id": GROUP, "secondary-types": ["Live", "Compilation", "Live"]}) == [
        "live", "compilation"]
    # a studio release: asked, and the answer is "none"
    assert T.group_types({"id": GROUP, "secondary-types": []}) == []


def test_a_group_that_does_not_say_is_not_read_as_none():
    assert T.group_types({"id": GROUP}) is None
    assert T.group_types(None) is None
    assert T.group_types({"secondary-types": "Live"}) is None


def test_stored_types_count_only_for_the_group_they_were_read_from():
    raw = json.dumps({"group": GROUP, "types": ["live"]})
    assert T.stored_types(raw, GROUP) == ["live"]
    assert T.stored_types(raw, GROUP.upper()) == ["live"]
    assert T.stored_types(raw, OTHER) is None      # re-matched since
    assert T.stored_types(raw, None) is None
    assert T.stored_types(None, GROUP) is None     # never asked
    assert T.stored_types("not json", GROUP) is None
    assert T.stored_types(json.dumps({"group": GROUP, "types": []}), GROUP) == []


def test_stamp_writes_once_and_never_the_filed_types(db):
    album_id = _album(db)
    with db._get_connection() as conn:
        assert T.stamp(conn, album_id, GROUP, ["live"]) is True
        assert T.stamp(conn, album_id, GROUP, ["live"]) is False   # already says so
        assert T.stamp(conn, album_id, GROUP, None) is False        # nothing to say
        assert T.stamp(conn, album_id, "", ["live"]) is False
        conn.commit()
    row = _row(db, album_id)
    assert _known(db, album_id) == ["live"]
    # $atypes in a path template reads this one: a later reorganize must not
    # find a "[Live]" the download was never filed with
    assert row["secondary_types"] == "[]"


# --- who fills it in ---------------------------------------------------------


class _Browse:
    def __init__(self, groups):
        self.groups = groups

    def browse_artist_release_groups(self, artist_mbid, release_types=None, limit=100, offset=0):
        return self.groups[offset:offset + limit]


def test_the_artist_reconcile_keeps_each_groups_types(db):
    live = _album(db, "Roseland NYC Live", GROUP)
    studio = _album(db, "Dummy", OTHER)
    with db._get_connection() as conn:
        artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Portishead'").fetchone()[0]
        conn.execute("UPDATE lib2_artists SET musicbrainz_id=? WHERE id=?",
                     ("8f6bd1e4-fbe1-4f50-aa9b-94c450ec0f11", artist_id))
        conn.commit()
    stats = R.reconcile_artist_release_groups(db, artist_id, client=_Browse([
        {"id": GROUP, "title": "Roseland NYC Live", "secondary-types": ["Live"]},
        {"id": OTHER, "title": "Dummy", "secondary-types": []},
    ]))
    assert stats["typed"] == 2
    assert _known(db, live) == ["live"]
    assert _known(db, studio) == []
    # and a second run has nothing left to write
    again = R.reconcile_artist_release_groups(db, artist_id, client=_Browse([
        {"id": GROUP, "title": "Roseland NYC Live", "secondary-types": ["Live"]},
        {"id": OTHER, "title": "Dummy", "secondary-types": []},
    ]))
    assert again["typed"] == 0


def _worker(db, service):
    from core import musicbrainz_worker as mbw
    with patch.object(mbw, "MusicBrainzService", return_value=service):
        return mbw.MusicBrainzWorker(db)


def test_the_worker_asks_for_an_album_that_has_its_group_but_not_its_kind(db):
    album_id = _album(db)
    service = MagicMock()
    worker = _worker(db, service)
    with db._get_connection() as conn:
        item = worker._group_types_item(conn)
        assert item == {"type": "album_types", "id": album_id,
                        "name": "Roseland NYC Live", "group_id": GROUP}
        # one MusicBrainz had no answer for is passed over, not asked every loop
        assert worker._group_types_item(conn) is None

    service.lookup_release_group_types.return_value = ["live"]
    worker._process_item(item)
    service.lookup_release_group_types.assert_called_once_with(GROUP)
    service.update_album_release_group_types.assert_called_once_with(album_id, GROUP, ["live"])


def test_the_worker_stores_nothing_when_musicbrainz_has_no_answer(db):
    album_id = _album(db)
    service = MagicMock()
    service.lookup_release_group_types.return_value = None
    worker = _worker(db, service)
    worker._process_item({"type": "album_types", "id": album_id, "name": "x", "group_id": GROUP})
    service.update_album_release_group_types.assert_not_called()


def test_an_album_whose_kind_is_known_is_not_asked_again(db):
    album_id = _album(db)
    with db._get_connection() as conn:
        T.stamp(conn, album_id, GROUP, [])
        conn.commit()
    worker = _worker(db, MagicMock())
    with db._get_connection() as conn:
        assert worker._group_types_item(conn) is None


def test_the_service_reads_the_types_off_the_lookups_it_already_makes(db):
    from core.musicbrainz_service import MusicBrainzService
    service = MusicBrainzService.__new__(MusicBrainzService)
    service.db = db
    service.mb_client = MagicMock()
    service.mb_client.get_release.return_value = {
        "id": "rel", "release-group": {"id": GROUP.upper(), "secondary-types": ["Soundtrack"]}}
    assert service.lookup_release_group_and_types("rel") == (GROUP, ["soundtrack"])
    assert service.lookup_release_group("rel") == GROUP
    service.mb_client.get_release_group.return_value = {"id": GROUP, "secondary-types": []}
    assert service.lookup_release_group_types(GROUP) == []
    service.mb_client.get_release_group.return_value = None
    assert service.lookup_release_group_types(GROUP) is None

    album_id = _album(db)
    service.update_album_release_group_types(album_id, GROUP, ["soundtrack"])
    assert _known(db, album_id) == ["soundtrack"]


# --- what the page is told ---------------------------------------------------


def _page_types(db, title):
    from core.library2.queries import get_artist
    with db._get_connection() as conn:
        artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Portishead'").fetchone()[0]
        artist = get_artist(conn, artist_id)
    rows = [*artist["albums"], *artist.get("eps", []), *artist["singles"]]
    return next(r for r in rows if r["title"] == title)["release_types"]


def test_the_artist_page_says_what_is_known_and_none_when_nothing_is(db):
    album_id = _album(db)
    _album(db, "Dummy", OTHER)
    assert _page_types(db, "Roseland NYC Live") is None
    with db._get_connection() as conn:
        T.stamp(conn, album_id, GROUP, ["live"])
        conn.commit()
    assert _page_types(db, "Roseland NYC Live") == ["live"]
    assert _page_types(db, "Dummy") is None


def test_types_read_from_another_group_are_not_shown(db):
    album_id = _album(db)
    with db._get_connection() as conn:
        T.stamp(conn, album_id, GROUP, ["live"])
        conn.execute("UPDATE lib2_albums SET musicbrainz_release_group_id=? WHERE id=?",
                     (OTHER, album_id))
        conn.commit()
    assert _page_types(db, "Roseland NYC Live") is None


def test_the_types_a_download_was_filed_with_come_first(db):
    album_id = _album(db)
    with db._get_connection() as conn:
        T.stamp(conn, album_id, GROUP, [])
        conn.execute("UPDATE lib2_albums SET secondary_types=? WHERE id=?",
                     (json.dumps(["Soundtrack"]), album_id))
        conn.commit()
    assert _page_types(db, "Roseland NYC Live") == ["soundtrack"]
