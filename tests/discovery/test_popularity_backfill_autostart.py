"""S7: the popularity-backfill autostart must serve every profile, not just
profile 1. A single-tick helper makes this testable without sleeping."""

from __future__ import annotations

import pytest

import api.discover_routes as routes
import core.discovery.popularity_backfill as pb


class _FakeDb:
    def __init__(self, profiles, missing):
        self._profiles = profiles
        self._missing = dict(missing)

    def get_all_profiles(self):
        return self._profiles

    def count_similar_artists_missing_popularity(self, profile_id=1):
        return self._missing.get(profile_id, 0)


@pytest.fixture()
def tick_env(monkeypatch):
    calls = []
    monkeypatch.setattr(pb, 'is_running', lambda: False)
    monkeypatch.setattr(
        pb, 'run_backfill',
        lambda database, **kw: calls.append(kw.get('profile_id')),
    )
    monkeypatch.setattr(routes, '_resolve_popularity_sources', lambda: ('sf', None, None))
    return calls


def test_tick_fills_profile_with_missing_even_when_profile_1_is_clean(monkeypatch, tick_env):
    """S7 core: profile 2 has gaps, profile 1 is clean — the tick must fill
    profile 2 (the old code only ever looked at profile 1)."""
    db = _FakeDb(
        profiles=[{'id': 1, 'name': 'Admin'}, {'id': 2, 'name': 'Kid'}],
        missing={1: 0, 2: 5},
    )
    monkeypatch.setattr(routes, 'get_database', lambda: db)

    routes._popularity_backfill_tick()

    assert tick_env == [2]


def test_tick_fills_every_profile_with_gaps(monkeypatch, tick_env):
    db = _FakeDb(
        profiles=[{'id': 1, 'name': 'Admin'}, {'id': 2, 'name': 'Kid'}],
        missing={1: 3, 2: 5},
    )
    monkeypatch.setattr(routes, 'get_database', lambda: db)

    routes._popularity_backfill_tick()

    assert tick_env == [1, 2]


def test_tick_skips_backfill_when_another_run_is_active(monkeypatch, tick_env):
    db = _FakeDb(profiles=[{'id': 1, 'name': 'Admin'}], missing={1: 9})
    monkeypatch.setattr(routes, 'get_database', lambda: db)
    monkeypatch.setattr(pb, 'is_running', lambda: True)

    routes._popularity_backfill_tick()

    assert tick_env == []


def test_tick_does_nothing_when_no_source_configured(monkeypatch, tick_env):
    db = _FakeDb(profiles=[{'id': 1, 'name': 'Admin'}], missing={1: 9})
    monkeypatch.setattr(routes, 'get_database', lambda: db)
    monkeypatch.setattr(routes, '_resolve_popularity_sources', lambda: (None, None, None))

    routes._popularity_backfill_tick()

    assert tick_env == []
