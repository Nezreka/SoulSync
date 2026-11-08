"""the one-time 3.6.1 carries (genre locks, issue ids, artist credits,
household watchlists) map legacy ids onto lib2 rows. the startup convergence
runs before the import loop: on a first upgrade lib2 is still empty there,
and a carry that ran then set its marker having carried nothing, forever."""

from __future__ import annotations

import inspect

from core.library2 import bootstrap
from database.music_database import MusicDatabase


def test_the_carries_wait_until_the_import_is_done(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    calls = []
    monkeypatch.setattr(bootstrap, "_carry_upgrade_state",
                        lambda conn: calls.append(1) or {})
    status = {"status": "running"}
    monkeypatch.setattr(bootstrap, "get_state", lambda _db: dict(status))

    bootstrap.run_deferred_backfills(db)
    assert calls == []

    status["status"] = "done"
    bootstrap.run_deferred_backfills(db)
    assert calls == [1]


def test_the_import_carries_before_it_merges_twins():
    """the twin merge deletes duplicate rows, some holding the legacy ids the
    carries look rows up by"""
    source = inspect.getsource(bootstrap.run_bootstrap_if_needed)
    assert source.index("_carry_upgrade_state(") < source.index("merge_import_twins(")
    assert source.index("mark_done(") < source.index("_carry_upgrade_state(")
