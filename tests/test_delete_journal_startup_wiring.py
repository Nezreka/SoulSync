"""The delete journal's crash recovery has to RUN.

`_execute_delete_items` persists `status='deleting'` immediately before the
unlink, and says why: "a process that dies mid-run leaves the item in a state
`reconcile_incomplete_deletes` can settle, instead of a file that is gone with
nothing saying so."

That recovery was only ever called when a NEW delete was started, which on most
installs is never. So a container restart during a delete (a Docker update, an
OOM kill, a host reboot) left the item `deleting` forever, the parent operation
`executing` with `completed_at` NULL forever, and the catalogue asserting a file
was live that may already have been unlinked — the exact divergence the
pre-unlink journal write exists to make recoverable (bug-audit BUG-14).

It now runs at startup, in the same block as the analogous
`lib2_bootstrap.reclaim_abandoned_claim`.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def autostart(monkeypatch):
    """`_autostart_library_v2_bootstrap_import` with its retry loop retired.

    Everything before the loop — the claim reclaim, the delete-journal recovery,
    the convergence passes — still runs for real against the stubs.
    """
    web_server = pytest.importorskip("web_server")

    from core.library2 import bootstrap as lib2_bootstrap

    monkeypatch.setattr(lib2_bootstrap, "reclaim_abandoned_claim",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr(lib2_bootstrap, "run_deferred_backfills",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr(lib2_bootstrap, "run_bootstrap_if_needed",
                        lambda *a, **k: {"retired": True}, raising=False)
    monkeypatch.setattr(lib2_bootstrap, "should_stop_autostart",
                        lambda _r: True, raising=False)
    monkeypatch.setattr("core.library2.migration_gate.start_deferred_workers",
                        lambda *a, **k: None, raising=False)
    return web_server


def test_startup_settles_an_interrupted_delete(autostart, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "core.library2.file_delete.reconcile_incomplete_deletes",
        lambda database: calls.append(database) or 3,
    )

    autostart._autostart_library_v2_bootstrap_import()

    assert len(calls) == 1, "the delete journal was never reconciled at startup"


def test_a_failing_recovery_does_not_stop_the_migration(autostart, monkeypatch):
    """Best-effort, like every other step in this block — a broken journal must
    not prevent an upgrading install from migrating."""
    started = []
    monkeypatch.setattr(
        "core.library2.file_delete.reconcile_incomplete_deletes",
        lambda _database: (_ for _ in ()).throw(RuntimeError("journal unreadable")),
    )
    monkeypatch.setattr("core.library2.migration_gate.start_deferred_workers",
                        lambda *a, **k: started.append(1), raising=False)

    autostart._autostart_library_v2_bootstrap_import()

    assert started == [1], "the bootstrap loop must still have been reached"
