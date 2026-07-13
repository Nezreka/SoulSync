"""Opt-in real-client acceptance for the Phase-5 restart boundary.

Run ``prepare`` and ``verify`` in separate processes (or containers) with the
same state database and download bind mount.  The first phase deliberately
persists ``submission_unknown`` without the external job id.  The second phase
constructs the production monitor anew and proves that it adopts the real
SABnzbd/NZBGet job by category/title after restart.

No Usenet provider is required: the synthetic NZB is paused immediately and
removed during verification.  Secrets are supplied only via environment and
are never persisted in the acceptance database.
"""

from __future__ import annotations

import os
import sqlite3
from itertools import count
from pathlib import Path
from uuid import uuid4

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.candidates import register_candidate
from core.acquisition.client_monitor import UsenetAcquisitionMonitor
from core.acquisition.grabs import get_grab, open_grabs, record_grab, update_grab
from core.acquisition.path_health import (
    inspect_mapping_configuration,
    inspect_reported_path,
)
from core.acquisition.requests import create_request, transition_request
from core.usenet_clients.nzbget import NZBGetAdapter
from core.usenet_clients.sabnzbd import SABnzbdAdapter
from utils.async_helpers import run_async


pytestmark = [
    pytest.mark.phase5_deployment,
    pytest.mark.skipif(
        os.environ.get("SOULSYNC_PHASE5_ACCEPTANCE") != "1",
        reason="set SOULSYNC_PHASE5_ACCEPTANCE=1 for real-client acceptance",
    ),
]


_NZB = b"""<?xml version="1.0" encoding="UTF-8"?>
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
  <head><meta type="category">soulsync</meta></head>
  <file poster="acceptance" date="0" subject="SoulSync Phase5 Acceptance">
    <groups><group>alt.binaries.test</group></groups>
    <segments><segment bytes="1" number="1">soulsync-phase5-acceptance@test.invalid</segment></segments>
  </file>
</nzb>
"""


def _required(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        pytest.fail(f"{name} is required for Phase-5 deployment acceptance")
    return value


def _phase() -> str:
    phase = _required("SOULSYNC_PHASE5_PHASE").lower()
    if phase not in {"prepare", "verify"}:
        pytest.fail("SOULSYNC_PHASE5_PHASE must be prepare or verify")
    return phase


def _adapter():
    client_type = _required("SOULSYNC_PHASE5_CLIENT").lower()
    url = _required("SOULSYNC_PHASE5_URL").rstrip("/")
    if client_type == "sabnzbd":
        adapter = SABnzbdAdapter.__new__(SABnzbdAdapter)
        adapter._url = url
        adapter._api_key = _required("SOULSYNC_PHASE5_SAB_API_KEY")
        adapter._category = _category()
        return adapter
    if client_type == "nzbget":
        adapter = NZBGetAdapter.__new__(NZBGetAdapter)
        adapter._id_counter = count(1)
        adapter._url = url
        adapter._username = _required("SOULSYNC_PHASE5_NZBGET_USERNAME")
        adapter._password = _required("SOULSYNC_PHASE5_NZBGET_PASSWORD")
        adapter._category = _category()
        return adapter
    pytest.fail("SOULSYNC_PHASE5_CLIENT must be sabnzbd or nzbget")


def _category() -> str:
    return str(os.environ.get("SOULSYNC_PHASE5_CATEGORY") or "soulsync").strip()


def _database_path() -> Path:
    path = Path(_required("SOULSYNC_PHASE5_STATE_DB"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_database_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _mapping_roots() -> tuple[str, Path]:
    remote = _required("SOULSYNC_PHASE5_REMOTE_ROOT").rstrip("/\\")
    local = Path(_required("SOULSYNC_PHASE5_LOCAL_ROOT"))
    return remote, local


def test_prepare_submission_unknown_before_container_restart() -> None:
    if _phase() != "prepare":
        pytest.skip("prepare phase only")

    adapter = _adapter()
    assert run_async(adapter.check_connection()) is True
    if isinstance(adapter, SABnzbdAdapter):
        assert run_async(adapter.category_exists(_category())) is True

    job_id = run_async(adapter.add_nzb(_NZB, category=_category()))
    assert job_id
    assert run_async(adapter.pause(str(job_id))) is True
    status = run_async(adapter.get_status(str(job_id)))
    assert status is not None
    assert status.category.casefold() == _category().casefold()

    remote_root, local_root = _mapping_roots()
    marker_dir = local_root / "phase5-acceptance-bundle"
    marker_dir.mkdir(parents=True, exist_ok=True)
    assert marker_dir.is_dir()

    conn = _connect()
    try:
        ensure_acquisition_schema(conn)
        request, created = create_request(
            conn,
            profile_id=1,
            scope="release_edition",
            entity_id=10,
            quality_profile_id=1,
            trigger="manual",
            idempotency_key=f"phase5-live-{uuid4()}",
        )
        assert created is True
        transition_request(conn, request.id, "searching")
        candidate, created = register_candidate(
            conn,
            request_id=request.id,
            source="usenet",
            protocol="usenet",
            content_scope="release_bundle",
            server_ref=f"phase5-live-{uuid4()}",
            title=status.name,
            indexer="phase5-acceptance",
            guid=f"phase5-live-{uuid4()}",
        )
        assert created is True
        transition_request(conn, request.id, "candidates_ready")
        download_id = f"phase5-live-{uuid4()}"
        record_grab(
            conn,
            download_id,
            "usenet",
            title=status.name,
            category=_category(),
            acquisition_request_id=request.id,
            release_candidate_id=candidate.id,
        )
        transition_request(conn, request.id, "grabbing")
        update_grab(
            conn,
            download_id,
            last_client_state="submission_unknown",
        )
        conn.commit()
        grab = get_grab(conn, download_id)
        assert grab is not None
        assert grab["external_job_id"] is None
        assert grab["last_client_state"] == "submission_unknown"
        assert remote_root
    finally:
        conn.close()


def test_verify_restart_adoption_and_mounted_path_mapping() -> None:
    if _phase() != "verify":
        pytest.skip("verify phase only")

    adapter = _adapter()
    assert run_async(adapter.check_connection()) is True
    conn = _connect()
    try:
        pending = list(open_grabs(conn, "usenet"))
        assert len(pending) == 1
        download_id = pending[0]["download_id"]
        already_adopted = bool(
            pending[0].get("adopted")
            and pending[0].get("external_job_id")
        )
    finally:
        conn.close()

    monitor = UsenetAcquisitionMonitor(
        _connect,
        adapter_getter=lambda: adapter,
        category_getter=_category,
        import_pipeline_runner=lambda: None,
    )
    result = monitor.run_once()
    assert result.reconciliation is not None
    if already_adopted:
        assert download_id in result.reconciliation.observed
    else:
        assert download_id in result.reconciliation.adopted

    conn = _connect()
    try:
        adopted = get_grab(conn, download_id)
        assert adopted is not None
        assert adopted["adopted"] == 1
        assert adopted["external_job_id"]
        external_job_id = adopted["external_job_id"]
    finally:
        conn.close()

    remote_root, local_root = _mapping_roots()
    config = {
        "download_source.usenet_path_mappings": [{
            "from": remote_root,
            "to": str(local_root),
        }],
    }
    config_get = lambda key, default=None: config.get(key, default)
    mapping_health = inspect_mapping_configuration(config_get)
    reported_health = inspect_reported_path(
        f"{remote_root}/phase5-acceptance-bundle",
        config_get=config_get,
    )
    assert mapping_health.to_public_dict()["healthy"] is True
    assert mapping_health.readable_target_count == 1
    assert reported_health.status == "mapped"
    assert reported_health.readable is True
    assert reported_health.remapped is True

    assert run_async(adapter.remove(str(external_job_id), delete_files=True)) is True
