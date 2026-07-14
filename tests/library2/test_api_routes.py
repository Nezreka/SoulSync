"""Flask-level tests for the Library v2 API (api/library_v2.py).

The core modules have their own unit tests; these cover the route layer's
own logic — artwork URL rewriting, monitor/profile cascades incl. the
consolidated-duplicate guard, delete cleanup, and input validation — against
a real (temp) SQLite schema with a fake MusicDatabase for the mirror calls.
"""

from __future__ import annotations

import asyncio
import sqlite3
from io import BytesIO

import pytest

flask = pytest.importorskip("flask")


class FakeDB:
    """MusicDatabase stand-in: real sqlite connection + recorded mirror calls."""

    def __init__(self, path: str):
        self.database_path = path
        self.wishlist_adds = []
        self.wishlist_removes = []
        self.watchlist_adds = []
        self.watchlist_removes = []

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- wishlist/watchlist mirror surface (recorded, always succeeds) -------
    def add_to_wishlist(self, payload, source_type="unknown", source_info=None,
                        user_initiated=False, profile_id=1, quality_profile_id=None,
                        raise_on_error=False):
        self.wishlist_adds.append({
            "id": payload.get("id"), "profile_id": profile_id,
            "quality_profile_id": quality_profile_id, "source_type": source_type,
            "user_initiated": user_initiated,
        })
        return True

    def remove_from_wishlist(self, track_id, profile_id=1, raise_on_error=False):
        self.wishlist_removes.append({"id": track_id, "profile_id": profile_id})
        return True

    def add_artist_to_watchlist(self, ext_id, name, profile_id, source,
                                raise_on_error=False):
        self.watchlist_adds.append({"ext_id": ext_id, "profile_id": profile_id})
        return True

    def remove_artist_from_watchlist(self, ext_id, profile_id,
                                     raise_on_error=False):
        self.watchlist_removes.append({"ext_id": ext_id, "profile_id": profile_id})
        return True


@pytest.fixture
def api(tmp_path):
    """A test client over a seeded lib2 DB. Yields (client, FakeDB, ids)."""
    db_path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)

    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name, sort_name, spotify_id, monitored) "
                "VALUES('Drake','Drake','sp-drake',0)")
    artist_id = cur.lastrowid

    def _album(title, album_type, monitored=0):
        cur.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, album_type, monitored) "
            "VALUES(?,?,?,?)", (artist_id, title, album_type, monitored))
        album_id = cur.lastrowid
        cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                    (album_id, artist_id))
        return album_id

    views_id = _album("Views", "album")
    single_id = _album("One Dance", "single")
    ep_id = _album("Best EP", "ep")

    def _track(album_id, title, monitored=0, spotify_id=None, canonical=None):
        cur.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, monitored, "
            "spotify_id, canonical_track_id) VALUES(?,?,1,?,?,?)",
            (album_id, title, monitored, spotify_id, canonical))
        track_id = cur.lastrowid
        cur.execute("INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id) "
                    "VALUES(?,?)", (track_id, artist_id))
        return track_id

    # Canonical pair: the album version owns the file, the single variant was
    # consolidated away (no file, canonical link to the album version).
    album_track = _track(views_id, "One Dance", spotify_id="sp-t1")
    single_track = _track(single_id, "One Dance", canonical=album_track)
    ep_track = _track(ep_id, "EP Song", spotify_id="sp-t2")
    cur.execute("INSERT INTO lib2_track_files(track_id, path, format, bitrate) "
                "VALUES(?, '/m/one-dance.flac', 'flac', 1000)", (album_track,))
    conn.commit()
    conn.close()

    db = FakeDB(db_path)
    # ADR-01: lib2 writes are admin-only (profile 1). Tests flip this to a
    # non-admin id to probe the rejection path.
    db.active_profile = 1
    db.config = {"features.library_v2": True}
    db.acquisition_search_adapters = []
    db.acquisition_submission_adapters = {}
    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes
    register_library_v2_routes(
        app,
        get_database=lambda: db,
        config_get=lambda key, default=None: db.config.get(key, default),
        config_manager=None,
        profile_id_getter=lambda: db.active_profile,
        acquisition_search_adapters_getter=(
            lambda _criteria: db.acquisition_search_adapters),
        acquisition_async_runner=asyncio.run,
        acquisition_submission_adapter_getter=(
            lambda source: db.acquisition_submission_adapters.get(source)),
    )
    ids = {"artist": artist_id, "views": views_id, "single": single_id,
           "ep": ep_id, "album_track": album_track,
           "single_track": single_track, "ep_track": ep_track}
    yield app.test_client(), db, ids


def _conn(db: FakeDB) -> sqlite3.Connection:
    return db._get_connection()


def test_canonical_api_rejects_chains_and_invalid_ids(api):
    client, _db, ids = api

    response = client.post(
        f"/api/library/v2/tracks/{ids['album_track']}/canonical",
        json={"canonical_track_id": ids["ep_track"]},
    )
    assert response.status_code == 400
    assert "canonical target" in response.get_json()["error"]

    response = client.post(
        f"/api/library/v2/tracks/{ids['ep_track']}/canonical",
        json={"canonical_track_id": ids["single_track"]},
    )
    assert response.status_code == 400
    assert "itself a duplicate" in response.get_json()["error"]

    response = client.post(
        f"/api/library/v2/tracks/{ids['ep_track']}/canonical",
        json={"canonical_track_id": "not-an-id"},
    )
    assert response.status_code == 400
    assert "must be an integer" in response.get_json()["error"]


def test_canonical_api_validates_pair_and_links_compatible_tracks(api):
    client, db, ids = api
    conn = _conn(db)
    candidate = conn.execute(
        """INSERT INTO lib2_tracks(album_id, title, duration, spotify_id)
           VALUES(?, 'One Dance', 200000, 'sp-t1')""",
        (ids["single"],),
    ).lastrowid
    conn.execute(
        """UPDATE lib2_tracks SET duration=202000, spotify_id='shared-recording'
            WHERE id=?""",
        (ids["ep_track"],),
    )
    conn.commit()
    conn.close()

    mismatch = client.post(
        f"/api/library/v2/tracks/{ids['single_track']}/canonical",
        json={"canonical_track_id": ids["ep_track"]},
    )
    assert mismatch.status_code == 400
    assert "titles" in mismatch.get_json()["error"]

    linked = client.post(
        f"/api/library/v2/tracks/{candidate}/canonical",
        json={"canonical_track_id": ids["album_track"]},
    )
    assert linked.status_code == 200
    assert linked.get_json()["canonical_track_id"] == ids["album_track"]
    conn = _conn(db)
    assert conn.execute(
        "SELECT canonical_track_id FROM lib2_tracks WHERE id=?", (candidate,)
    ).fetchone()[0] == ids["album_track"]
    conn.close()


def test_eps_get_local_artwork_urls(api):
    """Every release group — including EPs — must point at the local artwork
    endpoint, never at a raw DB image_url (which may be a media-server URL)."""
    client, _db, ids = api
    data = client.get(f"/api/library/v2/artists/{ids['artist']}").get_json()
    assert data["success"] is True
    for group in ("albums", "eps", "singles"):
        for entry in data["artist"][group]:
            assert entry["image_url"] == f"/api/library/v2/artwork/album/{entry['id']}"


def test_acquisition_request_resolves_server_owned_profiles_and_is_idempotent(api):
    client, _db, ids = api
    payload = {
        "scope": "release_group",
        "entity_id": ids["views"],
        "idempotency_key": "manual:views:1",
        "quality_profile_id": 999,
    }

    first = client.post(
        "/api/library/v2/acquisition/requests", json=payload)
    second = client.post(
        "/api/library/v2/acquisition/requests", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    first_data = first.get_json()
    second_data = second.get_json()
    assert first_data["request"]["id"] == second_data["request"]["id"]
    assert first_data["request"]["profile_id"] == 1
    assert first_data["request"]["quality_profile_id"] == 1
    assert first_data["request"]["status"] == "searching"
    assert first_data["request"]["search_options"]["content_scope"] == (
        "release_bundle")
    assert first_data["request"]["search_options"]["release_group_id"] == (
        ids["views"])
    assert second_data["created"] is False
    history = client.get(
        f"/api/library/v2/acquisition/requests/{first_data['request']['id']}/history"
    ).get_json()
    assert [event["event_type"] for event in history["events"]] == [
        "request_created"]


def test_acquisition_correlation_coverage_endpoint_is_redacted(api):
    client, db, _ids = api
    conn = db._get_connection()
    try:
        from core.acquisition.correlation_coverage import record_correlation_outcome
        record_correlation_outcome(conn, "manual", "prepared")
        record_correlation_outcome(conn, "scheduled", "blocked")
        conn.commit()
    finally:
        conn.close()

    response = client.get(
        "/api/library/v2/acquisition/correlation-coverage?days=7")

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["enforced"] is False
    assert data["enforcement_key"] == "features.acquisition_contract_enforce"
    assert data["coverage"]["consumers"]["manual"]["prepared"] == 1
    assert data["coverage"]["consumers"]["scheduled"]["blocked"] == 1
    assert "path" not in str(data).lower()


@pytest.mark.parametrize("days", ["abc", "0", "91"])
def test_acquisition_correlation_coverage_validates_window(api, days):
    client, _db, _ids = api
    response = client.get(
        f"/api/library/v2/acquisition/correlation-coverage?days={days}")
    assert response.status_code == 400


def test_public_acquisition_request_rejects_browser_owned_search_options(api):
    client, _db, ids = api

    response = client.post("/api/library/v2/acquisition/requests", json={
        "scope": "release_group",
        "entity_id": ids["views"],
        "idempotency_key": "forged-options",
        "search_options": {
            "content_scope": "recording",
            "any_release_ok": True,
            "identifiers": {"download_url": "https://attacker.invalid"},
        },
    })
    upgrade = client.post("/api/library/v2/acquisition/requests", json={
        "scope": "upgrade",
        "entity_id": ids["views"],
        "idempotency_key": "forged-upgrade",
    })

    assert response.status_code == 400
    assert response.get_json()["error"] == "search_options are server-managed"
    assert upgrade.status_code == 400
    assert "public acquisition scope" in upgrade.get_json()["error"]


def test_public_acquisition_options_preserve_group_edition_recording_layers(api):
    client, db, ids = api
    conn = db._get_connection()
    try:
        from core.library2.editions import backfill_editions
        backfill_editions(conn.cursor())
        row = conn.execute(
            """SELECT rt.recording_id, rt.release_edition_id
                 FROM lib2_release_tracks rt WHERE rt.track_id=?""",
            (ids["album_track"],),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()

    edition = client.post("/api/library/v2/acquisition/requests", json={
        "scope": "release_edition",
        "entity_id": row["release_edition_id"],
        "idempotency_key": "public-edition-options",
    }).get_json()["request"]
    recording = client.post("/api/library/v2/acquisition/requests", json={
        "scope": "recording",
        "entity_id": row["recording_id"],
        "idempotency_key": "public-recording-options",
    }).get_json()["request"]

    assert edition["search_options"] == {
        "content_scope": "release_bundle",
        "release_edition_id": row["release_edition_id"],
        "release_group_id": ids["views"],
    }
    assert recording["search_options"]["content_scope"] == "recording"
    assert recording["search_options"]["recording_id"] == row["recording_id"]
    assert recording["search_options"]["release_edition_id"] == row[
        "release_edition_id"]
    assert recording["search_options"]["release_group_id"] == ids["views"]
    assert recording["search_options"]["lib2_track_id"] == ids["album_track"]


def test_non_admin_cannot_create_acquisition_request(api):
    client, db, ids = api
    db.active_profile = 2

    response = client.post("/api/library/v2/acquisition/requests", json={
        "scope": "release_group",
        "entity_id": ids["views"],
        "idempotency_key": "forbidden",
    })

    assert response.status_code == 403


def test_acquisition_evaluation_returns_only_public_candidates_and_reasons(api):
    client, db, ids = api
    created = client.post("/api/library/v2/acquisition/requests", json={
        "scope": "release_group",
        "entity_id": ids["views"],
        "idempotency_key": "evaluate-views",
    }).get_json()
    request_id = created["request"]["id"]
    conn = db._get_connection()
    try:
        from core.acquisition.candidates import register_candidate
        good, _ = register_candidate(
            conn,
            request_id=request_id,
            source="usenet",
            protocol="usenet",
            content_scope="release_bundle",
            server_ref="ssc1-secret-good",
            title="Drake - Views",
            guid="good",
            facts={
                "artist": "Drake", "release_title": "Views",
                "format": "flac", "bit_depth": 24,
                "sample_rate": 96000, "track_count": 1,
            },
        )
        bad, _ = register_candidate(
            conn,
            request_id=request_id,
            source="usenet",
            protocol="usenet",
            content_scope="release_bundle",
            server_ref="ssc1-secret-bad",
            title="Other - Views",
            guid="bad",
            facts={"artist": "Other", "release_title": "Views", "format": "flac"},
        )
        conn.commit()
    finally:
        conn.close()

    evaluated = client.post(
        f"/api/library/v2/acquisition/requests/{request_id}/evaluate",
        json={"automatic": False},
    )

    assert evaluated.status_code == 200
    data = evaluated.get_json()
    assert {item["id"] for item in data["candidates"]} == {good.id, bad.id}
    rejected = next(item for item in data["candidates"] if item["id"] == bad.id)
    assert rejected["decision"]["accepted"] is False
    assert "artist_mismatch" in {
        reason["code"] for reason in rejected["decision"]["rejections"]}
    assert "server_ref" not in str(data)
    assert "ssc1-secret" not in str(data)
    assert data["selected_candidate_id"] is None

    listed = client.get(
        f"/api/library/v2/acquisition/requests/{request_id}/candidates"
    ).get_json()
    assert "server_ref" not in str(listed)


def test_acquisition_search_is_server_owned_and_persists_public_decisions(api):
    client, db, ids = api
    db.config["download_source.mode"] = "usenet"
    from core.acquisition.prowlarr_adapter import (
        ProwlarrAcquisitionAdapter,
        ProwlarrCandidateParser,
    )
    from core.download_plugins.candidate_store import CandidateStore
    from core.prowlarr_client import ProwlarrSearchResult

    class Prowlarr:
        def is_configured(self):
            return True

        async def search(self, query, **kwargs):
            assert query == "Drake Views"
            return [ProwlarrSearchResult(
                guid="views-flac",
                title="Drake - Views [24bit 96kHz FLAC]",
                indexer_id=7,
                indexer_name="Test Indexer",
                protocol="usenet",
                download_url="https://indexer.invalid/get?api_key=secret",
                size=800_000_000,
                grabs=10,
                raw={"downloadUrl": "https://indexer.invalid/secret"},
            )]

    db.acquisition_search_adapters = [ProwlarrAcquisitionAdapter(
        "usenet",
        client=Prowlarr(),
        parser=ProwlarrCandidateParser(
            "usenet", candidate_store=CandidateStore()),
        download_client_configured=lambda: True,
    )]
    created = client.post("/api/library/v2/acquisition/requests", json={
        "scope": "release_group",
        "entity_id": ids["views"],
        "idempotency_key": "search-views",
    }).get_json()

    searched = client.post(
        f"/api/library/v2/acquisition/requests/{created['request']['id']}/search",
        json={"automatic": True, "sources": ["torrent"]},
    )

    assert searched.status_code == 200
    data = searched.get_json()
    assert data["success"] is True
    assert data["search"]["sources"][0]["source"] == "usenet"
    assert data["persisted"] == {"created": 1, "refreshed": 0}
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["decision"]["accepted"] is True
    # Persisted trigger owns Manual/Auto behavior; browser flags are ignored.
    assert data["selected_candidate_id"] is None
    assert "server_ref" not in str(data)
    assert "indexer.invalid" not in str(data)
    assert "secret" not in str(data)
    history = client.get(
        f"/api/library/v2/acquisition/requests/{created['request']['id']}/history"
    ).get_json()
    assert [event["event_type"] for event in history["events"]] == [
        "request_created", "search_completed", "candidates_evaluated"]


def test_acquisition_search_operational_failure_is_retryable_not_no_candidate(api):
    client, db, ids = api
    db.config["download_source.mode"] = "usenet"

    class Parser:
        source = "usenet"

        def parse(self, payload, *, criteria):  # pragma: no cover - not called
            return None

    class Unconfigured:
        source = "usenet"
        parser = Parser()

        def is_configured(self):
            return False

        async def search(self, criteria):  # pragma: no cover - not called
            return []

    db.acquisition_search_adapters = [Unconfigured()]
    created = client.post("/api/library/v2/acquisition/requests", json={
        "scope": "release_group",
        "entity_id": ids["views"],
        "idempotency_key": "search-unconfigured",
    }).get_json()

    searched = client.post(
        f"/api/library/v2/acquisition/requests/{created['request']['id']}/search")

    assert searched.status_code == 503
    data = searched.get_json()
    assert data["request"]["status"] == "failed"
    assert data["request"]["status"] != "no_candidate"
    assert data["search"]["sources"][0]["status"] == "unconfigured"

    retried = client.post(
        f"/api/library/v2/acquisition/requests/{created['request']['id']}/retry"
    )
    assert retried.status_code == 200
    retried_data = retried.get_json()["request"]
    assert retried_data["status"] == "searching"
    assert retried_data["attempts"] == 2
    history = client.get(
        f"/api/library/v2/acquisition/requests/{created['request']['id']}/history"
    ).get_json()
    assert [event["event_type"] for event in history["events"]] == [
        "request_created", "search_failed", "retry_started"]


def test_acquisition_import_detail_never_exposes_server_paths(api):
    client, db, _ids = api
    conn = _conn(db)
    from core.acquisition import ensure_acquisition_schema
    from tests.acquisition.test_bundle_inventory import _pending_import
    from core.acquisition.imports import record_inventory_result
    ensure_acquisition_schema(conn)
    pending, _request, _candidate = _pending_import(
        conn,
        download_id="api-import-path-redaction",
        output_path="C:/sab/secret/album",
    )
    record_inventory_result(
        conn,
        pending.id,
        [{"relative_path": "Disc 1/01.flac", "size_bytes": 10}],
        resolved_path="D:/mounted/secret/album",
    )
    conn.commit()
    conn.close()

    response = client.get(
        f"/api/library/v2/acquisition/imports/{pending.id}")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["import"]["inventory"][0]["relative_path"] == "Disc 1/01.flac"
    rendered = str(payload)
    assert "C:/sab" not in rendered
    assert "D:/mounted" not in rendered


def test_acquisition_path_health_reports_mapping_without_exposing_paths(
    api, tmp_path
):
    client, db, _ids = api
    local_root = tmp_path / "mounted-secret"
    (local_root / "album").mkdir(parents=True)
    db.config["download_source.usenet_path_mappings"] = [{
        "from": "C:/sab/secret",
        "to": str(local_root),
    }]
    conn = _conn(db)
    from core.acquisition import ensure_acquisition_schema
    from tests.acquisition.test_bundle_inventory import _pending_import
    ensure_acquisition_schema(conn)
    pending, _request, _candidate = _pending_import(
        conn,
        download_id="api-path-health",
        output_path="C:/sab/secret/album",
    )
    conn.commit()
    conn.close()

    response = client.get("/api/library/v2/acquisition/path-health")

    assert response.status_code == 200
    payload = response.get_json()
    check = next(
        item for item in payload["imports"]
        if item["import_id"] == pending.id
    )
    assert check["status"] == "mapped"
    assert check["readable"] is True
    assert payload["mappings"]["healthy"] is True
    rendered = str(payload)
    assert "C:/sab/secret" not in rendered
    assert str(local_root) not in rendered


def test_acquisition_blocklist_can_be_read_and_manually_unblocked(api):
    client, db, ids = api
    created = client.post("/api/library/v2/acquisition/requests", json={
        "scope": "release_group",
        "entity_id": ids["views"],
        "idempotency_key": "blocklist-api",
    }).get_json()
    conn = db._get_connection()
    try:
        from core.acquisition.blocklist import block_candidate
        from core.acquisition.candidates import register_candidate
        candidate, _ = register_candidate(
            conn,
            request_id=created["request"]["id"],
            source="usenet",
            protocol="usenet",
            content_scope="release_bundle",
            server_ref="ssc1-blocked",
            title="Drake - Views",
            indexer="Indexer",
            guid="blocked-guid",
            facts={"artist": "Drake", "release_title": "Views"},
        )
        entry, _ = block_candidate(
            conn, candidate.id, reason_code="client_failure")
        conn.commit()
    finally:
        conn.close()

    listed = client.get(
        "/api/library/v2/acquisition/blocklist").get_json()
    assert [item["id"] for item in listed["entries"]] == [entry.id]
    assert "dedupe_key" not in str(listed)
    assert "server_ref" not in str(listed)

    removed = client.delete(
        f"/api/library/v2/acquisition/blocklist/{entry.id}")
    assert removed.status_code == 200
    assert removed.get_json()["entry"]["active"] is False
    assert client.get(
        "/api/library/v2/acquisition/blocklist").get_json()["entries"] == []


def test_acquisition_grab_submits_once_and_returns_only_public_state(api):
    client, db, ids = api
    from core.acquisition.submission import ExternalSubmission

    class Submitter:
        source = "usenet"

        def __init__(self):
            self.calls = 0
            self.monitored = []

        async def submit(self, prepared):
            self.calls += 1
            return ExternalSubmission(
                source="usenet",
                external_job_id="secret-client-job-id",
                client="FakeSAB",
                category="soulsync",
            )

        def start_monitor(self, prepared, submission):
            self.monitored.append((prepared.download_id, submission.external_job_id))

    submitter = Submitter()
    db.acquisition_submission_adapters["usenet"] = submitter
    created = client.post("/api/library/v2/acquisition/requests", json={
        "scope": "release_group",
        "entity_id": ids["views"],
        "idempotency_key": "grab-api",
    }).get_json()
    request_id = created["request"]["id"]
    conn = db._get_connection()
    try:
        from core.acquisition.candidates import register_candidate
        candidate, _ = register_candidate(
            conn,
            request_id=request_id,
            source="usenet",
            protocol="usenet",
            content_scope="release_bundle",
            server_ref="ssc1-private-token",
            title="Drake - Views",
            guid="grab-guid",
            # Missing artist is a visible, admin-overridable policy reject.
            facts={"release_title": "Views"},
        )
        conn.commit()
    finally:
        conn.close()
    evaluated = client.post(
        f"/api/library/v2/acquisition/requests/{request_id}/evaluate")
    assert evaluated.status_code == 200
    assert evaluated.get_json()["candidates"][0]["decision"]["can_force"] is True

    first = client.post(
        f"/api/library/v2/acquisition/requests/{request_id}/grab",
        json={
            "candidate_id": candidate.id,
            "force": True,
            "download_url": "https://attacker.invalid/ignored",
        },
    )
    second = client.post(
        f"/api/library/v2/acquisition/requests/{request_id}/grab",
        json={"candidate_id": candidate.id, "force": True},
    )

    assert first.status_code == 202
    first_data = first.get_json()
    assert first_data["submission_status"] == "queued"
    assert first_data["grab"]["status"] == "queued"
    assert second.status_code == 200
    assert second.get_json()["created"] is False
    assert submitter.calls == 1
    assert len(submitter.monitored) == 1
    assert "secret-client-job-id" not in str(first_data)
    assert "ssc1-private-token" not in str(first_data)
    assert "attacker.invalid" not in str(first_data)
    assert "external_job_id" not in str(first_data)
    assert "output_path" not in first_data["grab"]
    assert first_data["grab"]["has_output_path"] is False

    download_id = first_data["grab"]["download_id"]
    recovered = client.get(
        f"/api/library/v2/acquisition/grabs/{download_id}").get_json()
    assert recovered["grab"] == first_data["grab"]
    history = client.get(
        f"/api/library/v2/acquisition/requests/{request_id}/history"
    ).get_json()
    assert [event["event_type"] for event in history["events"]] == [
        "request_created",
        "candidates_evaluated",
        "force_grab",
        "grab_prepared",
        "grab_submitted",
    ]


def test_wanted_materialize_endpoint_is_shadow_only_and_idempotent(api):
    client, db, ids = api
    conn = db._get_connection()
    try:
        from core.library2.wanted import recompute_wanted
        conn.execute("DELETE FROM lib2_track_files WHERE track_id=?", (ids["album_track"],))
        conn.execute(
            """INSERT INTO lib2_monitor_rules(
                   entity_type, entity_id, profile_id, monitored, provenance)
               VALUES('track', ?, 1, 1, 'user_explicit')
               ON CONFLICT(entity_type, entity_id, profile_id) DO UPDATE SET
                   monitored=1, provenance='user_explicit'""",
            (ids["album_track"],),
        )
        recompute_wanted(conn, profile_id=1, track_ids=[ids["album_track"]])
        # Fixture rows were inserted after schema ensure; backfill their shadow
        # recording/edition rows now, as production importer does.
        from core.library2.editions import backfill_editions
        backfill_editions(conn.cursor())
        conn.commit()
    finally:
        conn.close()

    first = client.post(
        "/api/library/v2/acquisition/wanted/materialize",
        json={"track_ids": [ids["album_track"]]},
    ).get_json()
    second = client.post(
        "/api/library/v2/acquisition/wanted/materialize",
        json={"track_ids": [ids["album_track"]]},
    ).get_json()

    assert first["success"] is True and first["shadow"] is True
    assert len(first["requests"]) == 1
    assert first["requests"][0]["created"] is True
    assert second["requests"][0]["created"] is False
    assert first["requests"][0]["request"]["id"] == second["requests"][0]["request"]["id"]


def test_wanted_projection_status_endpoint_reports_cutover_readiness(api):
    client, db, _ids = api
    body = client.get("/api/library/v2/wanted-projection/status").get_json()
    assert body["success"] is True
    assert body["consumer_ready"] is False
    assert body["missing"] == body["tracks"]

    with _conn(db) as conn:
        from core.library2.wanted import recompute_wanted
        recompute_wanted(conn)
        conn.commit()
    body = client.get("/api/library/v2/wanted-projection/status").get_json()
    assert body["consumer_ready"] is True
    assert body["projection_version"] >= 1
    assert body["missing"] == 0 and body["stale"] == 0


def test_monitor_album_mirrors_with_active_profile(api):
    client, db, ids = api
    resp = client.post(f"/api/library/v2/albums/{ids['ep']}/monitor",
                       json={"monitored": True}).get_json()
    assert resp["success"] is True
    with _conn(db) as conn:
        assert conn.execute("SELECT monitored FROM lib2_albums WHERE id=?",
                            (ids["ep"],)).fetchone()[0] == 1
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                            (ids["ep_track"],)).fetchone()[0] == 1
    # The wishlist mirror carries the admin profile (the only profile that
    # may write to Library v2 per ADR-01) and the track's quality profile.
    assert db.wishlist_adds, "monitoring a fileless track must queue it"
    assert all(a["profile_id"] == 1 for a in db.wishlist_adds)
    assert all(a["quality_profile_id"] == 1 for a in db.wishlist_adds)


def test_track_toggle_is_user_initiated_album_toggle_is_not(api):
    """Audit P1-11: only the DIRECT track-level toggle may clear a user's
    wishlist-ignore (user_initiated=True). An album toggle is a cascade over
    tracks the user may have deliberately cancelled — it must respect the
    ignore-list, as must scheduled jobs and profile assignments."""
    client, db, ids = api
    resp = client.post(f"/api/library/v2/tracks/{ids['ep_track']}/monitor",
                       json={"monitored": True}).get_json()
    assert resp["success"] is True
    assert db.wishlist_adds and all(a["user_initiated"] for a in db.wishlist_adds)

    db.wishlist_adds.clear()
    resp = client.post(f"/api/library/v2/albums/{ids['ep']}/monitor",
                       json={"monitored": True}).get_json()
    assert resp["success"] is True
    assert db.wishlist_adds and not any(a["user_initiated"] for a in db.wishlist_adds)

    db.wishlist_adds.clear()
    resp = client.post(
        f"/api/library/v2/artists/{ids['artist']}/quality-profile",
        json={"quality_profile_id": 2, "monitor_existing": True},
    ).get_json()
    assert resp["success"] is True
    assert db.wishlist_adds and not any(a["user_initiated"] for a in db.wishlist_adds)


def test_album_unmonitor_preserves_explicit_track_intent(api):
    """Audit P1-14: an album toggle is a cascade — it must not destroy a
    deliberate per-track choice. A track the user explicitly monitored stays
    monitored (and is NOT withdrawn from the wishlist) when its album is
    unmonitored; rule-less siblings follow the cascade."""
    client, db, ids = api
    # Direct user action on the track: explicit intent.
    assert client.post(f"/api/library/v2/tracks/{ids['ep_track']}/monitor",
                       json={"monitored": True}).get_json()["success"] is True
    # Album ON then OFF — the cascade projects the sibling-less album; the
    # explicit track must survive the OFF.
    assert client.post(f"/api/library/v2/albums/{ids['ep']}/monitor",
                       json={"monitored": True}).get_json()["success"] is True
    db.wishlist_removes.clear()
    resp = client.post(f"/api/library/v2/albums/{ids['ep']}/monitor",
                       json={"monitored": False}).get_json()
    assert resp["success"] is True
    assert resp["preserved_tracks"] == 1
    with _conn(db) as conn:
        assert conn.execute("SELECT monitored FROM lib2_albums WHERE id=?",
                            (ids["ep"],)).fetchone()[0] == 0
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                            (ids["ep_track"],)).fetchone()[0] == 1
    assert all(r["id"] != "sp-t2" for r in db.wishlist_removes), (
        "the explicitly monitored track must not be withdrawn from the wishlist")


def test_album_cascade_still_projects_ruleless_tracks(api):
    """Without explicit per-track intent the album toggle behaves exactly as
    before: every child follows the cascade."""
    client, db, ids = api
    assert client.post(f"/api/library/v2/albums/{ids['views']}/monitor",
                       json={"monitored": True}).get_json()["preserved_tracks"] == 0
    with _conn(db) as conn:
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                            (ids["album_track"],)).fetchone()[0] == 1
    resp = client.post(f"/api/library/v2/albums/{ids['views']}/monitor",
                       json={"monitored": False}).get_json()
    assert resp["preserved_tracks"] == 0
    with _conn(db) as conn:
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                            (ids["album_track"],)).fetchone()[0] == 0


def test_bulk_monitor_updates_rules_projection_and_preserves_explicit_tracks(api):
    import time

    client, db, ids = api
    client.post(
        f"/api/library/v2/tracks/{ids['ep_track']}/monitor",
        json={"monitored": True},
    )
    response = client.post(
        f"/api/library/v2/artists/{ids['artist']}/releases/monitor",
        json={"scope": "all", "monitored": False},
    )
    assert response.status_code == 200
    started = response.get_json()
    assert started["job_id"]
    for _ in range(200):
        status = client.get(
            "/api/library/v2/jobs/status",
            query_string={"job_id": started["job_id"]},
        ).get_json()
        if not status["running"]:
            break
        time.sleep(0.01)
    assert status["running"] is False and status["error"] is None
    assert status["job_id"] == started["job_id"]
    assert any(job["job_id"] == started["job_id"] for job in status["jobs"])
    assert client.get(
        "/api/library/v2/jobs/status",
        query_string={"job_id": "unknown"},
    ).status_code == 404

    with _conn(db) as conn:
        explicit = conn.execute(
            "SELECT wanted, reason FROM lib2_wanted_tracks WHERE track_id=?",
            (ids["ep_track"],),
        ).fetchone()
        cascaded = conn.execute(
            "SELECT wanted, reason FROM lib2_wanted_tracks WHERE track_id=?",
            (ids["album_track"],),
        ).fetchone()
        album_rules = conn.execute(
            "SELECT COUNT(*) FROM lib2_monitor_rules "
            "WHERE entity_type='album' AND provenance='user_explicit'"
        ).fetchone()[0]
    assert dict(explicit) == {"wanted": 1, "reason": "track_explicit"}
    assert dict(cascaded) == {"wanted": 0, "reason": "track_rule:cascade"}
    assert album_rules == 3


def test_monitor_actions_record_provenance(api):
    client, db, ids = api
    client.post(f"/api/library/v2/tracks/{ids['ep_track']}/monitor",
                json={"monitored": True})
    client.post(f"/api/library/v2/albums/{ids['ep']}/monitor",
                json={"monitored": True})
    client.post(f"/api/library/v2/artists/{ids['artist']}/monitor",
                json={"monitored": True})
    with _conn(db) as conn:
        rows = {(r["entity_type"], r["entity_id"]): r["provenance"]
                for r in conn.execute(
                    "SELECT entity_type, entity_id, provenance FROM lib2_monitor_rules")}
    assert rows[("track", ids["ep_track"])] == "user_explicit"
    assert rows[("album", ids["ep"])] == "user_explicit"
    assert rows[("artist", ids["artist"])] == "user_explicit"


def test_profile_assign_respects_explicit_track_unmonitor(api):
    """The monitor_existing opt-in is a bulk cascade — it must not overturn a
    track the user explicitly unmonitored."""
    client, db, ids = api
    # Explicit user decision: this track stays off.
    assert client.post(f"/api/library/v2/tracks/{ids['ep_track']}/monitor",
                       json={"monitored": False}).get_json()["success"] is True
    resp = client.post(
        f"/api/library/v2/artists/{ids['artist']}/quality-profile",
        json={"quality_profile_id": 2, "monitor_existing": True},
    ).get_json()
    assert resp["success"] is True
    with _conn(db) as conn:
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                            (ids["ep_track"],)).fetchone()[0] == 0
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                            (ids["album_track"],)).fetchone()[0] == 1


def test_profile_assign_does_not_touch_monitoring_by_default(api):
    """Audit P1-15: assigning a quality profile is a quality decision, not a
    wanted-action. Without the explicit opt-in it must neither flip monitored
    flags nor queue wishlist adds — a deliberately unmonitored track must not
    get re-downloaded because the user changed a profile."""
    client, db, ids = api
    resp = client.post(
        f"/api/library/v2/artists/{ids['artist']}/quality-profile",
        json={"quality_profile_id": 2},  # upgrade policy, but no opt-in
    ).get_json()
    assert resp["success"] is True
    assert resp["auto_monitored"] == 0 and resp["mirrored"] == 0
    with _conn(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM lib2_tracks WHERE monitored=1").fetchone()[0] == 0
    assert db.wishlist_adds == []


def test_profile_assign_skips_consolidated_duplicates(api):
    """With the explicit monitor-existing opt-in, an upgrade-policy profile
    monitors the artist's tracks — but not a consolidated-away duplicate (no
    file, canonical partner owns the file)."""
    client, db, ids = api
    resp = client.post(
        f"/api/library/v2/artists/{ids['artist']}/quality-profile",
        json={"quality_profile_id": 2, "monitor_existing": True},  # seeded 'until_cutoff' profile
    ).get_json()
    assert resp["success"] is True
    with _conn(db) as conn:
        monitored = {r["id"]: r["monitored"] for r in conn.execute(
            "SELECT id, monitored FROM lib2_tracks")}
    assert monitored[ids["album_track"]] == 1
    assert monitored[ids["ep_track"]] == 1
    assert monitored[ids["single_track"]] == 0, (
        "the consolidated single variant must not be re-wanted")
    queued = {a["id"] for a in db.wishlist_adds}
    from core.library2.stable_ids import ensure_track_stable_id
    with _conn(db) as conn:
        single_stable = ensure_track_stable_id(conn, ids["single_track"])
    assert f"lib2-track:{single_stable}" not in queued


def test_delete_artist_removes_rows_mirrors_and_artwork(api):
    client, db, ids = api
    # Cached artwork that must disappear with the entity.
    from core.library2.artwork import artwork_file, thumb_file
    art = artwork_file(db, "artist", ids["artist"])
    art.write_bytes(b"jpg")
    thumb = thumb_file(db, "album", ids["views"])
    thumb.write_bytes(b"jpg")

    resp = client.delete(f"/api/library/v2/artists/{ids['artist']}").get_json()
    assert resp["success"] is True
    with _conn(db) as conn:
        for table in ("lib2_artists", "lib2_albums", "lib2_tracks", "lib2_track_files"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    # Wishlist withdrawals went out for the artist's tracks; watchlist too.
    assert db.wishlist_removes
    assert db.watchlist_removes and db.watchlist_removes[0]["ext_id"] == "sp-drake"
    assert not art.exists()
    assert not thumb.exists()


def test_delete_featured_artist_keeps_owner_album(api):
    """Audit P0-01: deleting an artist who is merely featured on another
    artist's album must NOT delete that album — only the credit rows."""
    client, db, ids = api
    with _conn(db) as conn:
        cur = conn.execute(
            "INSERT INTO lib2_artists(name, spotify_id) VALUES('Wizkid','sp-wizkid')")
        wizkid = cur.lastrowid
        conn.execute(
            "INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,?,'featured')",
            (ids["views"], wizkid))
        conn.execute(
            "INSERT INTO lib2_track_artists(track_id, artist_id, role) VALUES(?,?,'featured')",
            (ids["album_track"], wizkid))
        conn.commit()

    # Preview shows the real blast radius: nothing owned, one detachment.
    preview = client.get(f"/api/library/v2/artists/{wizkid}/delete-preview").get_json()
    assert preview["success"] is True
    assert preview["albums"] == 0 and preview["tracks"] == 0
    assert preview["detached_albums"] == 1

    resp = client.delete(f"/api/library/v2/artists/{wizkid}").get_json()
    assert resp["success"] is True
    assert resp["albums"] == 0 and resp["detached_albums"] == 1

    with _conn(db) as conn:
        # Drake's album, tracks and file links all survive.
        assert conn.execute("SELECT COUNT(*) FROM lib2_albums WHERE id=?",
                            (ids["views"],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?",
                            (ids["views"],)).fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM lib2_track_files WHERE track_id=?",
                            (ids["album_track"],)).fetchone()[0] == 1
        # Only the credit rows are gone.
        assert conn.execute("SELECT COUNT(*) FROM lib2_album_artists WHERE artist_id=?",
                            (wizkid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM lib2_track_artists WHERE artist_id=?",
                            (wizkid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM lib2_artists WHERE id=?",
                            (wizkid,)).fetchone()[0] == 0
    # No wishlist withdrawals for the surviving album's tracks.
    assert not db.wishlist_removes


def test_artist_delete_preview_for_primary_artist(api):
    client, _db, ids = api
    preview = client.get(f"/api/library/v2/artists/{ids['artist']}/delete-preview").get_json()
    assert preview["success"] is True
    assert preview["albums"] == 3 and preview["tracks"] == 3
    assert preview["file_links"] == 1 and preview["detached_albums"] == 0
    missing = client.get("/api/library/v2/artists/999999/delete-preview")
    assert missing.status_code == 404


def test_physical_file_delete_preview_is_separate_and_root_safe(api, tmp_path, monkeypatch):
    client, db, ids = api
    root = tmp_path / "music"
    root.mkdir()
    path = root / "one-dance.flac"
    path.write_bytes(b"audio")
    with _conn(db) as conn:
        conn.execute(
            "UPDATE lib2_track_files SET path=? WHERE track_id=?",
            (str(path), ids["album_track"]),
        )
        conn.commit()
    monkeypatch.setattr(
        "core.library2.file_delete._library_roots", lambda _config=None: [str(root)]
    )

    preview = client.get(
        f"/api/library/v2/albums/{ids['views']}/file-delete-preview"
    ).get_json()

    assert preview["success"] is True
    assert preview["deletable_count"] == 1
    assert preview["unsafe_count"] == 0
    assert preview["files"][0]["path"] == str(path)
    assert path.exists(), "preview must never mutate the filesystem"

    executed = client.post(
        f"/api/library/v2/albums/{ids['views']}/file-delete",
        json={"preview_token": preview["preview_token"]},
    ).get_json()
    assert executed["success"] is True
    assert executed["operation"]["status"] == "completed"
    assert not path.exists()
    with _conn(db) as conn:
        assert conn.execute(
            "SELECT file_state FROM lib2_track_files WHERE track_id=?",
            (ids["album_track"],),
        ).fetchone()[0] == "deleted"
        assert conn.execute(
            "SELECT 1 FROM lib2_albums WHERE id=?", (ids["views"],)
        ).fetchone()


def test_non_admin_profile_writes_are_rejected(api):
    """ADR-01 (admin-only, technically enforced): lib2 mutations from any
    profile but the admin are rejected with 403 — not silently applied to the
    global monitored columns and mirrored into the wrong profile's wishlist
    (audit P0-02). Reads stay available to every profile."""
    client, db, ids = api
    db.active_profile = 7  # non-admin household profile

    for method, url, body in (
        ("post", f"/api/library/v2/albums/{ids['ep']}/monitor", {"monitored": True}),
        ("post", f"/api/library/v2/artists/{ids['artist']}/quality-profile",
         {"quality_profile_id": 2}),
        ("post", f"/api/library/v2/albums/{ids['single']}/edit", {"album_type": "ep"}),
        ("put", f"/api/library/v2/metadata-overrides/release_group/"
                f"{ids['single']}/title", {"value": "Nope"}),
        ("patch", f"/api/library/v2/metadata-overrides/release_group/"
                  f"{ids['single']}", {"set": {"title": "Nope"}}),
        ("delete", f"/api/library/v2/artists/{ids['artist']}", None),
        ("post", "/api/library/v2/import", {}),
    ):
        resp = getattr(client, method)(url, json=body) if body is not None else \
            getattr(client, method)(url)
        assert resp.status_code == 403, f"{method.upper()} {url} must be admin-only"
        assert "admin" in (resp.get_json() or {}).get("error", "").lower()

    with _conn(db) as conn:
        # Nothing changed, nothing mirrored.
        assert conn.execute(
            "SELECT COUNT(*) FROM lib2_tracks WHERE monitored=1").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM lib2_artists").fetchone()[0] == 1
    assert db.wishlist_adds == [] and db.wishlist_removes == []

    # Reads still work for the non-admin profile.
    resp = client.get(f"/api/library/v2/artists/{ids['artist']}")
    assert resp.status_code == 200 and resp.get_json()["success"] is True


def test_import_is_hard_limited_to_admin_profile(legacy_db=None):
    """ADR-01: the importer derives GLOBAL monitored flags from one profile's
    watchlist/wishlist — any profile but the admin must be refused."""
    import pytest as _pytest
    from core.library2.importer import import_legacy_library

    with _pytest.raises(ValueError, match="admin-only"):
        import_legacy_library(None, profile_id=7)


def test_artist_list_rejects_non_numeric_page(api):
    client, _db, _ids = api
    resp = client.get("/api/library/v2/artists?page=abc")
    assert resp.status_code == 400


def test_album_edit_refiles_release_type(api):
    client, db, ids = api
    resp = client.post(f"/api/library/v2/albums/{ids['single']}/edit",
                       json={"album_type": "ep"}).get_json()
    assert resp["success"] is True and resp["album_type"] == "ep"
    with _conn(db) as conn:
        # Provider/import baseline is provenance, not a user-edit scratchpad.
        assert conn.execute("SELECT album_type FROM lib2_albums WHERE id=?",
                            (ids["single"],)).fetchone()[0] == "single"
        assert conn.execute(
            "SELECT value_json FROM lib2_metadata_overrides "
            "WHERE entity_type='release_group' AND entity_id=? "
            "AND field_name='album_type'",
            (ids["single"],),
        ).fetchone()[0] == '"ep"'
    detail = client.get(f"/api/library/v2/artists/{ids['artist']}").get_json()["artist"]
    assert ids["single"] in {album["id"] for album in detail["eps"]}
    assert ids["single"] not in {album["id"] for album in detail["singles"]}
    bad = client.post(f"/api/library/v2/albums/{ids['single']}/edit",
                      json={"album_type": "mixtape"})
    assert bad.status_code == 400


def test_generic_metadata_override_set_project_and_clear(api):
    client, db, ids = api
    url = f"/api/library/v2/metadata-overrides/release_group/{ids['views']}/title"
    response = client.put(url, json={
        "value": "Views (Corrected)",
        "reason": "admin correction",
    })
    assert response.status_code == 200
    assert response.get_json()["override"]["value"] == "Views (Corrected)"
    assert client.get(f"/api/library/v2/albums/{ids['views']}").get_json()[
        "album"
    ]["title"] == "Views (Corrected)"
    with _conn(db) as conn:
        assert conn.execute(
            "SELECT title FROM lib2_albums WHERE id=?", (ids["views"],)
        ).fetchone()[0] == "Views"

    assert client.delete(url).get_json() == {"removed": True, "success": True}
    assert client.get(f"/api/library/v2/albums/{ids['views']}").get_json()[
        "album"
    ]["title"] == "Views"


def test_generic_metadata_override_validates_payload_and_field(api):
    client, _db, ids = api
    base = f"/api/library/v2/metadata-overrides/release_group/{ids['views']}"
    assert client.put(f"{base}/title", json={}).status_code == 400
    assert client.put(f"{base}/unknown", json={"value": "x"}).status_code == 400
    assert client.put(
        "/api/library/v2/metadata-overrides/track/999999/title",
        json={"value": "x"},
    ).status_code == 404


def test_metadata_override_batch_is_atomic_and_can_clear(api):
    client, db, ids = api
    base = f"/api/library/v2/metadata-overrides/release_group/{ids['views']}"
    baseline = client.get(
        f"/api/library/v2/albums/{ids['views']}"
    ).get_json()["album"]
    response = client.patch(base, json={
        "set": {"title": "Views (Deluxe)", "year": 2024},
    })
    assert response.status_code == 200
    assert response.get_json()["overrides"] == {
        "title": "Views (Deluxe)",
        "year": 2024,
    }
    album = client.get(f"/api/library/v2/albums/{ids['views']}").get_json()["album"]
    assert (album["title"], album["year"]) == ("Views (Deluxe)", 2024)

    failed = client.patch(base, json={
        "set": {"album_type": "ep", "not_editable": "x"},
    })
    assert failed.status_code == 400
    with _conn(db) as conn:
        assert conn.execute(
            "SELECT 1 FROM lib2_metadata_overrides "
            "WHERE entity_type='release_group' AND entity_id=? "
            "AND field_name='album_type'",
            (ids["views"],),
        ).fetchone() is None

    cleared = client.patch(base, json={"clear": ["title", "year"]})
    assert cleared.status_code == 200
    assert cleared.get_json()["overrides"] == {}
    album = client.get(f"/api/library/v2/albums/{ids['views']}").get_json()["album"]
    assert (album["title"], album["year"]) == (baseline["title"], baseline["year"])


def test_metadata_override_batch_validates_shape_and_overlap(api):
    client, _db, ids = api
    base = f"/api/library/v2/metadata-overrides/artist/{ids['artist']}"
    assert client.patch(base, json={}).status_code == 400
    assert client.patch(base, json={"set": [], "clear": []}).status_code == 400
    assert client.patch(base, json={
        "set": {"name": "Corrected"},
        "clear": ["name"],
    }).status_code == 400


def test_refresh_unknown_entity_is_404(api):
    """An unknown id must be a 404 — not a success whose empty album scope
    silently widens into a full-library rescan (audit P1-08)."""
    client, _db, _ids = api
    resp = client.post("/api/library/v2/artists/999999/refresh")
    assert resp.status_code == 404
    resp = client.post("/api/library/v2/albums/999999/refresh")
    assert resp.status_code == 404


def test_refresh_artist_without_albums_scans_nothing(api):
    client, db, _ids = api
    with _conn(db) as conn:
        cur = conn.execute("INSERT INTO lib2_artists(name) VALUES('Empty Artist')")
        empty_artist = cur.lastrowid
        conn.commit()
    resp = client.post(f"/api/library/v2/artists/{empty_artist}/refresh").get_json()
    assert resp["success"] is True
    assert resp["refreshed_albums"] == 0
    assert resp["scan"].get("scanned") == 0


def test_refresh_busts_full_artwork_and_thumbnails(api):
    """Refresh must invalidate BOTH cached variants — the thumb wins the serve
    fast path, so a stale one would pin the old cover in lists forever."""
    client, db, ids = api
    from core.library2.artwork import artwork_file, thumb_file
    files = [
        artwork_file(db, "artist", ids["artist"]),
        thumb_file(db, "artist", ids["artist"]),
        artwork_file(db, "album", ids["views"]),
        thumb_file(db, "album", ids["views"]),
    ]
    for f in files:
        f.write_bytes(b"jpg")
    resp = client.post(f"/api/library/v2/artists/{ids['artist']}/refresh").get_json()
    assert resp["success"] is True
    for f in files:
        assert not f.exists(), f"{f.name} must be invalidated by refresh"


def test_artwork_route_serves_real_jpeg_with_matching_mime(api):
    client, db, ids = api
    from PIL import Image
    from core.library2.artwork import artwork_file

    cached = artwork_file(db, "album", ids["views"])
    Image.new("RGB", (3, 2), "blue").save(cached, "JPEG")

    response = client.get(f"/api/library/v2/artwork/album/{ids['views']}")

    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"
    with Image.open(BytesIO(response.data)) as image:
        assert image.format == "JPEG"
