"""Persistent automatic album-completion cooldown tests."""

import sqlite3

from core.automation.album_completion_cooldown import (
    claim_attempt, is_cooling_down, release_attempt,
)


class DB:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        return sqlite3.connect(self.path)


def test_cooldown_survives_new_database_instance(tmp_path):
    path = tmp_path / "music.db"
    first = DB(path)
    token = claim_attempt(first, 2, "Deezer", "release-1", now=100000)
    assert token
    restarted = DB(path)
    assert claim_attempt(
        restarted, 2, "deezer", "release-1", now=100100
    ) is None
    assert claim_attempt(
        restarted, 2, "deezer", "release-1", now=186401
    )


def test_cooldown_isolated_by_profile_and_release(tmp_path):
    db = DB(tmp_path / "music.db")
    assert claim_attempt(db, 2, "deezer", "release-1", now=100000)
    assert claim_attempt(db, 3, "deezer", "release-1", now=100000)
    assert claim_attempt(db, 2, "deezer", "release-2", now=100000)
    assert claim_attempt(db, 2, "spotify", "release-1", now=100000)


def test_failed_submission_can_release_reservation(tmp_path):
    db = DB(tmp_path / "music.db")
    token = claim_attempt(db, 2, "deezer", "release-1", now=100000)
    assert token
    release_attempt(db, 2, "deezer", "release-1", token)
    assert claim_attempt(db, 2, "deezer", "release-1", now=100001)


def test_stale_token_cannot_release_newer_reservation(tmp_path):
    db = DB(tmp_path / "music.db")
    old = claim_attempt(db, 2, "deezer", "release-1", now=100000)
    new = claim_attempt(db, 2, "deezer", "release-1", now=186401)
    assert new and new != old
    release_attempt(db, 2, "deezer", "release-1", old)
    assert claim_attempt(
        db, 2, "deezer", "release-1", now=186402
    ) is None


def test_peek_matches_the_claim_window(tmp_path):
    db = DB(tmp_path / "music.db")
    # no table yet, nothing claimed
    assert is_cooling_down(db, 2, "deezer", "release-1", now=100000) is False
    assert claim_attempt(db, 2, "Deezer", "release-1", now=100000)
    assert is_cooling_down(db, 2, "DEEZER", "release-1", now=100100) is True
    assert is_cooling_down(db, 3, "deezer", "release-1", now=100100) is False
    assert is_cooling_down(db, 2, "deezer", "release-2", now=100100) is False
    # once the claim would succeed again, the peek must say so too
    assert is_cooling_down(db, 2, "deezer", "release-1", now=186400) is False
    assert claim_attempt(db, 2, "deezer", "release-1", now=186400)
