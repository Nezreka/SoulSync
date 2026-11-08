"""Only a run that saw the whole library may prune a remembered-answer memory."""
from types import SimpleNamespace

from core.repair_jobs.base import whole_library_run


def test_an_unscoped_run_on_one_library_install_is_the_whole_library(monkeypatch):
    monkeypatch.setattr("core.library2.sql_util.owner_clause", lambda *a, **k: "")
    assert whole_library_run(SimpleNamespace(scope=None)) is True


def test_an_artist_run_is_not(monkeypatch):
    monkeypatch.setattr("core.library2.sql_util.owner_clause", lambda *a, **k: "")
    assert whole_library_run(SimpleNamespace(scope={"file_paths": ["/m/a.flac"]})) is False


def test_a_run_in_one_of_several_libraries_is_not(monkeypatch):
    monkeypatch.setattr("core.library2.sql_util.owner_clause",
                        lambda *a, **k: " AND +f.owner_profile_id IS NULL")
    assert whole_library_run(SimpleNamespace(scope={"library": "shared"})) is False
