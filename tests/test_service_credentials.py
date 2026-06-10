"""Phase 0: named, switchable service-credential sets.

Foundation for letting an admin save multiple named credential sets per auth
service ("pills") that each profile can switch between. These cover the PURE
selection/validation logic and the encrypted DB storage + per-profile selection
+ resolver — with real temp databases (not mocks) so encryption round-trips and
the stale-selection fallback are genuinely exercised.

This layer is dormant (nothing reads it at runtime yet), so it can't regress
existing behaviour — the tests pin the contract the later wiring will rely on.
"""

from __future__ import annotations

import pytest

from core.credentials.store import (
    SUPPORTED_SERVICES,
    is_supported_service,
    validate_credential_payload,
    pick_active_credential,
)
from database.music_database import MusicDatabase


# ── pure: validation ────────────────────────────────────────────────────────

def test_supported_services_cover_the_auth_sources():
    for s in ('spotify', 'tidal', 'deezer', 'qobuz', 'plex', 'jellyfin', 'navidrome'):
        assert is_supported_service(s)
    assert not is_supported_service('itunes')      # no auth → not a credential service
    assert not is_supported_service('musicbrainz')


def test_validate_payload_ok_and_missing():
    ok, missing = validate_credential_payload('spotify', {'client_id': 'a', 'client_secret': 'b'})
    assert ok and missing == []
    ok, missing = validate_credential_payload('spotify', {'client_id': 'a'})
    assert not ok and missing == ['client_secret']


def test_validate_payload_unknown_service_and_non_dict():
    assert validate_credential_payload('nope', {'x': 1}) == (False, [])
    ok, missing = validate_credential_payload('plex', None)
    assert not ok and set(missing) == {'base_url', 'token'}


def test_validate_treats_empty_string_as_missing():
    ok, missing = validate_credential_payload('navidrome',
                                              {'base_url': 'http://x', 'username': '', 'password': 'p'})
    assert not ok and missing == ['username']


# ── pure: active-set selection (stale-safe) ──────────────────────────────────

def test_pick_active_credential_match_and_misses():
    creds = [{'id': 1, 'label': 'A'}, {'id': 2, 'label': 'B'}]
    assert pick_active_credential(creds, 2)['label'] == 'B'
    assert pick_active_credential(creds, None) is None      # no selection
    assert pick_active_credential(creds, 99) is None        # stale id (set deleted)
    assert pick_active_credential([], 1) is None
    assert pick_active_credential(None, 1) is None


# ── DB: storage + selection + resolver (real temp DB, real encryption) ───────

@pytest.fixture
def db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / 'creds.db'))


def test_create_get_roundtrip_encrypts_payload(db, tmp_path):
    cid = db.create_service_credential('spotify', "Brock's Spotify",
                                       {'client_id': 'abc', 'client_secret': 'sek'})
    assert cid
    got = db.get_service_credential(cid)
    assert got['service'] == 'spotify' and got['label'] == "Brock's Spotify"
    assert got['payload'] == {'client_id': 'abc', 'client_secret': 'sek'}
    # The on-disk payload must be ciphertext, never the plaintext secret.
    import sqlite3
    raw = sqlite3.connect(str(tmp_path / 'creds.db')).execute(
        "SELECT payload FROM service_credentials WHERE id = ?", (cid,)).fetchone()[0]
    assert 'sek' not in raw and raw.startswith('gAAAAA')


def test_duplicate_label_per_service_rejected(db):
    assert db.create_service_credential('spotify', 'Main', {'client_id': 'a', 'client_secret': 'b'})
    assert db.create_service_credential('spotify', 'Main', {'client_id': 'c', 'client_secret': 'd'}) is None
    # same label is fine under a DIFFERENT service
    assert db.create_service_credential('tidal', 'Main', {'access_token': 't', 'refresh_token': 'r'})


def test_list_never_exposes_payload(db):
    db.create_service_credential('spotify', 'One', {'client_id': 'a', 'client_secret': 'b'})
    db.create_service_credential('deezer', 'Two', {'arl': 'xyz'})
    rows = db.list_service_credentials()
    assert {r['label'] for r in rows} == {'One', 'Two'}
    assert all('payload' not in r for r in rows)
    assert [r['label'] for r in db.list_service_credentials('deezer')] == ['Two']


def test_update_label_and_payload(db):
    cid = db.create_service_credential('qobuz', 'Q', {'user_auth_token': 'tok'})
    assert db.update_service_credential(cid, label='Q renamed')
    assert db.update_service_credential(cid, payload={'user_auth_token': 'newtok'})
    got = db.get_service_credential(cid)
    assert got['label'] == 'Q renamed' and got['payload']['user_auth_token'] == 'newtok'


def test_profile_selection_resolves_and_falls_back(db):
    cid = db.create_service_credential('spotify', 'Shared', {'client_id': 'a', 'client_secret': 'b'})
    # No selection → None (caller uses global default)
    assert db.resolve_profile_service_credential(7, 'spotify') is None
    db.set_profile_service_credential(7, 'spotify', cid)
    assert db.resolve_profile_service_credential(7, 'spotify') == {'client_id': 'a', 'client_secret': 'b'}
    # Clearing the selection falls back again
    db.set_profile_service_credential(7, 'spotify', None)
    assert db.resolve_profile_service_credential(7, 'spotify') is None


def test_delete_clears_selections_and_resolves_to_fallback(db):
    cid = db.create_service_credential('spotify', 'Temp', {'client_id': 'a', 'client_secret': 'b'})
    db.set_profile_service_credential(2, 'spotify', cid)
    db.set_profile_service_credential(9, 'spotify', cid)
    assert db.delete_service_credential(cid)
    # Both profiles' dangling selections resolve to None, not an error.
    assert db.resolve_profile_service_credential(2, 'spotify') is None
    assert db.resolve_profile_service_credential(9, 'spotify') is None
    assert db.get_service_credential(cid) is None


def test_selection_is_per_profile_and_per_service(db):
    sp = db.create_service_credential('spotify', 'SP', {'client_id': 'a', 'client_secret': 'b'})
    td = db.create_service_credential('tidal', 'TD', {'access_token': 't', 'refresh_token': 'r'})
    db.set_profile_service_credential(1, 'spotify', sp)
    db.set_profile_service_credential(1, 'tidal', td)
    assert db.resolve_profile_service_credential(1, 'spotify')['client_id'] == 'a'
    assert db.resolve_profile_service_credential(1, 'tidal')['access_token'] == 't'
    # A different profile shares the pool but has its own (empty) selection.
    assert db.resolve_profile_service_credential(2, 'spotify') is None
