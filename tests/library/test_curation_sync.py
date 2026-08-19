"""Collecting curation signals off the media servers (Cremonies, phase 2).

The failure policy is the whole test surface here. This data exists to STOP
deletions, so anything that goes wrong must leave the previous state alone
rather than write an empty set — an empty set reads as "nobody likes this",
which is what deletes a library.
"""

from __future__ import annotations

import pytest

from core.library.curation_sync import normalize_signals, sync_curation_signals


class _DB:
    def __init__(self, fail_on=None):
        self.stored = {}
        self.stamped = 0
        self._fail_on = fail_on

    def replace_curation_signals(self, server, user, rows):
        if self._fail_on == (server, user):
            raise RuntimeError("write failed")
        self.stored[(server, user)] = rows
        return len(rows)

    def mark_curation_sync(self):
        self.stamped += 1


class _Client:
    def __init__(self, signals=None, raises=False):
        self._signals = signals
        self._raises = raises
        self.calls = 0

    def get_curation_signals(self):
        self.calls += 1
        if self._raises:
            raise RuntimeError("server down")
        return self._signals


class _ClientWithoutSupport:
    """A server that doesn't implement the optional method at all."""


def _sig(path="/music/Artist/Album/01 Track.flac", **over):
    row = {"path": path, "favorite": False, "rating": None, "in_playlist": False}
    row.update(over)
    return row


# ── normalisation ─────────────────────────────────────────────────────────

def test_paths_become_track_keys():
    out = normalize_signals({"alice": [_sig(favorite=True)]})
    assert out["alice"][0]["track_key"] == "album/01 track.flac"
    assert out["alice"][0]["favorite"] is True
    assert out["alice"][0]["source_path"] == "/music/Artist/Album/01 Track.flac"


def test_rows_without_a_path_are_dropped():
    out = normalize_signals({"alice": [_sig(path=""), _sig(path=None), "junk"]})
    assert out["alice"] == []


def test_a_user_who_likes_nothing_is_still_recorded():
    """Read successfully, chose nothing — distinct from 'could not read', and
    it must clear their old signals."""
    assert normalize_signals({"alice": []}) == {"alice": []}


# ── the happy path ────────────────────────────────────────────────────────

def test_signals_are_stored_per_user_and_the_sync_is_stamped():
    db = _DB()
    summary = sync_curation_signals(db, {
        "navidrome": _Client({"alice": [_sig(favorite=True)],
                              "bob": [_sig(rating=4)]}),
    })
    assert summary["users"] == 2
    assert summary["stamped"] is True
    assert db.stamped == 1
    assert db.stored[("navidrome", "alice")][0]["favorite"] is True


def test_two_servers_both_contribute():
    db = _DB()
    summary = sync_curation_signals(db, {
        "navidrome": _Client({"alice": [_sig(favorite=True)]}),
        "jellyfin": _Client({"carol": [_sig(rating=5)]}),
    })
    assert sorted(summary["servers"]) == ["jellyfin", "navidrome"]
    assert summary["users"] == 2


# ── failures must not withdraw protection ─────────────────────────────────

def test_a_server_that_raises_does_not_stamp_or_clear():
    db = _DB()
    summary = sync_curation_signals(db, {"navidrome": _Client(raises=True)})
    assert db.stored == {}, "a failed read wrote over stored signals"
    assert summary["stamped"] is False
    assert db.stamped == 0


def test_a_server_returning_nothing_does_not_stamp():
    """A sweep that silently returns nothing must not refresh the stamp — a
    fresh-but-empty stamp tells the cleaner 'nobody likes anything'."""
    db = _DB()
    summary = sync_curation_signals(db, {"navidrome": _Client({})})
    assert summary["stamped"] is False
    assert db.stamped == 0


def test_a_client_without_the_method_is_skipped_not_fatal():
    db = _DB()
    summary = sync_curation_signals(db, {
        "plex": _ClientWithoutSupport(),
        "navidrome": _Client({"alice": [_sig(favorite=True)]}),
    })
    assert summary["servers"] == ["navidrome"]
    assert summary["stamped"] is True


def test_a_none_client_is_skipped():
    db = _DB()
    summary = sync_curation_signals(db, {"plex": None})
    assert summary["stamped"] is False


def test_one_users_write_failing_does_not_lose_the_others():
    db = _DB(fail_on=("navidrome", "bob"))
    summary = sync_curation_signals(db, {
        "navidrome": _Client({"alice": [_sig(favorite=True)],
                              "bob": [_sig(favorite=True)]}),
    })
    assert ("navidrome", "alice") in db.stored
    assert ("navidrome", "bob") not in db.stored
    assert summary["users"] == 1
    assert summary["stamped"] is True, "alice was read fine — that is real progress"


def test_every_server_failing_leaves_the_stamp_alone():
    db = _DB()
    summary = sync_curation_signals(db, {
        "navidrome": _Client(raises=True),
        "jellyfin": _Client(raises=True),
    })
    assert summary["servers"] == []
    assert db.stamped == 0


# ── multi-user credentials (phase 3) ──────────────────────────────────────

class _MultiUserClient:
    """Subsonic-shaped: can only report one user's stars per credential."""

    def __init__(self):
        self.called_with = None

    def get_curation_signals(self, users=None):
        self.called_with = users
        return {u: [_sig(favorite=True)] for u, _ in (users or [("configured", "")])}


class _CredDB(_DB):
    def __init__(self, sets=None, payloads=None):
        super().__init__()
        self._sets = sets or []
        self._payloads = payloads or {}

    def list_service_credentials(self, service=None):
        return list(self._sets)

    def get_service_credential(self, credential_id):
        return self._payloads.get(credential_id)


def test_each_saved_account_is_read_separately():
    from core.library.curation_sync import navidrome_user_credentials

    db = _CredDB(
        sets=[{"id": 1}, {"id": 2}],
        payloads={
            1: {"payload": {"username": "alice", "password": "a"}},
            2: {"payload": {"username": "bob", "password": "b"}},
        },
    )
    assert navidrome_user_credentials(db) == [("alice", "a"), ("bob", "b")]


def test_incomplete_credentials_are_skipped():
    from core.library.curation_sync import navidrome_user_credentials

    db = _CredDB(
        sets=[{"id": 1}, {"id": 2}],
        payloads={
            1: {"payload": {"username": "alice"}},          # no password
            2: {"payload": {"username": "bob", "password": "b"}},
        },
    )
    assert navidrome_user_credentials(db) == [("bob", "b")]


def test_credentials_are_passed_per_call():
    db = _DB()
    client = _MultiUserClient()
    summary = sync_curation_signals(
        db, {"navidrome": client},
        user_credentials={"navidrome": [("alice", "a"), ("bob", "b")]})
    assert client.called_with == [("alice", "a"), ("bob", "b")]
    assert summary["users"] == 2


def test_no_credentials_falls_back_to_the_configured_account():
    db = _DB()
    client = _MultiUserClient()
    sync_curation_signals(db, {"navidrome": client})
    assert client.called_with is None
    assert ("navidrome", "configured") in db.stored


def test_a_client_that_takes_no_credentials_still_works():
    """Jellyfin reads every user with one admin key, so it has no users kwarg."""
    db = _DB()
    summary = sync_curation_signals(
        db, {"jellyfin": _Client({"carol": [_sig(favorite=True)]})},
        user_credentials={"jellyfin": [("ignored", "x")]})
    assert summary["users"] == 1
