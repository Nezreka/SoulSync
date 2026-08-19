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


def test_unusable_rows_are_dropped_but_usable_ones_survive():
    """Junk rows are skipped individually. (A user whose rows ALL drop out is
    a different case — see test_rows_with_no_usable_path_at_all_omit_the_user,
    which treats it as a failed read rather than 'likes nothing'.)"""
    out = normalize_signals({"alice": [_sig(path=""), "junk", _sig(favorite=True)]})
    assert len(out["alice"]) == 1
    assert out["alice"][0]["favorite"] is True


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


# ── the sweep is OPTIONAL: it must not run for people who never enabled it ──

class _Cfg:
    def __init__(self, master=True, enabled=True, settings=None, raises=False):
        self._raises = raises
        self._values = {
            'repair.master_enabled': master,
            'repair.jobs.expired_download_cleaner': {
                'enabled': enabled,
                'settings': settings if settings is not None else {},
            },
        }

    def get(self, key, default=None):
        if self._raises:
            raise RuntimeError("config unreadable")
        return self._values.get(key, default)


class _StampDB:
    def __init__(self, synced_at=None, raises=False):
        self._synced_at = synced_at
        self._raises = raises

    def get_curation_sync_at(self):
        if self._raises:
            raise RuntimeError("db down")
        return self._synced_at


def test_no_sweep_when_the_repair_system_is_off():
    from core.library.curation_sync import curation_sweep_due

    assert curation_sweep_due(_Cfg(master=False), _StampDB()) is False


def test_no_sweep_when_the_cleaner_job_is_disabled():
    """The default state on every install — nobody should pay for this."""
    from core.library.curation_sync import curation_sweep_due

    assert curation_sweep_due(_Cfg(enabled=False), _StampDB()) is False


def test_no_sweep_when_curation_is_switched_off_in_the_job():
    from core.library.curation_sync import curation_sweep_due

    cfg = _Cfg(settings={'use_curation_signals': False})
    assert curation_sweep_due(cfg, _StampDB()) is False


def test_no_config_means_no_sweep():
    from core.library.curation_sync import curation_sweep_due

    assert curation_sweep_due(None, _StampDB()) is False


def test_unreadable_config_means_no_sweep():
    """Fail toward doing nothing: an unreadable config must not start
    scanning every user's library on a hunch."""
    from core.library.curation_sync import curation_sweep_due

    assert curation_sweep_due(_Cfg(raises=True), _StampDB()) is False


def test_sweeps_when_enabled_and_never_swept():
    from core.library.curation_sync import curation_sweep_due

    assert curation_sweep_due(_Cfg(), _StampDB(synced_at=None)) is True


def test_does_not_resweep_while_the_signals_are_fresh():
    """Retention is measured in weeks; re-scanning every 30 minutes is pure
    cost."""
    from datetime import datetime, timedelta, timezone

    from core.library.curation_sync import curation_sweep_due

    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert curation_sweep_due(_Cfg(), _StampDB(synced_at=recent)) is False


def test_resweeps_once_the_signals_age():
    from datetime import datetime, timedelta, timezone

    from core.library.curation_sync import curation_sweep_due

    old = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    assert curation_sweep_due(_Cfg(), _StampDB(synced_at=old)) is True


# ── a read that yields nothing usable is a FAILURE, not "likes nothing" ────

def test_rows_with_no_usable_path_at_all_omit_the_user():
    """Jellyfin can omit Path when the key lacks rights. Storing that as an
    empty set would withdraw protection from everything they starred."""
    out = normalize_signals({"alice": [_sig(path=""), _sig(path=None)]})
    assert "alice" not in out


def test_a_partial_parse_still_stores_what_worked():
    out = normalize_signals({"alice": [_sig(path=""), _sig(favorite=True)]})
    assert len(out["alice"]) == 1


def test_a_genuinely_empty_user_is_still_cleared():
    """Read fine, chose nothing — their old signals MUST be cleared, or an
    unstarred track stays protected forever."""
    assert normalize_signals({"alice": []}) == {"alice": []}


# ── credential passing is decided by signature, not by catching TypeError ──

class _BuggyMultiUserClient:
    def get_curation_signals(self, users=None):
        raise TypeError("a real bug inside the client")


def test_a_typeerror_inside_the_client_is_not_mistaken_for_no_support():
    """Catching TypeError would silently retry without credentials, hiding
    the bug and reading the wrong user's data."""
    db = _DB()
    summary = sync_curation_signals(
        db, {"navidrome": _BuggyMultiUserClient()},
        user_credentials={"navidrome": [("alice", "a")]})
    assert db.stored == {}
    assert summary["stamped"] is False


def test_the_gate_defaults_match_the_repair_worker():
    """master_enabled defaults ON in core/repair_worker.py:366. Defaulting it
    OFF here would leave the sweep silently never running on installs that
    simply have no such key, while the job itself happily ran."""
    from core.library.curation_sync import curation_sweep_due

    class _NoMasterKey(_Cfg):
        def get(self, key, default=None):
            if key == 'repair.master_enabled':
                return default          # key absent — caller's default wins
            return super().get(key, default)

    assert curation_sweep_due(_NoMasterKey(), _StampDB()) is True


def test_a_malformed_job_config_does_not_sweep():
    from core.library.curation_sync import curation_sweep_due

    class _Junk(_Cfg):
        def get(self, key, default=None):
            if key == 'repair.jobs.expired_download_cleaner':
                return "not a dict"
            return super().get(key, default)

    assert curation_sweep_due(_Junk(), _StampDB()) is False


def test_malformed_settings_fall_back_to_defaults():
    from core.library.curation_sync import curation_sweep_due

    cfg = _Cfg(settings=None)
    cfg._values['repair.jobs.expired_download_cleaner']['settings'] = "junk"
    assert curation_sweep_due(cfg, _StampDB()) is True
