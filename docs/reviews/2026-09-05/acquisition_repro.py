"""Read-only acquisition review repro; all databases are SQLite in-memory.

Run from repo root: .venv/bin/python -B docs/reviews/2026-09-05/acquisition_repro.py
No app startup, live config, services, external client, or file import.
The grab route is extracted unchanged from its nested registration function.
Filesystem staging and pipeline processor are mocked to inspect the boundary.
"""
from __future__ import annotations
import ast
import logging
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

log_stub = types.ModuleType('utils.logging_config')
log_stub.get_logger = logging.getLogger
sys.modules['utils.logging_config'] = log_stub
settings_stub = types.ModuleType('core.settings')
settings_stub.config_manager = types.SimpleNamespace(get=lambda key, default=None: default)
sys.modules['core.settings'] = settings_stub
# Any unintended default DB access is a hard failure, not a redirect to live data.
db_stub = types.ModuleType('database.music_database')
def blocked_db(*args, **kwargs):
    raise AssertionError('Default/live database access forbidden in review repro')
db_stub.MusicDatabase = blocked_db
db_stub.get_database = blocked_db
sys.modules['database.music_database'] = db_stub

from core.acquisition import ensure_acquisition_schema
from core.acquisition.candidates import register_candidate
from core.acquisition.eligibility_gate import CatalogContext, EffectivePolicy, RuntimeContext
from core.acquisition.grabs import get_grab, open_grabs
from core.acquisition.requests import create_request, get_request, transition_request
from core.acquisition.workflow import evaluate_request_candidates, prepare_candidate_grab, record_grab_outcome, retry_acquisition_request
from core.acquisition.imports import record_download_completed, record_inventory_result, record_matching_result
from core.acquisition import main_pipeline_bridge as bridge
from core.acquisition.retry_resume import _rebuild_task
from core.acquisition.retry_state import journal_retry_snapshot, get_retry_state
from core.imports.upgrade_intent import get_upgrade_intent
from core.runtime_state import download_tasks
from flask import Flask, jsonify, request

URI = 'file:acquisition-review?mode=memory&cache=shared'
def connect():
    c = sqlite3.connect(URI, uri=True)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c
anchor = connect()
ensure_acquisition_schema(anchor)
anchor.executescript('''
CREATE TABLE lib2_artists(id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE lib2_albums(id INTEGER PRIMARY KEY, primary_artist_id INTEGER, title TEXT, album_type TEXT, release_date TEXT, spotify_id TEXT);
CREATE TABLE lib2_tracks(id INTEGER PRIMARY KEY, album_id INTEGER, title TEXT, track_number INTEGER, disc_number INTEGER, duration INTEGER, spotify_id TEXT);
INSERT INTO lib2_artists VALUES(301,'Artist');
INSERT INTO lib2_albums VALUES(201,301,'Album','album','2024',NULL);
INSERT INTO lib2_tracks VALUES(101,201,'Song',1,1,180000,NULL);
''')
CAT = CatalogContext(artist='Artist', release_title='Album')
POL = EffectivePolicy()
RUN = RuntimeContext()

def candidate(c, req_id, key):
    return register_candidate(c, request_id=req_id, source='usenet', protocol='usenet', content_scope='release_bundle', server_ref='opaque-'+key, title='Artist - Album', indexer='idx', guid=key, facts={'artist':'Artist','release_title':'Album'})

def prepared(key):
    r, _ = create_request(anchor, profile_id=1, scope='release_edition', entity_id=201, quality_profile_id=2, trigger='scheduled', idempotency_key=key)
    transition_request(anchor, r.id, 'searching', increment_attempts=True)
    c, _ = candidate(anchor, r.id, key)
    evaluate_request_candidates(anchor, r.id, catalog=CAT, runtime=RUN, policy=POL, automatic=False)
    p = prepare_candidate_grab(anchor, r.id, c.id, download_id='dl-'+key, catalog=CAT, runtime=RUN, policy=POL)
    anchor.commit()
    return p

def extract_function(path, name, scope):
    tree = ast.parse((ROOT / path).read_text())
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    func.decorator_list = []
    node = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), func], type_ignores=[])
    ast.fix_missing_locations(node)
    exec(compile(node, str(ROOT / path), 'exec'), scope)
    return scope[name]

p = prepared('retry')
record_grab_outcome(anchor, p.download_id, completed=False, error='client temporarily unconfigured', failure_kind='runtime')
retry_acquisition_request(anchor, p.request.id)
again, created = candidate(anchor, p.request.id, 'retry')
assert again.id == p.candidate.id and created is False
evaluate_request_candidates(anchor, p.request.id, catalog=CAT, runtime=RUN, policy=POL, automatic=False)
anchor.commit()
submissions = []
def adapter(source):
    submissions.append(source)
    raise AssertionError('No external client may be called')
scope = dict(_guard=lambda: None, _conn=connect, ADMIN_PROFILE_ID=1, request=request, jsonify=jsonify, _acquisition_submission_adapter=adapter)
route = extract_function('api/library_v2.py', 'lib2_grab_acquisition_candidate', scope)
app = Flask('acquisition-review')
app.add_url_rule('/grab/<request_id>', view_func=route, methods=['POST'])
response = app.test_client().post('/grab/'+p.request.id, json={'candidate_id': again.id})
payload = response.get_json()
assert response.status_code == 200
assert payload['success'] and payload['created'] is False
assert payload['grab']['status'] == 'failed'
assert not submissions
assert get_request(anchor, p.request.id).status == 'candidates_ready'
print('REPRO 1 PASS: after real runtime failure + retry + same-GUID search, POST grab returns old failed grab, HTTP 200, success=true, no submit; request=candidates_ready')

p = prepared('intent')
rec = record_download_completed(anchor, p.download_id, output_path='/virtual/client')
record_inventory_result(anchor, rec.id, [{'relative_path':'01.flac','size_bytes':5}], resolved_path='/virtual/client')
rec = record_matching_result(anchor, rec.id, [{'relative_path':'01.flac','track_id':101,'track_number':1,'disc_number':1}], [], decision='import_ready')
anchor.commit()
seen = {}
def processor(key, context, path, task_id, batch_id, runtime):
    seen['context_intent'] = get_upgrade_intent(context)
    seen['task_intent'] = get_upgrade_intent(download_tasks[task_id])
    seen['task_id'] = task_id
    # Represent a pipeline quarantine handing control to the retry worker.
    download_tasks[task_id]['status'] = 'searching'
paths_stub = types.ModuleType('core.imports.paths')
paths_stub.docker_resolve_path = lambda value: value
with patch.dict(sys.modules, {'core.imports.paths': paths_stub}), patch.object(bridge, '_safe_source_path', return_value=Path('/virtual/client/01.flac')), patch.object(bridge, '_stage_working_copy', return_value='/virtual/transfer/01.flac'):
    result = bridge.dispatch_import_to_main_pipeline(connect, rec.id, config_get=lambda key, default=None: default, processor=processor, runtime=object())
assert not result.errors
assert seen['context_intent'].track_id == 101
assert seen['task_intent'] is None
journal_retry_snapshot(anchor, task_id=seen['task_id'], import_id=rec.id, track_id=101, retry_count=1)
state = get_retry_state(anchor, seen['task_id'])
restored, closure = _rebuild_task(anchor, state)
assert closure is None and restored is not None
assert get_upgrade_intent(restored) is None
print('REPRO 2 PASS: dispatch processor has sealed intent(track=101), retry task has none; real _rebuild_task also restores no intent')

p = prepared('crash')
# Simulate crash after remote acceptance, before local external_job_id persistence.
anchor.execute("UPDATE acquisition_grabs SET created_at='2026-09-04 01:00:00' WHERE download_id=?", (p.download_id,))
anchor.commit()
cleanup = extract_function('core/acquisition/client_monitor.py', 'fail_stale_local_submissions', dict(open_grabs=open_grabs, record_grab_outcome=record_grab_outcome))
failed = cleanup(anchor, created_before='2026-09-05 01:00:00')
assert p.download_id in failed
assert get_grab(anchor, p.download_id)['status'] == 'failed'
assert not any(g['download_id'] == p.download_id for g in open_grabs(anchor, 'usenet'))
print('REPRO 3 PASS: crash-window submitting row is terminally failed and removed from open-grab set before client reconciliation')
anchor.close()
