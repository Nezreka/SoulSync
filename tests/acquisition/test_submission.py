"""Prepared grabs cross the external-client boundary exactly once."""

from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import patch

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.candidates import register_candidate
from core.acquisition.decision_engine import (
    CatalogContext,
    EffectivePolicy,
    RuntimeContext,
)
from core.acquisition.grabs import get_grab
from core.acquisition.blocklist import active_blocklisted_dedupe_keys
from core.acquisition.history import list_history_events, record_history_event
from core.acquisition.requests import create_request, transition_request
from core.acquisition.requests import get_request
from core.acquisition.submission import (
    SubmissionError,
    UsenetSubmissionAdapter,
    record_external_submission,
    record_uncertain_submission,
)
from core.acquisition.workflow import (
    evaluate_request_candidates,
    prepare_candidate_grab,
)
from core.download_plugins.candidate_store import CandidateStore, candidate_binding


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    ensure_acquisition_schema(connection)
    yield connection
    connection.close()


def _prepared(conn, store):
    request, _ = create_request(
        conn,
        profile_id=1,
        scope="release_group",
        entity_id=3,
        quality_profile_id=2,
        trigger="manual",
        idempotency_key="submission",
    )
    request = transition_request(
        conn, request.id, "searching", increment_attempts=True)
    with candidate_binding(1):
        token = store.put("https://indexer.invalid/get?api_key=secret")
    candidate, _ = register_candidate(
        conn,
        request_id=request.id,
        source="usenet",
        protocol="usenet",
        content_scope="release_bundle",
        server_ref=token,
        title="Artist - Album",
        guid="guid",
        facts={"artist": "Artist", "release_title": "Album"},
    )
    evaluate_request_candidates(
        conn,
        request.id,
        catalog=CatalogContext(artist="Artist", release_title="Album"),
        runtime=RuntimeContext(),
        policy=EffectivePolicy(),
        automatic=False,
    )
    return prepare_candidate_grab(
        conn,
        request.id,
        candidate.id,
        download_id="download-1",
        catalog=CatalogContext(artist="Artist", release_title="Album"),
        runtime=RuntimeContext(),
        policy=EffectivePolicy(),
    )


def test_usenet_submission_resolves_token_and_persists_client_job(conn):
    store = CandidateStore()
    prepared = _prepared(conn, store)

    class Client:
        def __init__(self):
            self.call = None

        def is_configured(self):
            return True

        async def add_nzb(self, url, category):
            self.call = (url, category)
            return "nzo-1"

    client = Client()
    adapter = UsenetSubmissionAdapter(
        client_getter=lambda: client,
        candidate_store=store,
        category_getter=lambda: "soulsync-music",
    )

    submission = asyncio.run(adapter.submit(prepared))
    grab = record_external_submission(conn, prepared, submission)

    assert client.call == (
        "https://indexer.invalid/get?api_key=secret", "soulsync-music")
    assert grab["status"] == "queued"
    assert grab["external_job_id"] == "nzo-1"
    assert grab["category"] == "soulsync-music"
    public_history = [event.to_public_dict() for event in list_history_events(
        conn, download_id=prepared.download_id)]
    assert public_history[-1]["event_type"] == "grab_submitted"
    assert "indexer.invalid" not in str(public_history)
    assert "secret" not in str(public_history)


def test_unknown_candidate_reference_fails_before_client_call(conn):
    prepared = _prepared(conn, CandidateStore())

    class Client:
        def is_configured(self):
            return True

        async def add_nzb(self, url, category):  # pragma: no cover - not called
            raise AssertionError("client must not be called")

    adapter = UsenetSubmissionAdapter(
        client_getter=Client,
        candidate_store=CandidateStore(),
    )

    with pytest.raises(SubmissionError, match="search again") as raised:
        asyncio.run(adapter.submit(prepared))
    assert raised.value.uncertain is False
    assert raised.value.failure_kind == "runtime"


def test_client_exception_is_uncertain_and_does_not_mark_failed(conn):
    store = CandidateStore()
    prepared = _prepared(conn, store)

    class Client:
        def is_configured(self):
            return True

        async def add_nzb(self, url, category):
            raise TimeoutError(
                "POST https://sab.invalid/api?api_key=secret timed out")

    adapter = UsenetSubmissionAdapter(
        client_getter=Client,
        candidate_store=store,
    )

    with pytest.raises(SubmissionError) as raised:
        asyncio.run(adapter.submit(prepared))
    assert raised.value.uncertain is True
    assert "sab.invalid" not in str(raised.value)
    grab = record_uncertain_submission(conn, prepared, str(raised.value))
    assert grab["status"] == "submitting"
    assert grab["last_client_state"] == "submission_unknown"
    assert list_history_events(
        conn, download_id=prepared.download_id)[-1].event_type == (
        "grab_submission_uncertain")


def test_usenet_plugin_attaches_exactly_one_poller(conn):
    from core.download_plugins.usenet import UsenetDownloadPlugin

    store = CandidateStore()
    prepared = _prepared(conn, store)
    submission = type("Submission", (), {
        "source": "usenet",
        "external_job_id": "nzo-monitor",
    })()
    plugin = UsenetDownloadPlugin()

    with patch("core.download_plugins.usenet.threading.Thread") as thread_cls:
        plugin.monitor_acquisition_submission(prepared, submission)
        plugin.monitor_acquisition_submission(prepared, submission)

    assert plugin.active_downloads[prepared.download_id]["job_id"] == "nzo-monitor"
    assert thread_cls.call_count == 1
    assert thread_cls.call_args.kwargs["target"] == plugin._poll_job
    assert thread_cls.call_args.kwargs["args"] == (
        prepared.download_id, "nzo-monitor")


def test_plugin_candidate_failure_updates_request_history_and_blocklist(tmp_path):
    from core.download_plugins.usenet import UsenetDownloadPlugin

    path = str(tmp_path / "acquisition.db")
    seed = sqlite3.connect(path)
    seed.row_factory = sqlite3.Row
    ensure_acquisition_schema(seed)
    store = CandidateStore()
    prepared = _prepared(seed, store)
    submission = type("Submission", (), {
        "source": "usenet",
        "external_job_id": "nzo-failed",
        "client": "FakeSAB",
        "category": "soulsync",
    })()
    record_external_submission(seed, prepared, submission)
    seed.commit()
    seed.close()

    def connect():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    plugin = UsenetDownloadPlugin()
    with patch(
        "core.download_plugins.usenet._grabs_conn", side_effect=connect,
    ):
        plugin._mark_error(
            prepared.download_id,
            "Client reported a bad NZB",
            failure_kind="candidate",
        )

    check = connect()
    assert get_request(check, prepared.request.id).status == "failed"
    assert prepared.candidate.dedupe_key in active_blocklisted_dedupe_keys(check)
    assert [event.event_type for event in list_history_events(
        check, download_id=prepared.download_id)][-2:] == [
            "grab_failed", "candidate_blocklisted"]
    check.close()


def test_history_schema_migrates_closed_event_enum_without_losing_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE acquisition_history (
            id TEXT PRIMARY KEY,
            request_id TEXT,
            candidate_id TEXT,
            download_id TEXT,
            event_type TEXT NOT NULL,
            actor_profile_id INTEGER NOT NULL,
            reason_code TEXT,
            message TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK(actor_profile_id = 1),
            CHECK(event_type IN ('request_created'))
        );
        INSERT INTO acquisition_history(
            id, request_id, event_type, actor_profile_id)
        VALUES('old-event', 'request-1', 'request_created', 1);
    """)

    record_history_event(
        conn, "grab_submitted", request_id="request-1")

    events = list_history_events(conn, request_id="request-1")
    assert [event.id for event in events][0] == "old-event"
    assert [event.event_type for event in events] == [
        "request_created", "grab_submitted"]
    table_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='acquisition_history'"
    ).fetchone()[0]
    assert "CHECK(event_type IN" not in table_sql
    conn.close()
