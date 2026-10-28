"""#1910: a mirror's tracks get checked against the library without a sync.

cremonies' playlist sat on the server with every track matched, and the
mirror still said "65 not synced yet": only a sync stamped the ownership
answer the statuses read. check_mirror_tracks stamps the same three fields
from the same question the download modal asks."""

import json

import core.downloads.master as master
from core.playlists import library_check
from core.playlists.track_status import IN_LIBRARY, MISSING, UNCHECKED, track_status


class _Track:
    def __init__(self, tid):
        self.id = tid


class _DB:
    def __init__(self, rows):
        self.rows = {r["id"]: r for r in rows}
        self.writes = []

    def update_mirrored_track_extra_data(self, track_id, data):
        row = self.rows.get(track_id)
        if row is None:
            return False
        extra = json.loads(row.get("extra_data") or "{}")
        extra.update(data)
        row["extra_data"] = json.dumps(extra)
        self.writes.append(track_id)
        return True


def _row(i, name, artist, extra=None, source_id=None):
    return {"id": i, "track_name": name, "artist_name": artist,
            "source_track_id": source_id or f"dz{i}",
            "extra_data": json.dumps(extra) if extra is not None else None}


def _patch(monkeypatch, owned, manual=()):
    asked = []

    def fake_match(db, q, **kw):
        asked.append((q["name"], tuple(q["artists"])))
        if q["name"] in owned:
            return True, 0.9, _Track(owned[q["name"]])
        return False, 0.0, None

    def fake_manual(db, profile_id, q, source):
        return {"library_track_id": 77} if q["name"] in manual else None

    monkeypatch.setattr(master, "match_track_in_library", fake_match)
    monkeypatch.setattr(master, "live_manual_match", fake_manual)
    return asked


def _extra(db, i):
    return json.loads(db.rows[i]["extra_data"])


def test_stamps_owned_and_missing_the_way_a_sync_does(monkeypatch):
    db = _DB([_row(1, "Mr. Brightside", "The Killers"), _row(2, "Rare Cut", "Nobody")])
    _patch(monkeypatch, owned={"Mr. Brightside": 501})
    out = library_check.check_mirror_tracks(db, list(db.rows.values()), profile_id=1,
                                            source="deezer", active_server="navidrome")
    assert out == {"checked": 2, "found": 1, "remaining": 0}
    one, two = _extra(db, 1), _extra(db, 2)
    assert one["in_library"] is True and one["library_track_id"] == 501
    assert two["in_library"] is False and two["library_track_id"] is None
    assert one["library_checked_at"] and two["library_checked_at"]
    # and the statuses read them like a sync's stamp
    assert track_status(db.rows[1], set()) == IN_LIBRARY
    assert track_status(db.rows[2], set()) == MISSING


def test_already_checked_rows_are_left_to_the_sync(monkeypatch):
    synced = {"in_library": False, "library_checked_at": 123}
    db = _DB([_row(1, "A", "X", synced), _row(2, "B", "Y", {"in_library": True}), _row(3, "C", "Z")])
    asked = _patch(monkeypatch, owned={})
    out = library_check.check_mirror_tracks(db, list(db.rows.values()), profile_id=1,
                                            source="deezer", active_server=None)
    assert out["checked"] == 1 and db.writes == [3]
    assert [a[0] for a in asked] == ["C"]
    assert _extra(db, 1)["library_checked_at"] == 123   # untouched


def test_asks_by_the_identified_match_not_the_source_title(monkeypatch):
    extra = {"discovered": True,
             "matched_data": {"id": "dz9", "name": "Gives You Hell", "artists": ["The All-American Rejects"]}}
    db = _DB([_row(1, "gives you hell (clean)", "AAR", extra)])
    asked = _patch(monkeypatch, owned={"Gives You Hell": 9})
    library_check.check_mirror_tracks(db, list(db.rows.values()), profile_id=1,
                                      source="deezer", active_server=None)
    assert asked == [("Gives You Hell", ("The All-American Rejects",))]
    assert _extra(db, 1)["in_library"] is True


def test_a_wing_it_stub_is_not_a_match_to_ask_by(monkeypatch):
    extra = {"discovered": True, "wing_it_fallback": True,
             "matched_data": {"id": "wing_it_abc", "name": "Guess", "artists": ["Who"]}}
    db = _DB([_row(1, "Real Title", "Real Artist", extra)])
    asked = _patch(monkeypatch, owned={})
    library_check.check_mirror_tracks(db, list(db.rows.values()), profile_id=1,
                                      source="deezer", active_server=None)
    assert asked == [("Real Title", ("Real Artist",))]


def test_a_saved_manual_match_counts_as_owned(monkeypatch):
    db = _DB([_row(1, "Picked By Hand", "Someone")])
    asked = _patch(monkeypatch, owned={}, manual={"Picked By Hand"})
    library_check.check_mirror_tracks(db, list(db.rows.values()), profile_id=1,
                                      source="deezer", active_server=None)
    assert asked == []
    assert _extra(db, 1)["in_library"] is True and _extra(db, 1)["library_track_id"] == 77


def test_a_big_playlist_is_checked_a_slice_at_a_time(monkeypatch):
    db = _DB([_row(i, f"T{i}", "A") for i in range(1, 8)])
    _patch(monkeypatch, owned={})
    out = library_check.check_mirror_tracks(db, list(db.rows.values()), profile_id=1,
                                            source="deezer", active_server=None, limit=3)
    assert out == {"checked": 3, "found": 0, "remaining": 4}
    rest = library_check.check_mirror_tracks(db, list(db.rows.values()), profile_id=1,
                                             source="deezer", active_server=None, limit=10)
    assert rest["checked"] == 4 and rest["remaining"] == 0
    assert all(track_status(r, set()) != UNCHECKED for r in db.rows.values())


def test_one_failing_lookup_does_not_stop_the_rest(monkeypatch):
    db = _DB([_row(1, "Boom", "A"), _row(2, "Fine", "B")])

    def fake_match(db_, q, **kw):
        if q["name"] == "Boom":
            raise RuntimeError("db hiccup")
        return False, 0.0, None

    monkeypatch.setattr(master, "match_track_in_library", fake_match)
    monkeypatch.setattr(master, "live_manual_match", lambda *a, **k: None)
    out = library_check.check_mirror_tracks(db, list(db.rows.values()), profile_id=1,
                                            source="deezer", active_server=None)
    # the failed row stays unchecked rather than being stamped missing
    assert out["checked"] == 1 and db.writes == [2]
    assert track_status(db.rows[1], set()) == UNCHECKED


# ── the route ───────────────────────────────────────────────────────────────

def _route_client(db, monkeypatch, *, admin, profile_id):
    from flask import Flask, g

    import api.mirrored_playlists as mod
    monkeypatch.setattr(mod, "get_database", lambda: db)
    monkeypatch.setattr(mod, "get_current_profile_id", lambda: profile_id)
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.is_admin = admin

    app.register_blueprint(mod.create_blueprint())
    return app.test_client()


def test_route_stamps_the_mirror_and_refuses_someone_elses(tmp_path, monkeypatch):
    from database.music_database import MusicDatabase

    db = MusicDatabase(database_path=str(tmp_path / "music.db"))
    pk = db.mirror_playlist(
        source="deezer", source_playlist_id="p1", name="alternative",
        tracks=[{"track_name": "Song", "artist_name": "Artist", "source_track_id": "dz1"}],
        profile_id=1,
    )
    _patch(monkeypatch, owned={"Song": 5})

    stranger = _route_client(db, monkeypatch, admin=False, profile_id=2)
    assert stranger.post(f"/api/mirrored-playlists/{pk}/check-library").status_code == 404
    assert track_status(db.get_mirrored_playlist_tracks(pk)[0], set()) == UNCHECKED

    owner = _route_client(db, monkeypatch, admin=False, profile_id=1)
    resp = owner.post(f"/api/mirrored-playlists/{pk}/check-library")
    assert resp.status_code == 200, resp.data
    assert resp.get_json() == {"success": True, "checked": 1, "found": 1, "remaining": 0}
    assert track_status(db.get_mirrored_playlist_tracks(pk)[0], set()) == IN_LIBRARY


# ── budget and re-asking ────────────────────────────────────────────────────

def test_a_slow_library_stops_at_the_time_budget_and_still_moves(monkeypatch):
    import itertools

    db = _DB([_row(i, f"T{i}", "A") for i in range(1, 6)])
    _patch(monkeypatch, owned={})
    clock = itertools.count(0, 10)   # every monotonic() read is 10s later
    monkeypatch.setattr(library_check.time, "monotonic", lambda: next(clock))
    out = library_check.check_mirror_tracks(db, list(db.rows.values()), profile_id=1,
                                            source="deezer", active_server=None, time_budget=15)
    # the first row always runs, so a slow library still makes progress
    assert 1 <= out["checked"] < 5
    assert out["checked"] + out["remaining"] == 5


def test_its_own_miss_is_asked_again_once_stale(monkeypatch):
    stale = {"in_library": False, "library_checked_at": 1000, "library_checked_via": "check"}
    fresh = {"in_library": False, "library_checked_at": 1000 + library_check.RECHECK_MISSING_AFTER - 5,
             "library_checked_via": "check"}
    now = 1000 + library_check.RECHECK_MISSING_AFTER
    rows = [_row(1, "Stale", "A", stale), _row(2, "Fresh", "A", fresh)]
    assert [r["id"] for r in library_check.unchecked_rows(rows, now=now)] == [1]


def test_a_sync_or_manual_verdict_is_never_reasked(monkeypatch):
    old = 1000
    now = old + 10 * library_check.RECHECK_MISSING_AFTER
    rows = [
        _row(1, "Synced", "A", {"in_library": False, "library_checked_at": old,
                                "library_checked_via": "sync"}),
        _row(2, "Unmatched", "A", {"in_library": False, "library_checked_at": old,
                                   "library_checked_via": "manual"}),
        # a sync from before the marker existed
        _row(3, "Legacy", "A", {"in_library": False, "library_checked_at": old}),
    ]
    assert library_check.unchecked_rows(rows, now=now) == []


def test_the_stamp_says_it_came_from_the_check(monkeypatch):
    db = _DB([_row(1, "X", "A")])
    _patch(monkeypatch, owned={})
    library_check.check_mirror_tracks(db, list(db.rows.values()), profile_id=1,
                                      source="deezer", active_server=None)
    assert _extra(db, 1)["library_checked_via"] == "check"


def test_sync_and_manual_stamps_carry_their_own_marker():
    import inspect

    from core.discovery import sync
    from core.library import manual_library_match

    assert "'library_checked_via': 'sync'" in inspect.getsource(sync._record_library_membership)
    assert inspect.getsource(manual_library_match).count('"library_checked_via": "manual"') == 2


def test_the_detail_says_how_many_are_due(tmp_path, monkeypatch):
    from database.music_database import MusicDatabase

    db = MusicDatabase(database_path=str(tmp_path / "music.db"))
    pk = db.mirror_playlist(
        source="deezer", source_playlist_id="p1", name="alternative",
        tracks=[{"track_name": f"S{i}", "artist_name": "A", "source_track_id": f"dz{i}"}
                for i in range(3)],
        profile_id=1,
    )
    first = db.get_mirrored_playlist_tracks(pk)[0]
    db.update_mirrored_track_extra_data(first["id"], {
        "in_library": False, "library_checked_at": 1, "library_checked_via": "sync"})
    c = _route_client(db, monkeypatch, admin=True, profile_id=1)
    body = c.get(f"/api/mirrored-playlists/{pk}").get_json()
    assert body["library_check_due"] == 2
    assert body["status_counts"]["unchecked"] == 2
