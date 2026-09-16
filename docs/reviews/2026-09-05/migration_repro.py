"""Read-only review reproductions: actual source functions, synthetic memory DBs.
Run: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/reviews/2026-09-05/migration_repro.py
No web_server import, app startup, live DB, external service, or file writes.
"""
from __future__ import annotations
import ast
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import types
from datetime import datetime
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
logging.disable(logging.CRITICAL)
# Enforce the read-only/local contract even on indirect imports.
def audit(event, args):
    if event == 'open':
        _, mode, flags = args
        if (isinstance(mode, str) and any(x in mode for x in 'wax+')) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
            raise RuntimeError('Repro forbids filesystem writes')
    if event == 'sqlite3.connect':
        target = str(args[0])
        if target != ':memory:' and not (target.startswith('file:migration-review-') and 'mode=memory' in target):
            raise RuntimeError(f'Repro forbids non-memory DB: {target}')
    if event in {'socket.connect', 'socket.bind', 'subprocess.Popen', 'os.system'}:
        raise RuntimeError(f'Repro forbids external access: {event}')
sys.addaudithook(audit)

from flask import Flask, Blueprint, jsonify, request
from core.library2 import bootstrap, migration_gate, schema
from core.library2.importer import import_legacy_library, _reconcile_legacy_snapshot

REAL_CONNECT = sqlite3.connect
class MemoryDB:
    counter = 0
    def __init__(self, *, init=True):
        MemoryDB.counter += 1
        self.database_path = f'file:migration-review-{MemoryDB.counter}?mode=memory&cache=shared'
        self.keeper = self._get_connection()
        if init:
            schema.ensure_library_v2_schema(self.keeper, run_backfills=False)
            self.keeper.commit()
    def _get_connection(self):
        c = REAL_CONNECT(self.database_path, uri=True)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        return c
    def count(self, table):
        return self.keeper.execute(f'SELECT count(*) FROM {table}').fetchone()[0]


def extract(path, name, ns):
    tree = ast.parse((ROOT/path).read_text())
    node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
    return ns[name]


def gate_repro():
    tree = ast.parse((ROOT/'web_server.py').read_text())
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == '_MIGRATION_BLOCKED_ENDPOINTS' for t in n.targets))
    ns = {'request': request, 'jsonify': jsonify, 'get_database': lambda: object(), 'logger': logging.getLogger()}
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), 'web_server.py', 'exec'), ns)
    hook = extract('web_server.py', '_hold_catalogue_jobs_during_upgrade', ns)
    app = Flask('migration-hook-repro')
    app.before_request(hook)
    for source, function in [('api/database_admin.py', 'restore_backup_endpoint'),
                             ('api/auto_import.py', 'auto_import_approve')]:
        tree = ast.parse((ROOT/source).read_text())
        blueprint_name = next(n.args[0].value for n in ast.walk(tree)
                              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'Blueprint')
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
        route = next(n.args[0].value for n in node.decorator_list
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'route')
        bp = Blueprint(blueprint_name, __name__)
        bp.add_url_rule(route, endpoint=function, view_func=lambda **kw: jsonify(reached=True, endpoint=request.endpoint), methods=['POST'])
        app.register_blueprint(bp)
    app.add_url_rule('/api/library/v2/artists', endpoint='lib2_mutation', view_func=lambda: jsonify(reached=True), methods=['POST'])
    with patch.object(migration_gate, 'migration_required', return_value=True):
        for rule in app.url_map.iter_rules():
            if rule.endpoint == 'static':
                continue
            path = rule.rule.replace('<filename>', 'music_library.db.backup_20260101_000000').replace('<int:item_id>', '1').replace('<item_id>', '1')
            response = app.test_client().post(path)
            print('GATE', rule.endpoint, response.status_code, response.get_json())
            assert response.status_code == (409 if rule.endpoint == 'lib2_mutation' else 200)


def seed_native(db):
    c = db.keeper
    artist = c.execute("INSERT INTO lib2_artists(name,sort_name) VALUES('Native Artist','Native Artist')").lastrowid
    album = c.execute("INSERT INTO lib2_albums(primary_artist_id,title) VALUES(?,'Native Album')", (artist,)).lastrowid
    track = c.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Native Track')", (album,)).lastrowid
    c.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?,'/synthetic/track.flac')", (track,))
    c.commit()


def restore_repro():
    current = MemoryDB()
    seed_native(current)
    ns = {'get_database': lambda: current, 'config_manager': types.SimpleNamespace(get=lambda key, default=None: default),
          'logger': logging.getLogger()}
    autostart = extract('web_server.py', '_autostart_library_v2_bootstrap_import', ns)
    # The imported precache starter is not needed on the empty-source path.
    stub_api = types.ModuleType('api.library_v2')
    stub_api.start_artwork_precache = lambda *a, **kw: None
    with patch.dict(sys.modules, {'api.library_v2': stub_api}):
        autostart()
    print('RESTORE before', 'autostart_returned=True', 'state='+bootstrap.get_state(current)['status'],
          'artists='+str(current.count('lib2_artists')))
    old = MemoryDB(init=False)
    old.keeper.executescript('''
        CREATE TABLE artists(id TEXT PRIMARY KEY,name TEXT);
        CREATE TABLE albums(id TEXT PRIMARY KEY,artist_id TEXT,title TEXT);
        CREATE TABLE tracks(id TEXT PRIMARY KEY,album_id TEXT,title TEXT,file_path TEXT);
        INSERT INTO artists VALUES('old-1','Old Artist');
        INSERT INTO albums VALUES('old-album','old-1','Old Album');
        INSERT INTO tracks VALUES('old-track','old-album','Old Track',NULL);
    ''')
    safety = MemoryDB(init=False)
    holder = {'initialized': True}
    def close_db():
        holder['initialized'] = False
    def get_db():
        if not holder['initialized']:
            c = current._get_connection()
            schema.ensure_library_v2_schema(c, run_backfills=False)
            c.commit()
            c.close()
            holder['initialized'] = True
        return current
    fake_music_db = types.ModuleType('database.music_database')
    fake_music_db.close_database, fake_music_db.get_database = close_db, get_db
    filename = 'music_library.db.backup_20260101_000000'
    db_path, backup_path = '/synthetic/current.db', '/synthetic/'+filename
    def connect(path, *args, **kw):
        db = current if path == db_path else old if path == backup_path else safety
        return db._get_connection()
    fake_os = types.SimpleNamespace(environ={'DATABASE_PATH': db_path}, path=types.SimpleNamespace(
        dirname=os.path.dirname, join=os.path.join, exists=lambda p: p == backup_path))
    restore = extract('api/database_admin.py', 'restore_backup_endpoint', {
        'os': fake_os, '_BACKUP_FILENAME_RE': __import__('re').compile(r'^music_library\.db\.backup_\d{8}_\d{6}$'),
        'request': request, 'jsonify': jsonify, 'datetime': datetime,
        'SOULSYNC_VERSION': 'test', 'json': json, 'logger': logging.getLogger(),
        'open': lambda *args, **kw: io.StringIO(),
    })
    app = Flask('restore-repro')
    with app.test_request_context(json={}), patch.dict(sys.modules, {'database.music_database': fake_music_db}), patch.object(sqlite3, 'connect', side_effect=connect):
        response = restore(filename)
        if isinstance(response, tuple):
            raise AssertionError((response[1], response[0].get_json()))
        print('RESTORE response', response.get_json())
    class Worker:
        running, paused = True, False
        def pause(self): self.paused = True
        def resume(self): self.paused = False
    worker = Worker()
    supervisor = migration_gate.MigrationPauseSupervisor(current, lambda: [worker])
    active = supervisor.tick()
    print('RESTORE after', 'state='+bootstrap.get_state(current)['status'],
          'legacy_artists='+str(current.count('artists')), 'native_artists='+str(current.count('lib2_artists')),
          'migration_required='+str(active), 'worker_paused='+str(worker.paused))
    assert response.get_json()['success'] and current.count('lib2_artists') == 0 and active and worker.paused


def reset_without_source_repro():
    db = MemoryDB()
    seed_native(db)
    before = db.count('lib2_artists'), db.count('lib2_tracks'), db.count('lib2_track_files')
    try:
        import_legacy_library(db, reset=True)
        raise AssertionError('Expected missing-source error')
    except RuntimeError as exc:
        print('RESET_NO_SOURCE', str(exc), 'before='+str(before),
              'after='+str((db.count('lib2_artists'), db.count('lib2_tracks'), db.count('lib2_track_files'))))
    assert db.count('lib2_artists') == db.count('lib2_track_files') == 0


def external_id_preservation_repro():
    db = MemoryDB()
    c = db.keeper
    artist = c.execute("INSERT INTO lib2_artists(name,sort_name,legacy_artist_id,external_ids,legacy_import_run_id) VALUES('Kept Artist','Kept Artist','artist-old','{\"deezer\":\"42\"}','old')").lastrowid
    album = c.execute("INSERT INTO lib2_albums(primary_artist_id,title,legacy_album_id,external_ids,legacy_import_run_id) VALUES(?,'Kept Album','album-old','{\"deezer\":\"43\"}','old')", (artist,)).lastrowid
    c.execute("INSERT INTO lib2_tracks(album_id,title,legacy_track_id,external_ids,legacy_import_run_id) VALUES(?,'Kept Track','track-old','{\"deezer\":\"44\"}','old')", (album,))
    c.commit()
    stats = _reconcile_legacy_snapshot(c.cursor(), 'new')
    c.commit()
    print('PROVIDER_PRESERVATION', stats, 'remaining=', [db.count(t) for t in ('lib2_artists','lib2_albums','lib2_tracks')])
    assert [db.count(t) for t in ('lib2_artists','lib2_albums','lib2_tracks')] == [0, 0, 0]



def adoption_replay_repro():
    from collections import defaultdict
    from core.library2.importer import _ArtistResolver
    db = MemoryDB()
    c = db.keeper
    artist_id = c.execute(
        "INSERT INTO lib2_artists(name,sort_name,external_ids) VALUES(?,?,?)",
        ('Adopted Artist', 'Adopted Artist', json.dumps({'deezer': '42', 'musicbrainz': 'a3200370-345c-46e0-8786-347b300ce267'})),
    ).lastrowid
    profile_id = c.execute('SELECT id FROM quality_profiles ORDER BY is_default DESC,id LIMIT 1').fetchone()[0]
    fields = defaultdict(lambda: None, name='Adopted Artist', sort_name='Adopted Artist',
                         genres='[]', aliases='[]', provider_ids={'deezer': '42'})
    snapshots = []
    for unused in range(2):
        resolver = _ArtistResolver(c.cursor(), profile_id)
        resolver.seed_existing()
        assert resolver.upsert_legacy('legacy-42', fields, 'same-run') == artist_id
        c.commit()
        snapshots.append(dict(c.execute('SELECT musicbrainz_id,external_ids FROM lib2_artists WHERE id=?', (artist_id,)).fetchone()))
    print('ADOPTION_REPLAY', snapshots)
    assert snapshots[0]['musicbrainz_id'] and snapshots[1]['musicbrainz_id'] is None

if __name__ == '__main__':
    gate_repro()
    restore_repro()
    reset_without_source_repro()
    external_id_preservation_repro()
    adoption_replay_repro()
