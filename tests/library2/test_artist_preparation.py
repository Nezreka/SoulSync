from types import SimpleNamespace

from core.library2 import post_import, native_enrich
from core.library2 import bootstrap


def test_artist_reconcile_finishes_before_post_import_caches(monkeypatch):
    calls = []
    class Connection:
        def commit(self):
            calls.append('commit')
        def close(self):
            pass
    db = SimpleNamespace(_get_connection=lambda: Connection())
    def reconcile(conn, *, progress, cooldown_hours):
        calls.append('reconcile')
        progress(1, 1)
        return {'split': 1}
    monkeypatch.setattr(native_enrich, 'reconcile_unmapped_native_artists', reconcile)
    monkeypatch.setattr('core.library2.completeness.precache_tracklists', lambda *a, **k: calls.append('tracklists'))
    monkeypatch.setattr('core.library2.tag_cache.precache_tag_cache', lambda *a, **k: None)
    monkeypatch.setattr('core.library2.artist_rollup.refresh_artist_rollup', lambda *a, **k: None)
    post_import.run_post_import_precache(db, None)
    assert calls[0] == 'reconcile'
    assert calls.index('commit') < calls.index('tracklists')


def test_failed_artist_preparation_cannot_mark_migration_done(legacy_db):
    def prepare(progress):
        raise post_import.ArtistPreparationError('Could not prepare artist credits')
    result = bootstrap.run_bootstrap_if_needed(legacy_db, lambda *a: True, post_import=prepare)
    assert result['success'] is False
    assert bootstrap.get_state(legacy_db)['status'] == 'failed'
