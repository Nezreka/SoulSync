"""A wishlist row must never be left saying "sent to downloads" forever.

Reported: lots of audiobook wishlist rows showing "sent to downloads" with
nothing actually downloading.

"grabbed" is a handover, not an end state. The row goes there the moment a
download client accepts the release, and it is the download MONITOR that moves
it off — to done when the book imports, to failed when the client gives up. The
monitor only looks at rows with a LIVE download, so if that download never
registered, was cleared, or the monitor stopped running, the row sits on
"grabbed" for good: the retry query takes only "wanted" and "failed", so nothing
ever looks at it again and nothing says so.

This is the same class reset_stale_searching already solved for the state before
it, and it gets the same treatment.
"""

from __future__ import annotations

import pytest

from core.audiobook_database import (
    STATUS_FAILED,
    STATUS_GRABBED,
    STATUS_WANTED,
    AudiobookDatabase,
)


@pytest.fixture
def db(tmp_path):
    return AudiobookDatabase(str(tmp_path / 'audiobooks.db'))


def _wanted(db, asin='B001', title='A Book'):
    db.add_to_wishlist({'asin': asin, 'title': title, 'authors': ['An Author']})
    return asin


def _age(db, asin, seconds):
    """Push when the row last changed state into the past.

    status_changed_at, not last_attempt_at: the latter only moves when
    count_attempt is passed, and a manual grab deliberately does not count as an
    attempt — so gating on it freed a grab that had just happened.
    """
    conn = db._connect()
    conn.execute(
        "UPDATE audiobook_wishlist SET status_changed_at = ? WHERE asin = ?",
        (__import__('time').time() - seconds, asin),
    )
    conn.commit()


def _status(db, asin):
    for row in db.get_wishlist():
        if row['asin'] == asin:
            return row['status']
    return None


def test_a_grabbed_row_with_no_download_is_freed(db):
    asin = _wanted(db)
    db.mark_wishlist_status(asin, STATUS_GRABBED)
    _age(db, asin, 60 * 60 * 24)

    assert db.reset_stale_grabbed() == 1
    assert _status(db, asin) == STATUS_WANTED


def test_the_freed_row_says_why(db):
    asin = _wanted(db)
    db.mark_wishlist_status(asin, STATUS_GRABBED)
    _age(db, asin, 60 * 60 * 24)
    db.reset_stale_grabbed()

    row = next(r for r in db.get_wishlist() if r['asin'] == asin)
    assert 'nothing was tracking it' in (row.get('last_error') or '')


def test_a_book_still_downloading_is_left_alone(db):
    """A large audiobook on a thin swarm is normal, not stuck."""
    asin = _wanted(db)
    db.mark_wishlist_status(asin, STATUS_GRABBED)
    db.record_download(download_id='d1', asin=asin, title='A Book', source='torrent')
    _age(db, asin, 60 * 60 * 24 * 7)

    assert db.reset_stale_grabbed() == 0
    assert _status(db, asin) == STATUS_GRABBED


def test_a_finished_download_does_not_protect_the_row(db):
    """A completed or failed download is not something still tracking it."""
    asin = _wanted(db)
    db.mark_wishlist_status(asin, STATUS_GRABBED)
    db.record_download(download_id='d1', asin=asin, title='A Book', source='torrent')
    db.update_download('d1', status='failed', error='client gave up')
    _age(db, asin, 60 * 60 * 24)

    assert db.reset_stale_grabbed() == 1
    assert _status(db, asin) == STATUS_WANTED


def test_a_recent_grab_is_not_touched(db):
    """The age gate stops a pass freeing a grab that just happened."""
    asin = _wanted(db)
    db.mark_wishlist_status(asin, STATUS_GRABBED)

    assert db.reset_stale_grabbed() == 0
    assert _status(db, asin) == STATUS_GRABBED


def test_other_states_are_untouched(db):
    for asin, status in (('B1', STATUS_WANTED), ('B2', STATUS_FAILED)):
        _wanted(db, asin, asin)
        db.mark_wishlist_status(asin, status)
        _age(db, asin, 60 * 60 * 24)

    assert db.reset_stale_grabbed() == 0


def test_the_freed_row_is_picked_up_again(db):
    """The point of freeing it: the retry query only takes wanted and failed."""
    asin = _wanted(db)
    db.mark_wishlist_status(asin, STATUS_GRABBED)
    _age(db, asin, 60 * 60 * 24)
    assert db.get_wishlist_due(retry_after_seconds=0) == []

    db.reset_stale_grabbed()
    due = [r['asin'] for r in db.get_wishlist_due(retry_after_seconds=0)]
    assert asin in due


def test_a_manual_grab_is_not_freed_immediately(db):
    """A manual grab does not count as an attempt, so last_attempt_at stays
    where it was — gating the sweep on it freed the row on the very next pass.
    status_changed_at moves on every status change, including this one."""
    asin = _wanted(db)
    db.mark_wishlist_status(asin, STATUS_GRABBED)     # no count_attempt
    assert db.reset_stale_grabbed() == 0
    assert _status(db, asin) == STATUS_GRABBED


def test_a_row_that_never_recorded_a_change_is_left_alone(db):
    """Rows written before the column existed default to 0. Freeing those on
    sight would reset every grabbed row the first time this ran."""
    asin = _wanted(db)
    db.mark_wishlist_status(asin, STATUS_GRABBED)
    conn = db._connect()
    conn.execute("UPDATE audiobook_wishlist SET status_changed_at = 0 WHERE asin = ?", (asin,))
    conn.commit()

    assert db.reset_stale_grabbed() == 0
