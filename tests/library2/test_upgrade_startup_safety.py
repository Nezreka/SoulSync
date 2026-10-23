"""Startup must preserve a restorable database before changing its schema."""

from contextlib import closing
from pathlib import Path
import ast
import logging
import sqlite3

import pytest
from flask import Flask, jsonify, request

import core.db_integrity as integrity
import core.library2.schema as schema
import database.music_database as database_module
from database.music_database import MusicDatabase


def _populated(path):
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE saved_user_data(value TEXT)')
        conn.execute("INSERT INTO saved_user_data VALUES('keep me')")


def _restart(path):
    database_module._database_initialized_paths.discard(str(Path(path).resolve()))
    return MusicDatabase(str(path))


def _snapshots(path):
    return sorted((Path(path).parent / 'backups').glob('*upgrade*.db'))


@pytest.mark.parametrize('native', [False, True])
def test_required_schema_failure_rolls_back_every_step_and_does_not_cache_startup(tmp_path, monkeypatch, native):
    path = tmp_path / 'music.db'
    _populated(path)
    if native:
        with sqlite3.connect(path) as conn:
            schema.ensure_library_v2_schema(conn, run_backfills=False)
            conn.execute("INSERT OR REPLACE INTO lib2_bootstrap_state(id,status) VALUES(1,'done')")
    original = schema.ensure_library_v2_schema

    def fail(conn, **kwargs):
        conn.execute('CREATE TABLE partial_required_schema(id INTEGER)')
        raise sqlite3.OperationalError('required schema failed')

    monkeypatch.setattr(schema, 'ensure_library_v2_schema', fail)
    with pytest.raises(sqlite3.OperationalError, match='required schema failed'):
        MusicDatabase(str(path))
    assert str(path.resolve()) not in database_module._database_initialized_paths
    with sqlite3.connect(path) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name IN ('partial_required_schema','sample_stash','lib2_startup_upgrades')").fetchall()
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 0
        assert conn.execute('SELECT value FROM saved_user_data').fetchone()[0] == 'keep me'
    assert len(_snapshots(path)) == 1
    monkeypatch.setattr(schema, 'ensure_library_v2_schema', original)
    _restart(path)
    assert len(_snapshots(path)) == 2


def test_populated_database_snapshot_precedes_even_journal_mode_change(tmp_path, monkeypatch):
    path = tmp_path / 'music.db'
    _populated(path)
    original = integrity.safe_backup

    def observe(source, destination, **kwargs):
        with sqlite3.connect(source) as conn:
            assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
            assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sample_stash'").fetchone()
        original(source, destination, **kwargs)

    monkeypatch.setattr(integrity, 'safe_backup', observe)
    MusicDatabase(str(path))
    snapshots = _snapshots(path)
    assert len(snapshots) == 1
    assert integrity.quick_check(str(snapshots[0])) == 'ok'
    with sqlite3.connect(snapshots[0]) as conn:
        assert conn.execute('SELECT value FROM saved_user_data').fetchone()[0] == 'keep me'
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sample_stash'").fetchone()
    with sqlite3.connect(path) as conn:
        marker = conn.execute('SELECT backup_path FROM lib2_startup_upgrades').fetchone()
        assert Path(marker[0]) == snapshots[0]
    _restart(path)
    assert _snapshots(path) == snapshots


def test_backup_failure_aborts_before_any_persistent_database_change(tmp_path, monkeypatch):
    path = tmp_path / 'music.db'
    _populated(path)
    before = path.read_bytes()

    def fail(source, destination, **kwargs):
        Path(destination).write_bytes(b'partial backup')
        raise integrity.DBIntegrityError('backup failed')

    monkeypatch.setattr(integrity, 'safe_backup', fail)
    with pytest.raises(integrity.DBIntegrityError, match='backup failed'):
        MusicDatabase(str(path))
    assert path.read_bytes() == before
    assert str(path.resolve()) not in database_module._database_initialized_paths
    assert not list((tmp_path / 'backups').iterdir())


def test_fresh_empty_database_needs_no_snapshot(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('empty database does not need backup')

    monkeypatch.setattr(integrity, 'safe_backup', fail)
    path = tmp_path / 'music.db'
    MusicDatabase(str(path))
    assert not _snapshots(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT backup_path FROM lib2_startup_upgrades').fetchone()[0] is None
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='lib2_tracks'").fetchone()


@pytest.mark.parametrize('endpoint,path', [
    ('enriched_start', '/api/search/enriched/start'),
    ('manual_start', '/api/search/manual/start'),
])
def test_search_downloads_wait_for_upgrade_without_reaching_handler(monkeypatch, endpoint, path):
    from core.library2 import migration_gate
    tree = ast.parse((Path(__file__).parents[2] / 'web_server.py').read_text())
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_MIGRATION_BLOCKED_ENDPOINTS' for t in node.targets):
            selected.append(node)
        if isinstance(node, ast.FunctionDef) and node.name == '_hold_catalogue_jobs_during_upgrade':
            node.decorator_list = []
            selected.append(node)
    namespace = dict(request=request, jsonify=jsonify, get_database=lambda: object(), logger=logging.getLogger(__name__))
    exec(compile(ast.Module(body=selected, type_ignores=[]), 'web_server.py', 'exec'), namespace)
    app = Flask(__name__)
    app.before_request(namespace['_hold_catalogue_jobs_during_upgrade'])
    app.add_url_rule(path, endpoint=endpoint, view_func=lambda: jsonify(handler_reached=True), methods=['POST'])
    monkeypatch.setattr(migration_gate, 'migration_required', lambda _: True)
    response = app.test_client().post(path, json={})
    assert response.status_code == 409
    assert response.get_json()['success'] is False
    monkeypatch.setattr(migration_gate, 'migration_required', lambda _: False)
    assert app.test_client().post(path, json={}).get_json() == {'handler_reached': True}


def test_repeated_failed_upgrade_attempts_do_not_fill_the_disk(tmp_path):
    import sqlite3
    from core.library2.upgrade_backup import prepare_startup_upgrade

    database = tmp_path / 'music_library.db'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE tracks(id INTEGER)')
        conn.execute('INSERT INTO tracks VALUES(1)')
    snapshots = [prepare_startup_upgrade(database).backup_path for _ in range(5)]
    survivors = sorted((tmp_path / 'backups').glob('*.db'))
    assert len(survivors) == 2 and snapshots[-1] in survivors
