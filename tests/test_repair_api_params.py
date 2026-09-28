from __future__ import annotations

import pytest
from flask import Flask

import api.repair as repair_api


class MockWorker:
    def get_findings(self, **kwargs):
        return {
            'items': [],
            'total': 0,
            'page': kwargs.get('page', 0),
            'limit': kwargs.get('limit', 50),
        }

    def get_finding_albums(self, **kwargs):
        return []

    def get_history(self, **kwargs):
        return []


@pytest.fixture()
def client():
    app = Flask(__name__)
    mock_worker = MockWorker()
    repair_api.configure(
        worker_getter=lambda: mock_worker,
        image_url_fixer=lambda url: url,
        metadata_cache=None,
    )
    app.register_blueprint(repair_api.create_blueprint())
    return app.test_client()


def test_safe_int_helper():
    from api.repair import _safe_int
    assert _safe_int('undefined', 0) == 0
    assert _safe_int('null', 0) == 0
    assert _safe_int('nan', 50) == 50
    assert _safe_int(None, 10) == 10
    assert _safe_int('', 25) == 25
    assert _safe_int('0', 10) == 0
    assert _safe_int('42', 0) == 42
    assert _safe_int(42, 0) == 42
    assert _safe_int('abc', 100) == 100


def test_repair_findings_with_undefined_params(client):
    res = client.get('/api/repair/findings?page=undefined&limit=undefined')
    assert res.status_code == 200
    data = res.get_json()
    assert data['page'] == 0
    assert data['limit'] == 50


def test_repair_findings_albums_with_undefined_limit(client):
    res = client.get('/api/repair/findings/albums?group_by=album&limit=undefined')
    assert res.status_code == 200
    data = res.get_json()
    assert 'groups' in data


def test_repair_history_with_undefined_limit(client):
    res = client.get('/api/repair/history?limit=undefined')
    assert res.status_code == 200
    data = res.get_json()
    assert 'runs' in data
