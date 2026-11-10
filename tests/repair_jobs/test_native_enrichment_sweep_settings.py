from core.repair_jobs.base import JobContext
from core.repair_jobs.native_enrichment_sweep import (
    DEFAULT_BATCH,
    NativeEnrichmentSweepJob,
)


class _Config:
    def __init__(self, settings):
        self.settings = settings

    def get(self, key, default=None):
        if key == 'repair.jobs.native_enrichment_sweep.settings':
            return self.settings
        return default


def _context(settings):
    return JobContext(
        db=None,
        transfer_folder='/tmp',
        config_manager=_Config(settings),
    )


def test_batch_size_comes_from_the_persisted_job_settings():
    assert NativeEnrichmentSweepJob()._batch_size(_context({'batch_size': 17})) == 17


def test_batch_size_falls_back_for_invalid_or_boolean_values():
    job = NativeEnrichmentSweepJob()
    assert job._batch_size(_context({'batch_size': 'broken'})) == DEFAULT_BATCH
    assert job._batch_size(_context({'batch_size': True})) == DEFAULT_BATCH


def test_batch_size_does_not_require_an_undeclared_context_attribute():
    context = _context({})
    assert not hasattr(context, 'settings')
    assert NativeEnrichmentSweepJob()._batch_size(context) == DEFAULT_BATCH


def test_the_sweep_asks_the_registered_answer_instead_of_importing_the_app(monkeypatch):
    """L12: importing web_server from a job started the whole app."""
    import sys

    from core.library2 import match_status
    from core.repair_jobs.native_enrichment_sweep import _configured_services

    monkeypatch.setattr(match_status, "_configured_services_getter", lambda: {"deezer"})
    monkeypatch.delitem(sys.modules, "web_server", raising=False)
    assert _configured_services(None) == {"deezer"}
    assert "web_server" not in sys.modules


def test_unresolvable_artists_do_not_starve_the_rest(tmp_path, monkeypatch):
    """review 5 R5-I-7: a batch full of names no provider knows was retried on
    every run, so the artists behind it were never asked"""
    from types import SimpleNamespace
    from database.music_database import MusicDatabase
    from tests import lib2_seed
    import core.library2.native_enrich as ne
    import core.repair_jobs.native_enrichment_sweep as sweep

    db = MusicDatabase(str(tmp_path / 'e.db'))
    with db._get_connection() as conn:
        ids = [lib2_seed.artist(conn, name) for name in ('Unfindable A', 'Unfindable B', 'Findable C')]
        conn.commit()
    attempted = []

    def enrich(conn, kind, artist_id, **kw):
        attempted.append(artist_id)
        return {}
    monkeypatch.setattr(ne, 'enrich_native_entity_all_services', enrich)
    monkeypatch.setattr(sweep, '_configured_services', lambda cm: None)
    settings = {'batch_size': 2}
    cfg = SimpleNamespace(get=lambda k, d=None: settings if k.endswith('.settings') else d)
    sweep.NativeEnrichmentSweepJob().scan(JobContext(db, '/m', cfg))
    sweep.NativeEnrichmentSweepJob().scan(JobContext(db, '/m', cfg))
    assert attempted[:2] == ids[:2]
    assert attempted[2] == ids[2]
