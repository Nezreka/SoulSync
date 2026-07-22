"""Restart-safe Acquisition import coordinator tests."""

import threading
from datetime import datetime, timezone

from core.acquisition.import_pipeline import (
    advance_open_imports,
    is_due,
    retry_backoff_seconds,
)
from core.acquisition.main_pipeline_bridge import BridgeDispatchResult
from core.acquisition.imports import (
    get_import,
    record_pipeline_file_quarantined,
)
from tests.acquisition.test_main_pipeline_bridge import _seed_import


def test_open_import_dispatch_wait_uses_persistent_backoff(tmp_path):
    source_root = tmp_path / "client"
    source_root.mkdir()
    (source_root / "01.flac").write_bytes(b"audio")
    factory, importing, _request = _seed_import(
        tmp_path / "db.sqlite", source_root)
    calls = []

    def dispatcher(_factory, import_id, **_kwargs):
        calls.append(import_id)
        return BridgeDispatchResult(import_id, waiting=("01.flac",))

    now = datetime.now(timezone.utc).timestamp()
    result = advance_open_imports(
        factory,
        dispatcher=dispatcher,
        now=now,
    )

    assert result.outcomes == {importing.id: "waiting_pipeline"}
    assert calls == [importing.id]
    conn = factory()
    waiting = get_import(conn, importing.id)
    assert waiting.status == "importing"
    assert waiting.attempts == 1
    assert "shared pipeline" in (waiting.error or "")
    assert is_due(waiting, now=now) is False
    conn.close()


def test_retry_backoff_is_capped():
    assert retry_backoff_seconds(1) == 60
    assert retry_backoff_seconds(2) == 120
    assert retry_backoff_seconds(100) == 3600


def test_quarantined_import_is_not_blindly_redispatched_after_restart(tmp_path):
    source_root = tmp_path / "client"
    source_root.mkdir()
    (source_root / "01.flac").write_bytes(b"audio")
    factory, importing, _request = _seed_import(
        tmp_path / "db.sqlite", source_root)
    conn = factory()
    record_pipeline_file_quarantined(
        conn,
        importing.id,
        relative_path="01.flac",
        track_id=101,
        trigger="integrity",
        reason="Duration mismatch",
    )
    conn.commit()
    conn.close()
    calls = []

    result = advance_open_imports(
        factory,
        dispatcher=lambda *_args, **_kwargs: calls.append(True),
        now=datetime.now(timezone.utc).timestamp() + 7200,
    )

    assert result.processed == ()
    assert calls == []


def test_parallel_resume_and_monitor_dispatch_import_only_once(tmp_path):
    source_root = tmp_path / "client"
    source_root.mkdir()
    (source_root / "01.flac").write_bytes(b"audio")
    factory, importing, _request = _seed_import(
        tmp_path / "db.sqlite", source_root)
    dispatch_started = threading.Event()
    release_dispatch = threading.Event()
    calls = []
    outcomes = []

    def dispatcher(_factory, import_id, **_kwargs):
        calls.append(import_id)
        dispatch_started.set()
        assert release_dispatch.wait(timeout=5)
        return BridgeDispatchResult(import_id, waiting=("01.flac",))

    def run_advance():
        outcomes.append(advance_open_imports(factory, dispatcher=dispatcher))

    first = threading.Thread(target=run_advance)
    first.start()
    assert dispatch_started.wait(timeout=5)
    second = threading.Thread(target=run_advance)
    second.start()
    second.join(timeout=5)
    release_dispatch.set()
    first.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert calls == [importing.id]
    assert sorted(result.outcomes[importing.id] for result in outcomes) == [
        "already_running",
        "waiting_pipeline",
    ]
