"""Hub-level 'Retry all failed' (video parity) — one press re-queues every
not_found item across ALL music enrichment workers and entity types.

Contract: the sweep walks SERVICE_ENTITY_SUPPORT via the SAME
``reset_enrichment(service, entity, 'failed', None)`` the per-worker button
uses; a broken service is skipped, never fatal; the response reports the
total + a per-service breakdown (zero-services omitted).
"""

from __future__ import annotations

import pytest
from flask import Flask

import core.enrichment.api as eapi
from core.enrichment.unmatched import SERVICE_ENTITY_SUPPORT


class _FakeDb:
    def __init__(self):
        self.calls = []

    def reset_enrichment(self, service_id, entity_type, scope, entity_id):
        self.calls.append((service_id, entity_type, scope, entity_id))
        if service_id == 'discogs':
            raise RuntimeError('db locked')
        return 2 if service_id == 'spotify' else 0


@pytest.fixture()
def client():
    db = _FakeDb()
    eapi.configure(db_getter=lambda: db)
    app = Flask(__name__)
    app.register_blueprint(eapi.create_blueprint())
    try:
        yield app.test_client(), db
    finally:
        eapi.configure()


def test_sweep_hits_every_service_and_entity(client):
    http, db = client
    res = http.post('/api/enrichment/retry-all-failed')
    body = res.get_json()
    assert res.status_code == 200 and body['success']
    # every (service, entity) combination attempted, scope always 'failed'
    expected = {(s, e) for s, ents in SERVICE_ENTITY_SUPPORT.items() for e in ents}
    assert {(c[0], c[1]) for c in db.calls} == expected
    assert all(c[2] == 'failed' and c[3] is None for c in db.calls)


def test_broken_service_is_skipped_not_fatal(client):
    http, db = client
    body = http.post('/api/enrichment/retry-all-failed').get_json()
    # discogs raised for both its entities — the sweep still completed and
    # counted spotify's 3 entities × 2 resets
    assert body['reset'] == 6
    assert body['services'] == {'spotify': 6}      # zero/broken services omitted


def test_no_db_is_a_clean_503():
    eapi.configure()   # no db_getter
    app = Flask(__name__)
    app.register_blueprint(eapi.create_blueprint())
    assert app.test_client().post('/api/enrichment/retry-all-failed').status_code == 503
