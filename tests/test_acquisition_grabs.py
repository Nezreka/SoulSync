"""Persistent grab correlation (audit ADR-07 / P1-20 / P1-21).

The external client is the live queue; SoulSync persists only the business
correlation (job id, status transitions, output path). A restart re-attaches
to running jobs instead of losing them, and terminal statuses never get
overwritten by late poll threads.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from core.acquisition.grabs import (
    STATUS_CANCELLED,
    STATUS_CANCEL_PENDING,
    STATUS_COMPLETED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_QUEUED,
    ensure_acquisition_grabs_schema,
    get_grab,
    open_grabs,
    record_grab,
    update_grab,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_acquisition_grabs_schema(c)
    yield c
    c.close()


def test_record_and_get_roundtrip_with_context(conn):
    record_grab(conn, "dl-1", "usenet", title="Artist - Album",
                context={"flow": "track", "file_size": 42})
    grab = get_grab(conn, "dl-1")
    assert grab["status"] == "submitting"
    assert grab["source"] == "usenet"
    assert grab["context"] == {"flow": "track", "file_size": 42}
    assert grab["adopted"] == 0


def test_record_is_idempotent_per_download_id(conn):
    record_grab(conn, "dl-1", "usenet", title="first")
    update_grab(conn, "dl-1", status=STATUS_QUEUED, external_job_id="nzo1")
    record_grab(conn, "dl-1", "usenet", title="second")
    grab = get_grab(conn, "dl-1")
    assert grab["title"] == "first"
    assert grab["status"] == STATUS_QUEUED


def test_business_transitions_persist(conn):
    record_grab(conn, "dl-1", "usenet")
    update_grab(conn, "dl-1", status=STATUS_QUEUED, external_job_id="nzo1",
                client="SABnzbdAdapter")
    update_grab(conn, "dl-1", status=STATUS_DOWNLOADING,
                last_client_state="downloading")
    update_grab(conn, "dl-1", status=STATUS_COMPLETED, output_path="/done/album")
    grab = get_grab(conn, "dl-1")
    assert grab["status"] == STATUS_COMPLETED
    assert grab["external_job_id"] == "nzo1"
    assert grab["client"] == "SABnzbdAdapter"
    assert grab["output_path"] == "/done/album"


def test_terminal_status_is_never_overwritten(conn):
    """P1-21: a late poll thread seeing the removed job must not flip a
    user's cancelled grab into failed."""
    record_grab(conn, "dl-1", "usenet")
    update_grab(conn, "dl-1", status=STATUS_CANCELLED)
    update_grab(conn, "dl-1", status=STATUS_FAILED, error="job disappeared")
    grab = get_grab(conn, "dl-1")
    assert grab["status"] == STATUS_CANCELLED
    # Non-status enrichment still lands (the error text is kept for audit).
    assert grab["error"] == "job disappeared"


def test_open_grabs_returns_only_reconcilable_rows(conn):
    for i, status in enumerate(("submitting", STATUS_QUEUED, STATUS_DOWNLOADING,
                                STATUS_CANCEL_PENDING, STATUS_COMPLETED,
                                STATUS_FAILED, STATUS_CANCELLED)):
        record_grab(conn, f"dl-{i}", "usenet")
        if status != "submitting":
            update_grab(conn, f"dl-{i}", status=status)
    record_grab(conn, "dl-torrent", "torrent")
    ids = [g["download_id"] for g in open_grabs(conn, "usenet")]
    assert ids == ["dl-0", "dl-1", "dl-2", "dl-3"]


# ---------------------------------------------------------------------------
# Plugin wiring: the usenet plugin persists transitions and re-attaches
# to client jobs after a restart.
# ---------------------------------------------------------------------------


@pytest.fixture
def grab_db(tmp_path):
    """A real on-disk grab store + a _grabs_conn patch for the plugin
    (fresh connection per call, like the production helper)."""
    path = str(tmp_path / "grabs.db")
    seed = sqlite3.connect(path)
    ensure_acquisition_grabs_schema(seed)
    seed.commit()
    seed.close()

    def _connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    with patch("core.download_plugins.usenet._grabs_conn", side_effect=_connect):
        yield _connect


def test_plugin_persists_full_lifecycle(grab_db, tmp_path):
    from core.download_plugins.usenet import UsenetDownloadPlugin

    audio = tmp_path / "album" / "01 song.flac"
    audio.parent.mkdir()
    audio.write_bytes(b"x")

    plugin = UsenetDownloadPlugin()
    plugin.active_downloads["dl-1"] = {
        'id': "dl-1", 'filename': "t||Artist - Album", 'username': 'usenet',
        'display_name': 'Artist - Album', 'state': 'Initializing',
        'progress': 0.0, 'size': 0, 'transferred': 0, 'speed': 0,
        'file_path': None, 'audio_files': [], 'job_id': None, 'error': None,
    }
    plugin._record_grab("dl-1", "Artist - Album", {"flow": "track"})
    plugin._update_grab("dl-1", status=STATUS_QUEUED, external_job_id="nzo1",
                        client="SABnzbdAdapter")
    with patch("core.download_plugins.usenet.resolve_reported_save_path",
               side_effect=lambda p: p):
        plugin._finalize_download("dl-1", str(audio.parent))

    conn = grab_db()
    grab = get_grab(conn, "dl-1")
    conn.close()
    assert grab["status"] == STATUS_COMPLETED
    assert grab["external_job_id"] == "nzo1"
    assert grab["output_path"] == str(audio.parent)


def test_plugin_marks_failed_grab(grab_db):
    from core.download_plugins.usenet import UsenetDownloadPlugin

    plugin = UsenetDownloadPlugin()
    plugin._record_grab("dl-2", "Broken", {"flow": "track"})
    plugin._mark_error("dl-2", "Usenet client refused the NZB")
    conn = grab_db()
    grab = get_grab(conn, "dl-2")
    conn.close()
    assert grab["status"] == STATUS_FAILED
    assert "refused" in grab["error"]


def test_restart_adopts_open_client_jobs(grab_db):
    """P1-20: after a restart, grabs with a client job id get their
    in-memory row back and a poll thread re-attached — the client kept
    downloading the whole time."""
    from core.download_plugins.usenet import UsenetDownloadPlugin

    conn = grab_db()
    record_grab(conn, "dl-run", "usenet", title="Running Album",
                context={"flow": "track"})
    update_grab(conn, "dl-run", status=STATUS_DOWNLOADING,
                external_job_id="nzo-run")
    record_grab(conn, "dl-lost", "usenet", title="Never Submitted",
                context={"flow": "track"})
    record_grab(conn, "dl-unknown", "usenet", title="Maybe Submitted",
                context={"flow": "track"})
    update_grab(conn, "dl-unknown", last_client_state="submission_unknown")
    record_grab(conn, "dl-bundle", "usenet", title="Bundle",
                context={"flow": "album_bundle"})
    update_grab(conn, "dl-bundle", status=STATUS_DOWNLOADING,
                external_job_id="nzo-bundle")
    conn.commit()
    conn.close()

    plugin = UsenetDownloadPlugin()
    with patch("core.download_plugins.usenet.threading.Thread") as thread_cls:
        plugin._restore_grabs_once()

    # The running job was adopted: row recreated, poll thread scheduled.
    assert "dl-run" in plugin.active_downloads
    assert plugin.active_downloads["dl-run"]["job_id"] == "nzo-run"
    started = [c.kwargs["args"] for c in thread_cls.call_args_list]
    assert started == [("dl-run", "nzo-run")]
    assert all(c.kwargs["target"] == plugin._poll_job
               for c in thread_cls.call_args_list)

    conn = grab_db()
    adopted = get_grab(conn, "dl-run")
    lost = get_grab(conn, "dl-lost")
    unknown = get_grab(conn, "dl-unknown")
    bundle = get_grab(conn, "dl-bundle")
    conn.close()
    assert adopted["adopted"] == 1
    # submitting rows never reached the client — their NZB URL died with
    # the process; they fail visibly instead of hanging forever.
    assert lost["status"] == STATUS_FAILED
    # A timed-out add call may have been accepted remotely. Keep it open;
    # retrying would risk a duplicate until Category adoption resolves it.
    assert unknown["status"] == "submitting"
    assert unknown["last_client_state"] == "submission_unknown"
    # Bundle grabs keep their history row but are not adopted (their
    # synchronous worker is gone; Phase-5 client monitor takes over).
    assert bundle["adopted"] == 0
    assert "dl-bundle" not in plugin.active_downloads


def test_restore_runs_only_once(grab_db):
    from core.download_plugins.usenet import UsenetDownloadPlugin

    plugin = UsenetDownloadPlugin()
    plugin._restore_grabs_once()
    conn = grab_db()
    record_grab(conn, "dl-late", "usenet", context={"flow": "track"})
    update_grab(conn, "dl-late", status=STATUS_DOWNLOADING,
                external_job_id="nzo-late")
    conn.commit()
    conn.close()
    plugin._restore_grabs_once()
    assert "dl-late" not in plugin.active_downloads


def test_plugin_restore_leaves_request_bound_grabs_to_central_monitor(grab_db):
    from core.download_plugins.usenet import UsenetDownloadPlugin

    conn = grab_db()
    record_grab(
        conn,
        "dl-central",
        "usenet",
        acquisition_request_id="request-central",
        context={"flow": "track"},
    )
    update_grab(
        conn,
        "dl-central",
        status=STATUS_DOWNLOADING,
        external_job_id="nzo-central",
    )
    conn.commit()
    conn.close()

    plugin = UsenetDownloadPlugin()
    with patch("core.download_plugins.usenet.threading.Thread") as thread_cls:
        plugin._restore_grabs_once()

    assert "dl-central" not in plugin.active_downloads
    thread_cls.assert_not_called()


def test_cancel_persists_two_step_state_machine(grab_db):
    """Cancel intent lands as cancel_pending before the client remove and
    becomes cancelled only after it succeeded (P1-21)."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from core.download_plugins.usenet import UsenetDownloadPlugin

    plugin = UsenetDownloadPlugin()
    plugin._grabs_restored = True
    plugin.active_downloads["dl-c"] = {
        'id': "dl-c", 'filename': 'x', 'username': 'usenet',
        'display_name': 'x', 'state': 'InProgress, Downloading',
        'progress': 0.0, 'size': 0, 'transferred': 0, 'speed': 0,
        'file_path': None, 'audio_files': [], 'job_id': 'nzo-c', 'error': None,
    }
    plugin._record_grab("dl-c", "x", {"flow": "track"})
    plugin._update_grab("dl-c", status=STATUS_DOWNLOADING,
                        external_job_id="nzo-c")

    adapter = MagicMock()
    adapter.remove = AsyncMock(return_value=True)
    with patch("core.download_plugins.usenet.get_active_usenet_adapter",
               return_value=adapter):
        asyncio.new_event_loop().run_until_complete(
            plugin.cancel_download("dl-c"))
    conn = grab_db()
    grab = get_grab(conn, "dl-c")
    conn.close()
    assert grab["status"] == STATUS_CANCELLED

    # A failing client remove leaves the intent visible as cancel_pending
    # (fresh grab — 'cancelled' above is terminal by design).
    plugin.active_downloads["dl-c2"] = dict(
        plugin.active_downloads["dl-c"], id="dl-c2", job_id="nzo-c2")
    plugin._record_grab("dl-c2", "x", {"flow": "track"})
    plugin._update_grab("dl-c2", status=STATUS_DOWNLOADING,
                        external_job_id="nzo-c2")
    adapter.remove = AsyncMock(side_effect=RuntimeError("client down"))
    with patch("core.download_plugins.usenet.get_active_usenet_adapter",
               return_value=adapter):
        asyncio.new_event_loop().run_until_complete(
            plugin.cancel_download("dl-c2"))
    conn = grab_db()
    grab = get_grab(conn, "dl-c2")
    conn.close()
    assert grab["status"] == STATUS_CANCEL_PENDING
