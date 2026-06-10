"""Phase 3: server-side admin gating of shared/global-destructive endpoints.

The audit found these were callable by any profile (UI hid them, the API didn't).
For a real multi-user setup that's unsafe — a non-admin could restore/vacuum the
DB, wipe the shared library, clear the Plex library, or mint API keys. These
assert the @admin_only gate now blocks non-admins, that admin is NOT blocked
(zero change for single-profile installs, where everyone is the default admin),
and crucially that a PROFILE-SCOPED op (clearing your OWN wishlist) was NOT
over-gated.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='ss-admin-gate-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'gate.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


# (method, path) for every endpoint that must be admin-only.
GATED = [
    ('GET', '/api/v1/api-keys-internal'),
    ('POST', '/api/v1/api-keys-internal/generate'),
    ('DELETE', '/api/v1/api-keys-internal/revoke/abc'),
    ('POST', '/api/plex/clear-library'),
    ('PUT', '/api/library/clear-match'),
    ('DELETE', '/api/library/track/123'),
    ('DELETE', '/api/library/album/123'),
    ('POST', '/api/library/tracks/delete-batch'),
    ('POST', '/api/database/update'),
    ('POST', '/api/database/update/stop'),
    ('POST', '/api/database/backup'),
    ('DELETE', '/api/database/backups/x.db'),
    ('POST', '/api/database/backups/x.db/restore'),
    ('POST', '/api/database/maintenance/vacuum'),
    ('DELETE', '/api/metadata-cache/clear'),
    ('DELETE', '/api/metadata-cache/clear-musicbrainz'),
    ('POST', '/api/metadata-cache/evict'),
]


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def nonadmin(client):
    pid = web_server.get_database().create_profile(name=f'u_{os.urandom(3).hex()}')
    with client.session_transaction() as sess:
        sess['profile_id'] = pid
    return pid


def _call(client, method, path):
    return client.open(path, method=method, json={})


@pytest.mark.parametrize('method,path', GATED)
def test_nonadmin_blocked(client, nonadmin, method, path):
    # @admin_only returns 403 BEFORE the view body runs, so this never triggers
    # the underlying destructive operation — safe to assert across all of them.
    assert _call(client, method, path).status_code == 403, f"{method} {path} should be 403 for non-admin"


def test_admin_not_blocked_by_the_gate(client):
    # Default session = profile 1 (admin). Prove the gate lets admin through on a
    # SAFE, read-only gated endpoint (listing API keys) — confirming the no-change
    # guarantee for single-profile installs without triggering a destructive op.
    assert client.get('/api/v1/api-keys-internal').status_code != 403


def test_profile_scoped_wishlist_clear_not_overgated(client, nonadmin):
    # Clearing your OWN wishlist is profile-scoped data — a non-admin MUST still
    # be allowed. This is the guard against a blanket sweep.
    assert _call(client, 'POST', '/api/wishlist/clear').status_code != 403
