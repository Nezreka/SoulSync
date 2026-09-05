"""Batch-first selection, for providers with a per-parent bulk endpoint.

Spotify and iTunes both have "give me every album by this artist" and "give me
every track on this album" endpoints, and both build their queue around them: a
matched artist with unattempted albums is worth one API call for the whole set,
where the individual path would spend one per album. Falling back to individual
lookups only when the parent is itself unmatched is what keeps the daily API budget
survivable on a large library.

That is a materially different shape from ``next_pending`` — it selects a *parent*
and then works on its children — so it lives here rather than being bent into the
flat version. Both workers consumed the same item dicts already, differing only in
the service-prefixed key, so one function serves both.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.provider_attempts import (
    attempt_state, ensure_provider_attempt_schema, record_attempt,
)
from core.library2.schema import ensure_library_v2_schema
from core.library2.worker_queue import (
    next_batch_pending, pending_children, record_children,
)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "lib2.db"))
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    ensure_provider_attempt_schema(c.cursor())
    yield c
    c.close()


def _artist(conn, name="Rone", *, provider_id=None, service="spotify"):
    return conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, external_ids) VALUES(?,?,?)",
        (name, name, json.dumps({service: provider_id} if provider_id else {})),
    ).lastrowid


def _album(conn, artist_id, title="Tohu Bohu", *, provider_id=None,
           service="spotify", owned=True):
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type,external_ids) "
        "VALUES(?,?,'album',?)",
        (artist_id, title, json.dumps({service: provider_id} if provider_id else {})),
    ).lastrowid
    if owned:
        # The queue offers only what the user owns, and ownership is a live file
        # row — an album and its artist reach it through a track that has one.
        _track(conn, album, title=f"{title} (owned)", number=99)
    return album


def _track(conn, album_id, title="Bora", number=1):
    track = conn.execute(
        "INSERT INTO lib2_tracks(album_id,title,track_number) VALUES(?,?,?)",
        (album_id, title, number)).lastrowid
    conn.execute(
        "INSERT INTO lib2_track_files(track_id,path,is_primary,file_state) "
        "VALUES(?,?,1,'active')", (track, f"/music/{track}.flac"))
    return track


def _settle(conn, album_id, service="spotify"):
    """Mark an album and its tracks done, so only the artist is still due.

    `_album` gives the album a live file — the queue offers nothing else — which
    also makes that album and track legitimately pending work. Tests about the
    artist settle them first.
    """
    _matched(conn, "album", album_id, service)
    for row in conn.execute(
            "SELECT id FROM lib2_tracks WHERE album_id=?", (album_id,)).fetchall():
        _matched(conn, "track", int(row["id"]), service)


def _matched(conn, entity_type, entity_id, service="spotify"):
    record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                   service=service, status="matched")


class TestTheBatchIsPreferred:
    def test_an_unattempted_artist_comes_first(self, conn):
        artist = _artist(conn, provider_id="sp-a")
        _album(conn, artist)

        item = next_batch_pending(conn, "spotify")

        assert item["type"] == "artist"
        assert item["id"] == artist

    def test_a_matched_artist_with_pending_albums_yields_an_album_batch(self, conn):
        artist = _artist(conn, provider_id="sp-a")
        _album(conn, artist)
        _matched(conn, "artist", artist)

        item = next_batch_pending(conn, "spotify")

        assert item["type"] == "album_batch"
        assert item["artist_id"] == artist
        assert item["artist_name"] == "Rone"
        assert item["spotify_artist_id"] == "sp-a"
        assert item["name"] == "Albums for Rone"

    def test_the_provider_key_follows_the_service(self, conn):
        """Both workers already read item['<service>_artist_id']; keeping that
        rather than renaming it means their process methods do not have to change."""
        artist = _artist(conn, provider_id="it-a", service="itunes")
        _album(conn, artist, service="itunes")
        _matched(conn, "artist", artist, service="itunes")

        item = next_batch_pending(conn, "itunes")

        assert item["itunes_artist_id"] == "it-a"

    def test_an_artist_without_a_stored_id_yields_no_batch(self, conn):
        """The batch endpoint is addressed by the provider's own artist id. Without
        one there is nothing to call, and the albums fall to the individual path."""
        artist = _artist(conn)
        _album(conn, artist)
        _matched(conn, "artist", artist)

        item = next_batch_pending(conn, "spotify")

        assert item["type"] == "album_individual"

    def test_a_matched_album_with_pending_tracks_yields_a_track_batch(self, conn):
        artist = _artist(conn, provider_id="sp-a")
        album = _album(conn, artist, provider_id="sp-al", owned=False)
        _track(conn, album)
        _matched(conn, "artist", artist)
        _matched(conn, "album", album)

        item = next_batch_pending(conn, "spotify")

        assert item["type"] == "track_batch"
        assert item["album_id"] == album
        assert item["album_name"] == "Tohu Bohu"
        assert item["spotify_album_id"] == "sp-al"
        assert item["artist_name"] == "Rone"
        assert item["name"] == "Tracks on Tohu Bohu"

    def test_albums_are_batched_before_tracks(self, conn):
        artist = _artist(conn, provider_id="sp-a")
        album = _album(conn, artist, provider_id="sp-al", owned=False)
        second = _album(conn, artist, "Creatures")
        _track(conn, album)
        _matched(conn, "artist", artist)
        _matched(conn, "album", album)

        item = next_batch_pending(conn, "spotify")

        assert item["type"] == "album_batch", "the pending album outranks the track"
        assert second is not None


class TestTheIndividualFallback:
    def test_an_album_whose_artist_is_unmatched_goes_individual(self, conn):
        artist = _artist(conn)
        album = _album(conn, artist)
        record_attempt(conn, entity_type="artist", entity_id=artist,
                       service="spotify", status="not_found")

        item = next_batch_pending(conn, "spotify")

        assert item["type"] == "album_individual"
        assert item["id"] == album
        assert item["artist"] == "Rone"

    def test_a_track_whose_album_is_unmatched_goes_individual(self, conn):
        artist = _artist(conn)
        album = _album(conn, artist, owned=False)
        track = _track(conn, album)
        for entity_type, entity_id in (("artist", artist), ("album", album)):
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service="spotify", status="not_found")

        item = next_batch_pending(conn, "spotify")

        assert item["type"] == "track_individual"
        assert item["id"] == track

    def test_a_stale_not_found_comes_back_as_individual(self, conn):
        artist = _artist(conn, provider_id="sp-a")
        _settle(conn, _album(conn, artist))
        record_attempt(conn, entity_type="artist", entity_id=artist,
                       service="spotify", status="not_found")
        conn.execute("UPDATE lib2_provider_attempts "
                     "SET last_attempted_at=datetime('now','-90 days') "
                     "WHERE entity_type='artist'")

        item = next_batch_pending(conn, "spotify")

        assert item["type"] == "artist"
        assert item["id"] == artist

    def test_nothing_due_yields_nothing(self, conn):
        artist = _artist(conn, provider_id="sp-a")
        album = _album(conn, artist, provider_id="sp-al", owned=False)
        track = _track(conn, album)
        for entity_type, entity_id in (("artist", artist), ("album", album),
                                       ("track", track)):
            _matched(conn, entity_type, entity_id)

        assert next_batch_pending(conn, "spotify") is None


class TestThePinnedOverride:
    def test_a_pinned_group_jumps_ahead_as_an_individual_item(self, conn):
        """The pin means "work on tracks now", and the batch path is keyed off a
        matched parent, so the override serves the individual shape."""
        artist = _artist(conn, provider_id="sp-a")
        album = _album(conn, artist, owned=False)
        track = _track(conn, album)

        item = next_batch_pending(conn, "spotify", pinned="track")

        assert item["type"] == "track_individual"
        assert item["id"] == track
        assert album is not None

    def test_an_exhausted_pin_falls_through(self, conn):
        artist = _artist(conn, provider_id="sp-a")
        _settle(conn, _album(conn, artist))

        item = next_batch_pending(conn, "spotify", pinned="track")

        assert item["type"] == "artist"


class TestTheChildrenOfABatch:
    def test_only_unattempted_children_are_listed(self, conn):
        artist = _artist(conn, provider_id="sp-a")
        first = _album(conn, artist, "Tohu Bohu")
        second = _album(conn, artist, "Creatures")
        _matched(conn, "album", first)

        children = pending_children(conn, "spotify", "artist", artist,
                                    child="album")

        assert [child["id"] for child in children] == [second]
        assert children[0]["title"] == "Creatures"

    def test_track_children_carry_their_number(self, conn):
        """The batch matcher uses the track number as a tiebreak when two tracks
        on a release share a title."""
        artist = _artist(conn, provider_id="sp-a")
        album = _album(conn, artist, provider_id="sp-al", owned=False)
        track = _track(conn, album, "Bora", number=3)

        children = pending_children(conn, "spotify", "album", album, child="track")

        assert children == [{"id": track, "title": "Bora", "track_number": 3}]

    def test_a_whole_batch_can_be_marked_at_once(self, conn):
        """One failed bulk call is one outcome for every child — recording them
        individually would be the same write repeated N times."""
        artist = _artist(conn, provider_id="sp-a")
        first = _album(conn, artist, "Tohu Bohu")
        second = _album(conn, artist, "Creatures")

        assert record_children(conn, "spotify", "artist", artist, "error",
                               child="album") == 2

        for album_id in (first, second):
            state = attempt_state(conn, entity_type="album", entity_id=album_id)
            assert state["spotify"]["status"] == "error"

    def test_marking_a_batch_leaves_settled_children_alone(self, conn):
        artist = _artist(conn, provider_id="sp-a")
        settled = _album(conn, artist, "Tohu Bohu")
        _album(conn, artist, "Creatures")
        _matched(conn, "album", settled)

        assert record_children(conn, "spotify", "artist", artist, "not_found",
                               child="album") == 1

        assert attempt_state(conn, entity_type="album", entity_id=settled
                             )["spotify"]["status"] == "matched"


class TestTheBatchPathRespectsOwnership:
    """The single-item path was owned-filtered; the batch path was not.

    v2 keeps a watched artist's discography and the wishlist in the same tables
    as the owned library, so without the filter the batch queue handed the
    worker every provider-only release: API budget spent matching things the
    user does not own, which then became `matched` parents and seeded
    track_batch work of their own -- while the UI, reading the owned-filtered
    `pending_count`, showed 0 pending / 100%.
    """

    def test_pending_children_offers_only_owned_albums(self, conn):
        artist = _artist(conn, provider_id="sp-a")
        owned = _album(conn, artist, title="Owned")
        for n in range(3):
            _album(conn, artist, title=f"Discography {n}", owned=False)

        children = pending_children(
            conn, "spotify", "artist", artist, child="album")

        assert [c["title"] for c in children] == ["Owned"]

    def test_a_parent_whose_only_pending_children_are_unowned_is_not_batched(
            self, conn):
        artist = _artist(conn, provider_id="sp-a")
        owned = _album(conn, artist, title="Owned")
        _settle(conn, owned)
        _matched(conn, "artist", artist)
        for n in range(3):
            _album(conn, artist, title=f"Discography {n}", owned=False)

        # Nothing owned is left to do, so the queue must be empty rather than
        # offering an album_batch of the three discography rows.
        assert next_batch_pending(conn, "spotify") is None

    def test_pending_children_offers_only_owned_tracks(self, conn):
        artist = _artist(conn, provider_id="sp-a")
        album = _album(conn, artist, provider_id="sp-al", owned=False)
        owned = _track(conn, album, title="Owned Track", number=1)
        # A track with no file row is catalogued but not owned.
        conn.execute(
            "INSERT INTO lib2_tracks(album_id,title,track_number) VALUES(?,?,?)",
            (album, "Wishlist Track", 2))

        children = pending_children(
            conn, "spotify", "album", album, child="track")

        assert [c["title"] for c in children] == ["Owned Track"]


class TestProgressCountsTheSameUniverseTheQueueWorksOn:
    def test_discography_rows_do_not_inflate_the_denominator(self, conn):
        from core.library2.worker_queue import progress_breakdown

        artist = _artist(conn, provider_id="sp-a")
        owned = _album(conn, artist, title="Owned")
        for n in range(4):
            _album(conn, artist, title=f"Discography {n}", owned=False)
        _matched(conn, "album", owned)

        albums = progress_breakdown(conn, "spotify")["albums"]

        # 1 owned album, matched. A total of 5 could never reach 100%.
        assert albums == {"matched": 1, "total": 1, "percent": 100}
