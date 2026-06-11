"""Per-profile LOGIN password (opt-in username/password mode) — DB layer.

Separate from the quick-switch PIN: a profile with no password set is NOT
loginable (you can't authenticate to an account with no credential), unlike the
PIN where 'no PIN = always valid'.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def test_migration_adds_password_hash_column(db):
    with db._get_connection() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()]
    assert 'password_hash' in cols


def test_set_and_verify_password(db):
    pid = db.create_profile(name='Brock')
    assert db.profile_has_password(pid) is False        # none yet
    assert db.verify_profile_password(pid, 'hunter2') is False  # no password → not loginable

    db.set_profile_password(pid, 'hunter2')
    assert db.profile_has_password(pid) is True
    assert db.verify_profile_password(pid, 'hunter2') is True
    assert db.verify_profile_password(pid, 'wrong') is False


def test_no_password_is_never_loginable(db):
    # Unlike the PIN (no PIN = always valid), a passwordless account can't log in.
    pid = db.create_profile(name='NoPass')
    assert db.verify_profile_password(pid, '') is False
    assert db.verify_profile_password(pid, 'anything') is False


def test_clearing_password(db):
    pid = db.create_profile(name='Temp')
    db.set_profile_password(pid, 'pw')
    assert db.profile_has_password(pid) is True
    db.set_profile_password(pid, '')                    # clear
    assert db.profile_has_password(pid) is False
    assert db.verify_profile_password(pid, 'pw') is False


def test_get_profile_by_name_case_insensitive(db):
    pid = db.create_profile(name='Daughter')
    assert db.get_profile_by_name('daughter')['id'] == pid
    assert db.get_profile_by_name('DAUGHTER')['id'] == pid
    assert db.get_profile_by_name('nobody') is None


def test_password_is_independent_of_pin(db):
    # Setting a password must not touch the PIN and vice-versa (separate creds).
    from werkzeug.security import generate_password_hash
    pid = db.create_profile(name='Both', pin_hash=generate_password_hash('1234', method='pbkdf2:sha256'))
    db.set_profile_password(pid, 'longpassword')
    assert db.verify_profile_pin(pid, '1234') is True            # PIN still works
    assert db.verify_profile_password(pid, 'longpassword') is True  # password works
    assert db.verify_profile_password(pid, '1234') is False        # PIN is NOT the password
