"""H11: is_track_missing_from_library must fail CLOSED on infrastructure
errors — a transient DB error must not report an owned track as missing
(which wishlists it and re-downloads it next cycle)."""
import sqlite3
from types import SimpleNamespace

from core.watchlist_scanner import WatchlistScanner


class _LockedDB:
    """The track IS in the library — but the DB throws a transient error."""

    def check_track_exists(self, *a, **kwargs):
        raise sqlite3.OperationalError("database is locked")


class _EmptyDB:
    """Healthy DB, track genuinely absent."""

    def check_track_exists(self, *a, **kwargs):
        return None, 0.0


class _HitDB:
    """Healthy DB, track genuinely present."""

    def check_track_exists(self, *a, **kwargs):
        return SimpleNamespace(file_path="/music/Real Artist/Owned Song.flac",
                               album_title="Real Album"), 1.0


def _scanner(db):
    scanner = WatchlistScanner.__new__(WatchlistScanner)
    scanner._database = db
    scanner._matching_engine = SimpleNamespace(clean_title=lambda t: t)
    return scanner


def _track():
    return {"name": "Owned Song", "artists": [{"name": "Real Artist"}]}


def test_h11_db_error_fails_closed_not_missing():
    """A transient DB error must NOT report the track as missing."""
    scanner = _scanner(_LockedDB())
    assert scanner.is_track_missing_from_library(_track(), album_name="Real Album") is False


def test_h11_genuine_miss_still_reports_missing():
    """Control: a healthy DB with no match still reports missing (True)."""
    scanner = _scanner(_EmptyDB())
    assert scanner.is_track_missing_from_library(_track(), album_name="Real Album") is True


def test_h11_genuine_hit_still_reports_present():
    """Control: a healthy DB with a match still reports present (False)."""
    scanner = _scanner(_HitDB())
    assert scanner.is_track_missing_from_library(_track(), album_name="Real Album") is False
