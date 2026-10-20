"""Pagination for GET /api/quarantine/list (#1775)."""
import json
from types import SimpleNamespace

import pytest
from flask import Flask

import api.quarantine as routes


@pytest.fixture
def env(tmp_path, monkeypatch):
    quarantine = tmp_path / 'ss_quarantine'
    quarantine.mkdir()
    settings = {'soulseek.download_path': str(tmp_path)}
    config = SimpleNamespace(get=lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(routes, 'config_manager', config)
    monkeypatch.setattr(routes, 'docker_resolve_path', lambda p: p)
    for i in range(5):
        name = f'q{i}'
        (quarantine / f'{name}.flac.quarantined').write_bytes(b'bad audio')
        (quarantine / f'{name}.json').write_text(json.dumps({
            'expected_track': f'Track {i}', 'expected_artist': 'Artist',
            'original_filename': f'{name}.flac',
            'trigger': 'acoustid_unverified', 'quarantine_reason': 'no match',
            'timestamp': f'2026-01-0{i + 1}T00:00:00Z',
        }))
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    return SimpleNamespace(client=app.test_client(), quarantine=quarantine)


def test_no_params_returns_all_and_total(env):
    response = env.client.get('/api/quarantine/list')
    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True
    assert len(data['entries']) == 5
    assert data['total'] == 5
    assert 'limit' not in data
    assert 'offset' not in data


def test_direct_paged_list_reads_only_window_sidecars(env, monkeypatch):
    import core.imports.quarantine as quarantine_mod

    real_load = quarantine_mod.json.load
    parsed = []

    def counting_load(f, *args, **kwargs):
        parsed.append(1)
        return real_load(f, *args, **kwargs)

    monkeypatch.setattr(quarantine_mod.json, 'load', counting_load)
    entries = quarantine_mod.list_quarantine_entries(
        str(env.quarantine), limit=2, offset=1)
    # ids sort descending (q4..q0), so offset 1 / limit 2 is the middle two.
    assert [e['id'] for e in entries] == ['q3', 'q2']
    # Only the window's sidecars were parsed — strictly fewer than the
    # five sidecars present on disk.
    assert len(parsed) <= 2
    assert len(parsed) < 5


def test_paginated_total_matches_count(env):
    from core.imports.quarantine import count_quarantine_entries
    response = env.client.get('/api/quarantine/list?limit=2&offset=0')
    assert response.status_code == 200
    data = response.get_json()
    assert data['total'] == count_quarantine_entries(str(env.quarantine)) == 5


@pytest.mark.parametrize('query', [
    'limit=%C2%B2',  # '²': isdigit() is True but it is not ASCII
    'limit=',
    'limit=' + '9' * 5000,
])
def test_malformed_limit_is_400(env, query):
    response = env.client.get(f'/api/quarantine/list?{query}')
    assert response.status_code == 400
    assert response.get_json()['success'] is False


def test_limit_and_offset(env):
    response = env.client.get('/api/quarantine/list?limit=2&offset=0')
    assert response.status_code == 200
    data = response.get_json()
    assert len(data['entries']) == 2
    assert data['total'] == 5
    assert data['limit'] == 2
    assert data['offset'] == 0


def test_offset_near_end_returns_remainder(env):
    response = env.client.get('/api/quarantine/list?offset=4&limit=2')
    assert response.status_code == 200
    data = response.get_json()
    assert len(data['entries']) == 1
    assert data['total'] == 5


@pytest.mark.parametrize('query', ['limit=0', 'limit=abc'])
def test_invalid_limit_is_400(env, query):
    response = env.client.get(f'/api/quarantine/list?{query}')
    assert response.status_code == 400
    assert response.get_json()['success'] is False


def test_invalid_offset_is_400(env):
    response = env.client.get('/api/quarantine/list?limit=2&offset=-1')
    assert response.status_code == 400
    assert response.get_json()['success'] is False


def _all_ids(env):
    data = env.client.get('/api/quarantine/list').get_json()
    return [e['id'] for e in data['entries']]


def test_offset_window_returns_expected_ids_in_order(env):
    all_ids = _all_ids(env)
    response = env.client.get('/api/quarantine/list?limit=2&offset=1')
    assert response.status_code == 200
    data = response.get_json()
    assert [e['id'] for e in data['entries']] == all_ids[1:3]
    assert data['total'] == 5


def test_offset_beyond_end_returns_empty_with_total(env):
    response = env.client.get('/api/quarantine/list?limit=2&offset=99')
    assert response.status_code == 200
    data = response.get_json()
    assert data['entries'] == []
    assert data['total'] == 5


def test_limit_alone_returns_first_page(env):
    all_ids = _all_ids(env)
    response = env.client.get('/api/quarantine/list?limit=2')
    assert response.status_code == 200
    data = response.get_json()
    assert [e['id'] for e in data['entries']] == all_ids[:2]
    assert data['limit'] == 2
    assert data['offset'] == 0
    assert data['total'] == 5


def test_offset_alone_slices_and_annotates_only_slice(env, monkeypatch):
    all_ids = _all_ids(env)
    seen = []

    def record(entries, quarantine_dir, config):
        seen.append([e['id'] for e in entries])

    monkeypatch.setattr(
        'core.imports.quarantine_library.annotate_quarantine_library', record)
    response = env.client.get('/api/quarantine/list?offset=3')
    assert response.status_code == 200
    data = response.get_json()
    assert [e['id'] for e in data['entries']] == all_ids[3:]
    assert data['total'] == 5
    assert data['offset'] == 3
    assert 'limit' not in data
    assert seen == [all_ids[3:]]


def test_offset_abc_is_400(env):
    response = env.client.get('/api/quarantine/list?offset=abc')
    assert response.status_code == 400
    assert response.get_json()['success'] is False


def test_negative_limit_is_400(env):
    response = env.client.get('/api/quarantine/list?limit=-1')
    assert response.status_code == 400
    assert response.get_json()['success'] is False


def test_huge_limit_is_clamped_to_500(env):
    response = env.client.get('/api/quarantine/list?limit=99999')
    assert response.status_code == 200
    data = response.get_json()
    assert data['limit'] == 500
    assert len(data['entries']) == data['total'] == 5


def test_slice_before_annotate(env, monkeypatch):
    seen = []

    def record(entries, quarantine_dir, config):
        seen.append([e['id'] for e in entries])

    monkeypatch.setattr(
        'core.imports.quarantine_library.annotate_quarantine_library', record)
    response = env.client.get('/api/quarantine/list?limit=2&offset=0')
    assert response.status_code == 200
    data = response.get_json()
    returned_ids = [e['id'] for e in data['entries']]
    assert len(returned_ids) == 2
    assert seen == [returned_ids]
