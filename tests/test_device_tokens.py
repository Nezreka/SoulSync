"""device tokens (PAIRING_SPEC.md 4): the row's states, the pick window, the
once-only pick, revocation, and the gate's decision for every state."""

from __future__ import annotations

import sqlite3

import pytest

from core.connect import device_access as A
from core.connect import device_tokens as T


class _DB:
    def __init__(self, path):
        self.path = str(path)
        with self._get_connection() as c:
            c.execute("CREATE TABLE profiles (id INTEGER PRIMARY KEY, name TEXT)")
            c.execute("INSERT INTO profiles (id, name) VALUES (1, 'Admin'), (2, 'Mags')")
            T.ensure_tables(c.cursor())
            c.commit()

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


@pytest.fixture
def db(tmp_path):
    T._reset_for_tests()
    return _DB(tmp_path / "d.db")


def test_a_token_is_long_random_and_only_its_hash_is_kept(db):
    token, did = T.create(db, name="Car", now=1000.0)
    assert token.startswith("sdt_") and len(token) > 40
    with db._get_connection() as c:
        dump = "\n".join(str(tuple(r)) for r in c.execute("SELECT * FROM device_tokens"))
    assert token not in dump
    assert T.hash_token(token) in dump
    assert T.find(db, token)["id"] == did
    assert T.find(db, token + "x") is None
    assert T.find(db, "sk_" + token[4:]) is None


def test_a_new_device_is_unprofiled_with_a_15_minute_window(db):
    token, _ = T.create(db, name="Car", now=1000.0)
    row = T.find(db, token)
    assert row["profile_id"] is None
    assert row["scope"] == "playback"
    assert row["pick_deadline"] == 1000.0 + 15 * 60
    assert T.state_of(row, 1000.0 + 15 * 60 - 1) == T.UNPROFILED
    assert T.state_of(row, 1000.0 + 15 * 60) == T.EXPIRED


def test_the_pick_is_once_and_only_inside_the_window(db):
    token, did = T.create(db, name="Car", now=1000.0)
    assert T.pick_profile(db, did, 2, now=1000.0 + 60)
    assert not T.pick_profile(db, did, 1, now=1000.0 + 61)  # no second pick
    row = T.find(db, token)
    assert row["profile_id"] == 2 and row["pick_deadline"] is None
    assert T.state_of(row, 10 ** 10) == T.LIVE  # a picked device has no deadline

    late, late_id = T.create(db, name="Phone", now=1000.0)
    assert not T.pick_profile(db, late_id, 2, now=1000.0 + 15 * 60)
    assert T.find(db, late)["profile_id"] is None


def test_revoked_is_revoked_and_cannot_pick(db):
    token, did = T.create(db, name="Car", now=1000.0)
    assert T.revoke(db, did)
    assert not T.revoke(db, did)
    assert T.state_of(T.find(db, token), 1001.0) == T.REVOKED
    assert not T.pick_profile(db, did, 2, now=1001.0)


def test_revoking_a_profiles_devices_takes_them_all(db):
    a, aid = T.create(db, name="A", now=1000.0)
    b, bid = T.create(db, name="B", now=1000.0)
    c, cid = T.create(db, name="C", now=1000.0)
    T.pick_profile(db, aid, 2, now=1001.0)
    T.pick_profile(db, bid, 2, now=1001.0)
    T.pick_profile(db, cid, 1, now=1001.0)
    assert T.revoke_for_profile(db, 2) == 2
    assert T.state_of(T.find(db, a), 1002.0) == T.REVOKED
    assert T.state_of(T.find(db, b), 1002.0) == T.REVOKED
    assert T.state_of(T.find(db, c), 1002.0) == T.LIVE


def test_the_list_shows_live_and_waiting_never_revoked_expired_or_hashes(db):
    live, lid = T.create(db, name="Live", now=1000.0)
    T.pick_profile(db, lid, 2, now=1001.0)
    waiting, wid = T.create(db, name="Waiting", now=1000.0)
    gone, gid = T.create(db, name="Gone", now=1000.0)
    T.revoke(db, gid)
    T.create(db, name="Old", now=0.0)  # its window passed long ago
    rows = T.list_devices(db, now=1100.0)
    assert [r["name"] for r in rows] == ["Waiting", "Live"]
    views = [T.public_row(r, 1100.0) for r in rows]
    assert views[0]["state"] == T.UNPROFILED and views[0]["pick_seconds_left"] == 15 * 60 - 100
    assert views[1]["state"] == T.LIVE and views[1]["profile_name"] == "Mags"
    assert all("token_hash" not in v for v in views)
    assert T.sweep_expired(db, now=1100.0) == 1


def test_names_are_clipped_and_kind_is_checked(db):
    _, did = T.create(db, name="x" * 500, now=1.0)
    assert len(T.get(db, did)["name"]) == 80
    with pytest.raises(ValueError):
        T.create(db, name="TV", kind="admin")


def test_last_seen_is_written_at_most_once_a_minute(db, monkeypatch):
    _, did = T.create(db, name="Car", now=1.0)
    writes = []
    real = db._get_connection

    def counting():
        conn = real()
        writes.append(1)
        return conn

    monkeypatch.setattr(db, "_get_connection", counting)
    T.touch(db, did, "10.0.0.2", now=100.0)
    T.touch(db, did, "10.0.0.3", now=130.0)
    assert len(writes) == 1
    T.touch(db, did, "10.0.0.4", now=161.0)
    assert len(writes) == 2
    monkeypatch.setattr(db, "_get_connection", real)
    assert T.get(db, did)["last_ip"] == "10.0.0.4"


# ── the gate's decision ──────────────────────────────────────────────────

def _row(**kw):
    base = {"id": 1, "profile_id": 2, "scope": "playback", "revoked_at": None, "pick_deadline": None}
    base.update(kw)
    return base


@pytest.mark.parametrize("row,access,want", [
    (None, A.PLAYBACK, (401, "device_token_invalid")),
    (_row(revoked_at="2026-01-01"), A.PLAYBACK, (401, "device_token_invalid")),
    (_row(revoked_at="2026-01-01"), A.PICK, (401, "device_token_invalid")),
    (_row(profile_id=None, pick_deadline=50.0), A.PICK, (401, "device_pick_expired")),
    (_row(), None, (403, "device_not_allowed")),
    (_row(profile_id=None, pick_deadline=500.0), None, (403, "device_not_allowed")),
    (_row(profile_id=None, pick_deadline=500.0), A.PLAYBACK, (403, "device_pick_first")),
    (_row(scope="admin"), A.PLAYBACK, (403, "device_not_allowed")),
    (_row(profile_id=None, pick_deadline=500.0), A.PICK, (A.OK, None)),
    (_row(), A.PLAYBACK, (A.OK, None)),
    (_row(), A.PICK, (A.OK, None)),
])
def test_decide(row, access, want):
    status, code, _words = A.decide(row, access, 100.0)
    assert (status, code) == want


def test_the_token_rides_in_its_header_or_as_a_bearer_never_an_sk_key():
    assert A.request_token({"X-Device-Token": " sdt_abc "}) == "sdt_abc"
    assert A.request_token({"Authorization": "Bearer sdt_abc"}) == "sdt_abc"
    assert A.request_token({"Authorization": "bearer sdt_abc"}) == "sdt_abc"
    assert A.request_token({"Authorization": "Bearer sk_abc"}) == ""
    assert A.request_token({}) == ""


def test_marks_are_read_through_wrappers():
    import functools

    @A.device_playback
    def view():
        return "x"

    @functools.wraps(view)
    def wrapper():
        return view()

    assert A.device_access(view) == A.PLAYBACK
    assert A.device_access(wrapper) == A.PLAYBACK
    assert A.device_access(lambda: None) is None
