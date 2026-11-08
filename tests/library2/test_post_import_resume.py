"""A restart in the middle of the post-upgrade preparation runs the full pass
again: it used to run only the short credit pass from then on, so the
upgrade's tracklists and tags were never prepared."""

from core.library2 import post_import
from database.music_database import MusicDatabase


def _run(db, monkeypatch, results):
    monkeypatch.setattr(post_import, "run_post_import_precache", lambda *a, **k: results)
    thread = post_import.start_background_preparation(db, None, full=True)
    thread.join(10)


def test_a_full_pass_that_finished_clears_the_marker(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _run(db, monkeypatch, {"artists": "ok", "tracklists": "ok", "tags": "ok"})
    assert post_import.full_preparation_pending(db) is False


def test_a_full_pass_that_did_not_finish_stays_pending(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _run(db, monkeypatch, {"artists": "ok", "tracklists": "provider down", "tags": "ok"})
    assert post_import.full_preparation_pending(db) is True


def test_a_pass_killed_midway_stays_pending(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))

    def killed(*_a, **_k):
        raise RuntimeError("container stopped")

    monkeypatch.setattr(post_import, "run_post_import_precache", killed)
    post_import.start_background_preparation(db, None, full=True).join(10)
    assert post_import.full_preparation_pending(db) is True


def test_a_fresh_install_has_nothing_pending(tmp_path):
    assert post_import.full_preparation_pending(MusicDatabase(str(tmp_path / "m.db"))) is False
