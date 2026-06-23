"""#874 — wishlist ignore-list: TTL skip-gate for user-removed/cancelled tracks.

Two layers:
  * pure logic (core.wishlist.ignore) — TTL, id-normalization, display extract
  * DB seam (MusicDatabase on a temp db) — add/check/remove/list/clear, the
    add_to_wishlist gate, the manual-add bypass, and the regression that the
    success-cleanup removal path does NOT ignore.
"""

from datetime import datetime, timedelta

import pytest

from core.wishlist.ignore import (
    IGNORE_TTL_DAYS,
    REASON_CANCELLED,
    REASON_REMOVED,
    active_ignored_ids,
    extract_display,
    is_expired,
    is_ignored,
    normalize_ignore_id,
)
from database.music_database import MusicDatabase


# ── pure logic ──────────────────────────────────────────────────────────

def test_normalize_strips_composite_album_suffix():
    assert normalize_ignore_id("track123::album456") == "track123"
    assert normalize_ignore_id("  track123  ") == "track123"
    assert normalize_ignore_id("") == ""
    assert normalize_ignore_id(None) == ""


def test_is_expired_true_past_ttl_false_within():
    now = datetime(2026, 6, 15, 12, 0, 0)
    fresh = (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    stale = (now - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    assert is_expired(fresh, now) is False
    assert is_expired(stale, now) is True


def test_is_expired_unparseable_is_treated_expired_fail_open():
    # Corrupt timestamp must lapse (never wedge a track out of the wishlist).
    assert is_expired("not-a-date", datetime(2026, 6, 15)) is True
    assert is_expired("", datetime(2026, 6, 15)) is True
    assert is_expired(None, datetime(2026, 6, 15)) is True


def test_is_ignored_matches_composite_and_bare_ids():
    now = datetime(2026, 6, 15, 12, 0, 0)
    created = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    rows = [{"track_id": "abc", "created_at": created}]
    # Stored bare, queried with composite (and vice-versa) — both match.
    assert is_ignored(rows, "abc::album9", now) is True
    rows2 = [{"track_id": "abc", "created_at": created}]
    assert is_ignored(rows2, "abc", now) is True
    assert is_ignored(rows2, "different", now) is False


def test_active_ignored_ids_drops_expired():
    now = datetime(2026, 6, 15, 12, 0, 0)
    fresh = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    stale = (now - timedelta(days=99)).strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        {"track_id": "keep", "created_at": fresh},
        {"track_id": "drop", "created_at": stale},
    ]
    assert active_ignored_ids(rows, now) == {"keep"}


def test_extract_display_handles_dict_and_string_artists():
    assert extract_display({"name": "Song", "artists": [{"name": "A"}]}) == ("Song", "A")
    assert extract_display({"name": "Song", "artists": ["B"]}) == ("Song", "B")
    assert extract_display({}) == ("", "")
    assert extract_display(None) == ("", "")


# ── DB seam (temp database — never the live db) ─────────────────────────

@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _track(track_id="t1", name="Some Song", artist="Some Artist", album_id="alb1"):
    return {
        "id": track_id,
        "name": name,
        "artists": [{"name": artist}],
        "album": {"id": album_id, "name": "Some Album", "images": []},
    }


def test_add_check_remove_roundtrip(db):
    assert db.is_track_ignored("t1") is False
    assert db.add_to_wishlist_ignore("t1", "Song", "Artist", REASON_REMOVED) is True
    assert db.is_track_ignored("t1") is True
    # Composite id of the same base track is also considered ignored.
    assert db.is_track_ignored("t1::albX") is True
    assert db.remove_from_wishlist_ignore("t1") is True
    assert db.is_track_ignored("t1") is False


def test_get_and_clear_ignore_list(db):
    db.add_to_wishlist_ignore("a", "SongA", "ArtA", REASON_REMOVED)
    db.add_to_wishlist_ignore("b", "SongB", "ArtB", REASON_CANCELLED)
    entries = db.get_wishlist_ignore()
    assert {e["track_id"] for e in entries} == {"a", "b"}
    assert any(e["reason"] == "cancelled" for e in entries)
    assert db.clear_wishlist_ignore() == 2
    assert db.get_wishlist_ignore() == []


def test_expired_entry_is_not_active_and_gets_purged(db):
    db.add_to_wishlist_ignore("old", "Old", "Art", REASON_REMOVED)
    # Backdate it well past the TTL.
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE wishlist_ignore SET created_at = ? WHERE track_id = ?",
            ((datetime.now() - timedelta(days=IGNORE_TTL_DAYS + 5)).strftime("%Y-%m-%d %H:%M:%S"), "old"),
        )
        conn.commit()
    assert db.is_track_ignored("old") is False        # lapsed
    assert db.get_wishlist_ignore() == []             # and purged on read
    with db._get_connection() as conn:
        remaining = conn.execute("SELECT COUNT(*) c FROM wishlist_ignore").fetchone()["c"]
    assert remaining == 0


# ── the gate: add_to_wishlist honours the ignore-list ───────────────────

def test_gate_blocks_auto_readd_but_manual_bypasses_and_clears(db):
    track = _track("t1")
    # Auto add works first time.
    assert db.add_to_wishlist(track, source_type="playlist") is True
    # User removes + ignores it.
    db.remove_from_wishlist("t1")
    db.add_to_wishlist_ignore("t1", "Some Song", "Some Artist", REASON_REMOVED)
    # Auto re-add (watchlist / failed-capture / cancel) is now blocked.
    assert db.add_to_wishlist(track, source_type="playlist") is False
    assert db.is_track_ignored("t1") is True
    # A MANUAL add bypasses the gate AND clears the ignore so it sticks.
    assert db.add_to_wishlist(track, source_type="manual") is True
    assert db.is_track_ignored("t1") is False


def test_user_initiated_add_bypasses_and_clears_keeping_source_type(db):
    # #897 / carlosjfcasero: a user manually adds an album track they had
    # previously cancelled. It must bypass the gate AND clear the ignore — but
    # WITHOUT pretending to be source_type='manual' (the album modal sends
    # source_type='album', which the Albums/Singles categorisation relies on,
    # and which an automatic path like repair_worker also legitimately uses).
    track = _track("t7")
    db.add_to_wishlist_ignore("t7", "Owned Song", "Owned Artist", REASON_CANCELLED)
    # An automatic 'album' add (e.g. repair_worker) is still correctly blocked.
    assert db.add_to_wishlist(track, source_type="album") is False
    assert db.is_track_ignored("t7") is True
    # The explicit user click (user_initiated) goes through and clears the ignore,
    # while the stored source_type stays 'album'.
    assert db.add_to_wishlist(track, source_type="album", user_initiated=True) is True
    assert db.is_track_ignored("t7") is False
    # Provenance preserved: the stored row is still source_type='album', NOT 'manual'.
    row = next(r for r in db.get_wishlist_tracks() if str(r.get("spotify_track_id")) == "t7")
    assert row.get("source_type") == "album"


def test_gate_failopen_when_ignore_table_errors(db, monkeypatch):
    # If the ignore check raises, the add must still succeed (never block).
    monkeypatch.setattr(db, "is_track_ignored", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert db.add_to_wishlist(_track("t9"), source_type="playlist") is True


def test_route_remove_track_records_ignore(db, monkeypatch):
    # Pin the route → ignore wiring (not just the DB layer): a user-initiated
    # remove via the route function must drop the row AND ignore the track.
    import types
    from core.wishlist import routes as routes_module

    db.add_to_wishlist(_track("rt1", name="Route Song", artist="Route Artist"),
                       source_type="playlist")

    class _Svc:
        database = db

        def remove_track_from_wishlist(self, tid, profile_id=1):
            return db.remove_from_wishlist(tid, profile_id=profile_id)

    monkeypatch.setattr(routes_module, "get_wishlist_service", lambda: _Svc())
    runtime = types.SimpleNamespace(profile_id=1, logger=routes_module.module_logger)

    payload, status = routes_module.remove_track_from_wishlist(runtime, "rt1")
    assert status == 200
    assert db.is_track_ignored("rt1") is True
    # The ignore carries the captured label.
    entry = db.get_wishlist_ignore()[0]
    assert entry["track_name"] == "Route Song"
    assert entry["reason"] == REASON_REMOVED


def test_regression_success_cleanup_does_not_ignore(db):
    # The post-download success path calls remove_from_wishlist directly — it
    # must NOT add anything to the ignore-list (only user remove/cancel do).
    track = _track("t5")
    assert db.add_to_wishlist(track, source_type="playlist") is True
    db.remove_from_wishlist("t5")                 # simulate success cleanup
    assert db.is_track_ignored("t5") is False     # NOT ignored
    # And so a later legitimate auto-add still works.
    assert db.add_to_wishlist(track, source_type="playlist") is True
