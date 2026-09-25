"""The shared reads every enrichment worker makes, from lib2 (docs §32.3.1 stage 2).

Four helpers in ``core/worker_utils.py`` and ``core/enrichment/manual_match_honoring.py``
hold legacy SQL that twelve workers reach through: the artist-match acceptance
gate, the owned-catalog titles it disambiguates with, the expected-track-count
cache the Album Completeness job reads, and the stored-id fast path that keeps a
manual match from being searched over. Converting them once moves the shared half
of twelve conversions, so each worker's own change stays small.

The gate is the one with teeth. It exists because a single provider id smeared
across unrelated artists is a real bug that happened, and lib2 stores those ids in
two places — a promoted column for Spotify/MusicBrainz and ``external_ids`` for
everyone else — so a check that looked at only one of them would pass the smear
straight through.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.schema import ensure_library_v2_schema


@pytest.fixture
def conn(tmp_path):
    # Autocommit, so the second connection honor_stored_match opens sees what the
    # test just inserted — the point of that helper is that it does NOT reuse the
    # caller's connection.
    c = sqlite3.connect(str(tmp_path / "lib2.db"), isolation_level=None)
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    yield c
    c.close()


def _artist(conn, name, *, spotify_id=None, external_ids=None):
    return conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, spotify_id, external_ids) "
        "VALUES(?,?,?,?)",
        (name, name, spotify_id, json.dumps(external_ids or {})),
    ).lastrowid


def _album(conn, artist_id, title, **columns):
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
        "VALUES(?,?,'album')", (artist_id, title)).lastrowid
    for column, value in columns.items():
        conn.execute(
            f"UPDATE lib2_albums SET {column}=? WHERE id=?", (value, album_id))
    return album_id


def _own(conn, album_id, name="Song"):
    track = conn.execute(
        "INSERT INTO lib2_tracks(album_id,title) VALUES(?,?)", (album_id, name)
    ).lastrowid
    conn.execute(
        "INSERT INTO lib2_track_files(track_id,path,is_primary,file_state) "
        "VALUES(?,?,1,'active')", (track, f"/music/{album_id}.flac"))


class TestTheProviderIdConflictCheck:
    """Whether a differently-named artist already holds this provider id."""

    def test_a_free_id_is_no_conflict(self, conn):
        from core.library2.worker_support import provider_id_conflict

        artist = _artist(conn, "Rone")

        assert provider_id_conflict(
            conn, "discogs", "12345", artist, "Rone") is None

    def test_an_id_held_by_a_different_artist_is_reported(self, conn):
        from core.library2.worker_support import provider_id_conflict

        _artist(conn, "Rone Jazz Trio", external_ids={"discogs": "12345"})
        mine = _artist(conn, "Rone")

        assert provider_id_conflict(
            conn, "discogs", "12345", mine, "Rone") == "Rone Jazz Trio"

    def test_the_same_named_artist_is_not_a_conflict(self, conn):
        """The same artist indexed on two media servers legitimately shares an
        id — only a *different* artist holding it signals the smear."""
        from core.library2.worker_support import provider_id_conflict

        _artist(conn, "Rone", external_ids={"discogs": "12345"})
        mine = _artist(conn, "Rone")

        assert provider_id_conflict(
            conn, "discogs", "12345", mine, "Rone") is None

    def test_the_row_itself_is_not_its_own_conflict(self, conn):
        from core.library2.worker_support import provider_id_conflict

        mine = _artist(conn, "Rone", external_ids={"discogs": "12345"})

        assert provider_id_conflict(
            conn, "discogs", "12345", mine, "Rone") is None

    def test_a_promoted_column_is_searched_too(self, conn):
        """Spotify and MusicBrainz live in a real column, not external_ids. A
        check that only read the JSON would pass a smeared Spotify id."""
        from core.library2.worker_support import provider_id_conflict

        _artist(conn, "Some Other Band", spotify_id="sp-77")
        mine = _artist(conn, "Rone")

        assert provider_id_conflict(
            conn, "spotify", "sp-77", mine, "Rone") == "Some Other Band"

    def test_an_empty_id_is_never_a_conflict(self, conn):
        from core.library2.worker_support import provider_id_conflict

        mine = _artist(conn, "Rone")

        assert provider_id_conflict(conn, "discogs", "", mine, "Rone") is None
        assert provider_id_conflict(conn, "discogs", None, mine, "Rone") is None

    def test_a_different_services_id_does_not_collide(self, conn):
        """Numeric ids repeat across catalogues — Deezer 12345 and Discogs 12345
        are unrelated, and treating them as one would reject good matches."""
        from core.library2.worker_support import provider_id_conflict

        _artist(conn, "Unrelated", external_ids={"deezer": "12345"})
        mine = _artist(conn, "Rone")

        assert provider_id_conflict(
            conn, "discogs", "12345", mine, "Rone") is None


class TestTheAcceptanceGate:
    def test_a_matching_name_on_a_free_id_is_accepted(self, conn):
        from core.library2.worker_support import accept_artist_match

        artist = _artist(conn, "Rone")

        ok, reason = accept_artist_match(
            conn, "discogs", "12345", artist, "Rone", "Rone")

        assert ok and reason == ""

    def test_a_name_mismatch_is_rejected(self, conn):
        from core.library2.worker_support import accept_artist_match

        artist = _artist(conn, "ODESZA")

        ok, reason = accept_artist_match(
            conn, "discogs", "12345", artist, "ODESZA", "Odessa")

        assert not ok
        assert "name mismatch" in reason

    def test_a_claimed_id_is_rejected_even_with_a_matching_name(self, conn):
        from core.library2.worker_support import accept_artist_match

        _artist(conn, "Rone Jazz Trio", external_ids={"discogs": "12345"})
        mine = _artist(conn, "Rone")

        ok, reason = accept_artist_match(
            conn, "discogs", "12345", mine, "Rone", "Rone")

        assert not ok
        assert "Rone Jazz Trio" in reason


class TestOwnedAlbumTitles:
    def test_the_artists_own_albums_come_back(self, conn):
        from core.library2.worker_support import owned_album_titles

        artist = _artist(conn, "Rone")
        _own(conn, _album(conn, artist, "Tohu Bohu"))
        _own(conn, _album(conn, artist, "Creatures"))
        other = _artist(conn, "Someone Else")
        _own(conn, _album(conn, other, "Not Mine"))

        assert sorted(owned_album_titles(conn, artist)) == ["Creatures", "Tohu Bohu"]

    def test_provider_only_albums_are_excluded(self, conn):
        """The point of this list is what the user actually owns — a discography
        row the user has no files for is not evidence of anything."""
        from core.library2.worker_support import owned_album_titles

        artist = _artist(conn, "Rone")
        _own(conn, _album(conn, artist, "Owned"))
        _album(conn, artist, "Provider Only", origin="discography")

        assert owned_album_titles(conn, artist) == ["Owned"]

    def test_featured_credits_count_as_owned(self, conn):
        """A compilation credited to Various Artists still evidences the artist,
        and lib2 records that through the junction rather than primary_artist_id."""
        from core.library2.worker_support import owned_album_titles

        artist = _artist(conn, "Rone")
        various = _artist(conn, "Various Artists")
        album = _album(conn, various, "A Compilation")
        _own(conn, album)
        conn.execute(
            "INSERT INTO lib2_album_artists(album_id,artist_id,role) "
            "VALUES(?,?,'featured')", (album, artist))

        assert owned_album_titles(conn, artist) == ["A Compilation"]


class TestTheExpectedTrackCount:
    def test_a_positive_count_is_cached(self, conn):
        from core.library2.worker_support import set_expected_track_count

        artist = _artist(conn, "Rone")
        album = _album(conn, artist, "Tohu Bohu")

        set_expected_track_count(conn, album, 12)

        assert conn.execute(
            "SELECT expected_track_count FROM lib2_albums WHERE id=?",
            (album,)).fetchone()[0] == 12

    def test_nothing_is_written_without_a_usable_count(self, conn):
        """A source that carries no track info must not blank a good value
        another source already gave — same rule the legacy helper had."""
        from core.library2.worker_support import set_expected_track_count

        artist = _artist(conn, "Rone")
        album = _album(conn, artist, "Tohu Bohu", expected_track_count=12)

        for bad in (None, 0, -3, "abc", ""):
            set_expected_track_count(conn, album, bad)

        assert conn.execute(
            "SELECT expected_track_count FROM lib2_albums WHERE id=?",
            (album,)).fetchone()[0] == 12

    def test_a_later_source_replaces_an_earlier_one(self, conn):
        """Last write wins, as it did on legacy: any metadata-source count beats
        the observed-count fallback, and pinning the maximum would leave a deluxe
        edition's total making a standard album look permanently incomplete."""
        from core.library2.worker_support import set_expected_track_count

        artist = _artist(conn, "Rone")
        album = _album(conn, artist, "Tohu Bohu", expected_track_count=18)

        set_expected_track_count(conn, album, 12)

        assert conn.execute(
            "SELECT expected_track_count FROM lib2_albums WHERE id=?",
            (album,)).fetchone()[0] == 12


class _Db:
    """The tight-connection contract honor_stored_match keeps: it takes the
    database, not a connection, so the id read is closed before the provider call
    and before on_match opens its own connection to write."""

    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


@pytest.fixture
def db(tmp_path, conn):
    return _Db(str(tmp_path / "lib2.db"))


class TestTheStoredIdFastPath:
    def test_a_stored_id_is_found_in_external_ids(self, conn):
        from core.library2.worker_support import stored_provider_id

        artist = _artist(conn, "Rone", external_ids={"discogs": "12345"})

        assert stored_provider_id(conn, "artist", artist, "discogs") == "12345"

    def test_a_stored_id_is_found_in_a_promoted_column(self, conn):
        from core.library2.worker_support import stored_provider_id

        artist = _artist(conn, "Rone", spotify_id="sp-1")

        assert stored_provider_id(conn, "artist", artist, "spotify") == "sp-1"

    def test_no_stored_id_reads_as_none(self, conn):
        from core.library2.worker_support import stored_provider_id

        artist = _artist(conn, "Rone")

        assert stored_provider_id(conn, "artist", artist, "discogs") is None

    def test_the_callback_runs_when_a_stored_id_fetches(self, conn, db):
        from core.library2.worker_support import MATCHED, honor_stored_match

        artist = _artist(conn, "Rone", external_ids={"discogs": "12345"})
        seen = []

        assert honor_stored_match(
            db, entity_type="artist", entity_id=artist, service="discogs",
            fetch=lambda stored: {"id": stored, "name": "Rone"},
            on_match=lambda eid, stored, data: seen.append((eid, stored, data)),
        ) == MATCHED
        assert seen == [(artist, "12345", {"id": "12345", "name": "Rone"})]

    def test_without_a_stored_id_the_caller_falls_through(self, conn, db):
        from core.library2.worker_support import NO_STORED_ID, honor_stored_match

        artist = _artist(conn, "Rone")

        result = honor_stored_match(
            db, entity_type="artist", entity_id=artist, service="discogs",
            fetch=lambda stored: {"id": stored},
            on_match=lambda *a: None,
        )
        assert result == NO_STORED_ID
        assert not result, "falsy, so the caller still searches by name"

    def test_a_failed_fetch_keeps_the_stored_id_instead_of_searching(self, conn, db):
        """L2-005: a transient provider failure is not evidence that the id is
        wrong. Falling through sent the worker into a fuzzy name search that
        overwrote a deliberately chosen match with whatever came back."""
        from core.library2.provider_attempts import ensure_provider_attempt_schema
        from core.library2.worker_support import UNAVAILABLE, honor_stored_match

        ensure_provider_attempt_schema(conn.cursor())
        conn.commit()
        artist = _artist(conn, "Rone", external_ids={"discogs": "12345"})
        searched = []

        def boom(_stored):
            raise RuntimeError("rate limited")

        result = honor_stored_match(
            db, entity_type="artist", entity_id=artist, service="discogs",
            fetch=boom, on_match=lambda *a: searched.append(1),
        )

        assert result == UNAVAILABLE
        assert result, "truthy, so the caller skips search-by-name"
        assert searched == []
        # …and the failure is on record, so the queue backs off instead of
        # handing the same entity straight back on the next tick.
        row = conn.execute(
            "SELECT status, attempts FROM lib2_provider_attempts "
            "WHERE entity_type='artist' AND entity_id=? AND service='discogs'",
            (artist,)).fetchone()
        assert row["status"] == "error"

    def test_an_empty_response_also_keeps_the_stored_id(self, conn, db):
        """A provider answering "I have nothing for this id" is indistinguishable
        from one having a bad day, and both used to release the id."""
        from core.library2.worker_support import UNAVAILABLE, honor_stored_match

        artist = _artist(conn, "Rone", external_ids={"discogs": "12345"})

        assert honor_stored_match(
            db, entity_type="artist", entity_id=artist, service="discogs",
            fetch=lambda _stored: None, on_match=lambda *a: None,
        ) == UNAVAILABLE

    def test_a_missing_ledger_does_not_break_the_refresh(self, conn, db):
        from core.library2.worker_support import UNAVAILABLE, honor_stored_match

        conn.execute("DROP TABLE IF EXISTS lib2_provider_attempts")
        conn.commit()
        artist = _artist(conn, "Rone", external_ids={"discogs": "12345"})

        assert honor_stored_match(
            db, entity_type="artist", entity_id=artist, service="discogs",
            fetch=lambda _s: None, on_match=lambda *a: None,
        ) == UNAVAILABLE

    def test_a_callback_error_is_not_swallowed(self, conn, db):
        """A failed DB write is the worker's problem to hear about — swallowing it
        would report a match that never landed."""
        from core.library2.worker_support import honor_stored_match

        artist = _artist(conn, "Rone", external_ids={"discogs": "12345"})

        def boom(*_a):
            raise sqlite3.OperationalError("locked")

        with pytest.raises(sqlite3.OperationalError):
            honor_stored_match(
                db, entity_type="artist", entity_id=artist, service="discogs",
                fetch=lambda stored: {"id": stored}, on_match=boom,
            )


def test_the_module_holds_no_legacy_sql():
    import pathlib

    from tests.library2.legacy_usage import count_legacy_usage

    usage = count_legacy_usage(
        pathlib.Path("core/library2/worker_support.py").read_text())

    assert (usage.reads, usage.writes) == (0, 0)
