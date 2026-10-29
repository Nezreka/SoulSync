"""Cached/fresh parity for solver examples that explicitly request it."""
import sqlite3
from types import SimpleNamespace

import pytest

from core.reconciliation.match_cache import MatchingCache
from core.reconciliation.plan import (
    reconcile_folder,
)


@pytest.fixture(params=['fresh', 'persisted'])
def matching_parity(request, monkeypatch, tmp_path):
    """Run every real-world matcher regression through a persisted round trip."""
    if request.param == 'fresh':
        return
    original = reconcile_folder
    db = SimpleNamespace(_get_connection=lambda: sqlite3.connect(tmp_path / 'matching.db'))

    def checked(files, slots, forced_pairs=None):
        expected = original(files, slots, forced_pairs=forced_pairs)
        cold = MatchingCache(db, 'shadow')
        assert cold.match(files, slots, forced_pairs, 'regression', original) == expected
        warm = MatchingCache(db, 'reuse')
        actual = warm.match(files, slots, forced_pairs, 'regression', original)
        assert actual == expected
        assert not cold.counts['cache_errors'] and not warm.counts['cache_errors']
        if all((f.get('acoustid') or {}).get('status') in ('ok', 'no_match') for f in files):
            assert warm.counts['reused'] == 1
        return actual

    monkeypatch.setattr(request.module, 'reconcile_folder', checked)
