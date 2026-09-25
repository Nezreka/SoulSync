"""Admin API boundaries for LV2-006 and LV2-013."""

from __future__ import annotations

import pytest


flask = pytest.importorskip("flask")


def _api(*, profile=1):
    state = {"profile": profile}
    calls = {"acquisition": [], "integrity": []}
    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes

    def _acquisition(*, dry_run):
        calls["acquisition"].append(dry_run)
        return {"dry_run": dry_run, "observed": 2, "counts": {"awaiting_evidence": 2}}

    def _integrity(*, max_findings):
        calls["integrity"].append(max_findings)
        return {"read_only": True, "findings": [], "findings_total": 0}

    register_library_v2_routes(
        app,
        get_database=lambda: object(),
        config_get=lambda key, default=None: (
            True if key == "features.library_v2" else default
        ),
        profile_id_getter=lambda: state["profile"],
        acquisition_reconciliation_runner=_acquisition,
        integrity_report_runner=_integrity,
    )
    return app.test_client(), state, calls


def test_acquisition_get_is_admin_dry_run():
    client, _, calls = _api()

    response = client.get("/api/library/v2/maintenance/reconcile-acquisition")

    assert response.status_code == 200
    assert response.get_json()["report"]["dry_run"] is True
    assert calls["acquisition"] == [True]


def test_acquisition_post_only_applies_with_explicit_true():
    client, _, calls = _api()

    preview = client.post(
        "/api/library/v2/maintenance/reconcile-acquisition", json={"apply": False},
    )
    applied = client.post(
        "/api/library/v2/maintenance/reconcile-acquisition", json={"apply": True},
    )

    assert preview.get_json()["report"]["dry_run"] is True
    assert applied.get_json()["report"]["dry_run"] is False
    assert calls["acquisition"] == [True, False]


def test_read_only_maintenance_endpoints_are_admin_only():
    client, _, calls = _api(profile=2)

    acquisition = client.get(
        "/api/library/v2/maintenance/reconcile-acquisition",
    )
    integrity = client.get("/api/library/v2/maintenance/integrity-report")

    assert acquisition.status_code == 403
    assert integrity.status_code == 403
    assert calls == {"acquisition": [], "integrity": []}


def test_integrity_report_is_bounded_and_read_only():
    client, _, calls = _api()

    response = client.get(
        "/api/library/v2/maintenance/integrity-report?max_findings=120",
    )

    assert response.status_code == 200
    assert response.get_json()["report"]["read_only"] is True
    assert calls["integrity"] == [120]


def test_integrity_report_rejects_invalid_bound():
    client, _, calls = _api()

    response = client.get(
        "/api/library/v2/maintenance/integrity-report?max_findings=lots",
    )

    assert response.status_code == 400
    assert calls["integrity"] == []
