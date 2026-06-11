"""Login-password recovery via security question + answer — DB layer."""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def test_migration_adds_recovery_columns(db):
    with db._get_connection() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()]
    assert 'recovery_question' in cols and 'recovery_answer_hash' in cols


def test_set_get_verify(db):
    pid = db.create_profile(name='RecUser')
    assert db.profile_has_recovery(pid) is False
    assert db.get_profile_recovery_question(pid) is None

    db.set_profile_recovery(pid, 'First pet?', 'Rex')
    assert db.profile_has_recovery(pid) is True
    assert db.get_profile_recovery_question(pid) == 'First pet?'
    assert db.verify_profile_recovery_answer(pid, 'Rex') is True
    assert db.verify_profile_recovery_answer(pid, 'Fido') is False


def test_answer_match_is_forgiving(db):
    pid = db.create_profile(name='Forgiving')
    db.set_profile_recovery(pid, 'City?', '  New   York ')
    assert db.verify_profile_recovery_answer(pid, 'new york') is True       # case + spacing
    assert db.verify_profile_recovery_answer(pid, 'NEW YORK') is True


def test_no_recovery_never_verifies(db):
    pid = db.create_profile(name='NoRec')
    assert db.verify_profile_recovery_answer(pid, '') is False
    assert db.verify_profile_recovery_answer(pid, 'anything') is False


def test_clearing_recovery(db):
    pid = db.create_profile(name='ClearRec')
    db.set_profile_recovery(pid, 'Q?', 'A')
    assert db.profile_has_recovery(pid) is True
    db.set_profile_recovery(pid, '', '')
    assert db.profile_has_recovery(pid) is False
    assert db.get_profile_recovery_question(pid) is None


def test_answer_is_hashed_not_plaintext(db):
    pid = db.create_profile(name='Hashed')
    db.set_profile_recovery(pid, 'Q?', 'secretanswer')
    with db._get_connection() as conn:
        stored = conn.execute("SELECT recovery_answer_hash FROM profiles WHERE id = ?", (pid,)).fetchone()[0]
    assert 'secretanswer' not in stored and stored.startswith('pbkdf2:')
