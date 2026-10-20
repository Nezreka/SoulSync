import sqlite3
import pytest
from unittest.mock import patch
from core.automation.album_completion_verify import filter_still_missing


def test_disc_aware_stale_track_filter():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE albums (id INTEGER PRIMARY KEY, owner_profile_id INTEGER)")
    conn.execute("INSERT INTO albums VALUES (42, NULL)")
    conn.execute("CREATE TABLE tracks (album_id INTEGER, disc_number INTEGER, track_number INTEGER, owner_profile_id INTEGER, file_path TEXT)")
    conn.execute("INSERT INTO tracks VALUES (42, 1, 2, NULL, ?)", ("/music/owned.flac",))
    class DB:
        def _get_connection(self):
            return conn
        def _owner_scope_sql(self, scope, column):
            return ("owner_profile_id IS NULL", [])
    missing = [{"disc_number": 1, "track_number": 2}, {"disc_number": 2, "track_number": 2}]
    with patch("core.automation.album_completion_verify.library_scope_for_profile", return_value="shared"):
        result = filter_still_missing(DB(), 42, missing, 1)
    assert result == [missing[1]]


def test_other_profile_track_is_not_owned():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE albums (id INTEGER PRIMARY KEY, owner_profile_id INTEGER)")
    conn.execute("INSERT INTO albums VALUES (42, NULL)")
    conn.execute("CREATE TABLE tracks (album_id INTEGER, disc_number INTEGER, track_number INTEGER, owner_profile_id INTEGER, file_path TEXT)")
    conn.execute("INSERT INTO tracks VALUES (42, 1, 3, 2, ?)", ("/music/other.flac",))
    class DB:
        def _get_connection(self):
            return conn
        def _owner_scope_sql(self, scope, column):
            return ("owner_profile_id = ?", [scope])
    missing = [{"disc_number": 1, "track_number": 3}]
    with patch("core.automation.album_completion_verify.library_scope_for_profile", return_value=3):
        result = filter_still_missing(DB(), 42, missing, 3)
    assert result == missing


def test_related_album_existing_track_is_not_missing():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE albums (id INTEGER PRIMARY KEY, owner_profile_id INTEGER)"
    )
    conn.executemany(
        "INSERT INTO albums VALUES (?, NULL)", [(42,), (43,)]
    )
    conn.execute(
        "CREATE TABLE tracks (album_id INTEGER, disc_number INTEGER, "
        "track_number INTEGER, owner_profile_id INTEGER, file_path TEXT)"
    )
    conn.execute(
        "INSERT INTO tracks VALUES (43, 1, 9, NULL, '/music/owned.flac')"
    )

    class DB:
        def _get_connection(self):
            return conn

        def _owner_scope_sql(self, scope, column):
            return ("owner_profile_id IS NULL", [])

    missing = [
        {"disc_number": 1, "track_number": 9},
        {"disc_number": 1, "track_number": 10},
    ]

    with patch(
        "core.automation.album_completion_verify.library_scope_for_profile",
        return_value="shared",
    ):
        result = filter_still_missing(
            DB(), 42, missing, 1, [42, 43]
        )

    assert result == [missing[1]]


def test_related_personal_album_is_rejected():
    import pytest

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE albums (id INTEGER PRIMARY KEY, owner_profile_id INTEGER)"
    )
    conn.executemany(
        "INSERT INTO albums VALUES (?, ?)", [(42, None), (43, 2)]
    )
    conn.execute(
        "CREATE TABLE tracks (album_id INTEGER, disc_number INTEGER, "
        "track_number INTEGER, owner_profile_id INTEGER, file_path TEXT)"
    )

    class DB:
        def _get_connection(self):
            return conn

        def _owner_scope_sql(self, scope, column):
            return ("owner_profile_id IS NULL", [])

    with patch(
        "core.automation.album_completion_verify.library_scope_for_profile",
        return_value="shared",
    ):
        with pytest.raises(ValueError):
            filter_still_missing(
                DB(), 42,
                [{"disc_number": 1, "track_number": 9}],
                1, [42, 43],
            )


def test_malformed_related_album_groups_are_rejected():
    import pytest

    class DB:
        def _get_connection(self):
            pytest.fail("Invalid group must not reach the database")

    bad_groups = [
        [],
        [43],
        [42, 42],
        [42, ""],
        "42,43",
        list(range(101)),
    ]

    for group in bad_groups:
        with pytest.raises(ValueError):
            filter_still_missing(
                DB(), 42, [], 1, group
            )


@pytest.mark.parametrize("protected_album", [42, 43, 44])
@pytest.mark.parametrize("decision", ["pending", "partial", "complete"])
def test_completion_respects_review_choices_on_every_related_album(tmp_path, protected_album, decision):
    from core.library.reconciliation_choices import ensure_schema

    path = tmp_path / "library.db"
    with sqlite3.connect(path) as conn:
        ensure_schema(conn)
        conn.executescript("""
            CREATE TABLE albums (id INTEGER PRIMARY KEY, owner_profile_id INTEGER);
            INSERT INTO albums VALUES (42, NULL), (43, NULL), (44, NULL);
            CREATE TABLE tracks (id TEXT, album_id INTEGER, disc_number INTEGER,
                track_number INTEGER, owner_profile_id INTEGER, file_path TEXT);
            CREATE TABLE reconcile_album_plans (case_id TEXT, album_id TEXT, album_folder TEXT);
            CREATE TABLE repair_findings (id INTEGER, job_id TEXT, review_case_key TEXT,
                status TEXT, file_path TEXT, entity_id TEXT, user_action TEXT, details_json TEXT);
            CREATE TABLE reconcile_album_intent (album_id TEXT, scope TEXT);
        """)
        conn.execute("INSERT INTO reconcile_album_plans VALUES ('review', ?, '/album')", (str(protected_album),))
        if decision == "pending":
            conn.execute("INSERT INTO repair_findings VALUES (1, 'library_reconciliation', 'review', 'pending', NULL, NULL, NULL, '{}')")
        else:
            conn.execute("INSERT INTO reconcile_album_intent VALUES (?, ?)", (str(protected_album), decision))

    class DB:
        def _get_connection(self):
            return sqlite3.connect(path)

        def _owner_scope_sql(self, scope, column):
            return ("owner_profile_id IS NULL", [])

    missing = [{"disc_number": 1, "track_number": 2}]
    with patch("core.automation.album_completion_verify.library_scope_for_profile", return_value="shared"):
        if protected_album in (42, 43):
            with pytest.raises(ValueError, match="Review this album plan"):
                filter_still_missing(DB(), 42, missing, 1, [42, 43])
        else:
            assert filter_still_missing(DB(), 42, missing, 1, [42, 43]) == missing
