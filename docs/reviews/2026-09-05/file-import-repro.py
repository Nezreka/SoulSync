"""Read-only source-level reproductions; no application imports or live services.

Runs selected current function bodies via AST. Local imports are replaced by
Pass and dependencies explicitly injected. SQLite is exclusively :memory:;
filesystem operations use doubles. This tests control flow/SQL, not real codecs
or a production database. Execute with .venv/bin/python -B <this file>.
"""
from __future__ import annotations
import ast
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
from dataclasses import dataclass, field
from types import SimpleNamespace as NS
from typing import Mapping

ROOT = Path(__file__).resolve().parents[3]

class StripImports(ast.NodeTransformer):
    def visit_Import(self, node):
        return ast.copy_location(ast.Pass(), node)
    visit_ImportFrom = visit_Import

def load_functions(relative, names, env):
    tree = ast.parse((ROOT / relative).read_text())
    selected = [StripImports().visit(n) for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert len(selected) == len(names), (relative, names)
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *selected], type_ignores=[])
    env.setdefault('__name__', '__main__')
    env.setdefault('logger', logging.getLogger('file-import-repro'))
    exec(compile(ast.fix_missing_locations(module), str(ROOT / relative), 'exec'), env)
    return env

def ddl(name):
    for node in ast.parse((ROOT / 'core/library2/schema.py').read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(name)

class NonClosingConnection:
    def __init__(self, conn): self.conn = conn
    def __getattr__(self, name): return getattr(self.conn, name)
    def close(self): pass


def reactivation():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE lib2_tracks(id INTEGER PRIMARY KEY, album_id INTEGER)')
    conn.execute('CREATE TABLE lib2_albums(id INTEGER PRIMARY KEY, origin TEXT, updated_at TEXT)')
    conn.execute(ddl('LIB2_TRACK_FILES_DDL'))
    conn.execute('INSERT INTO lib2_tracks VALUES(1,2)')
    conn.execute("INSERT INTO lib2_albums VALUES(2,'library',NULL)")
    conn.execute("INSERT INTO lib2_track_files(id,track_id,path,file_state) VALUES(3,1,'/virtual/song.flac','deleted')")
    database = NS(_get_connection=lambda: NonClosingConnection(conn))
    noop = lambda *a, **kw: None
    env = dict(json=json, os=NS(path=NS(getsize=lambda _: 123456, exists=lambda _: True, basename=os.path.basename)),
               get_database=lambda: database, config_manager=NS(),
               get_import_context_album=lambda c: {'name': 'Album'},
               get_import_context_artist=lambda c: {'name': 'Artist'},
               get_import_source_ids=lambda c: {}, get_import_source=lambda c: '',
               _metadata_source=lambda *a: None, _get=lambda d,*keys: next((str(d[k]) for k in keys if d.get(k)), ''),
               _primary_artist_name=lambda d: 'Artist', _parse_source_info=lambda s: {},
               _qualified_provider_id=lambda *a: None, _clean_provider_id=lambda v: None,
               normalize_name=lambda s: str(s).lower(), probe_audio_quality=noop,
               _acoustid_status_for=noop, _pipeline_result_json=lambda c: '{}',
               ACQUIRED_QUALITY_CONTEXT_KEY='_acquired_audio_quality', RETENTION_CONTEXT_KEY='_retention_transforms',
               quality_json=noop, transforms_json=noop, retire_replaced_files=noop,
               default_quality_profile_id=lambda c: 1, recompute_wanted=noop, _warm_new_artwork=noop)
    load_functions('core/library2/autolink.py', ['link_download_into_library_v2'], env)
    context = {'lib2_entity': {'track_id': 1}, 'track_info': {'name': 'Song'}, '_final_processed_path': '/virtual/song.flac'}
    returned = env['link_download_into_library_v2'](context, raise_on_error=True)
    row = conn.execute('SELECT id,file_state,is_primary,size FROM lib2_track_files').fetchone()
    assert returned == 3 and row['file_state'] == 'deleted', dict(row)
    check_env = dict(get_database=lambda: database)
    load_functions('core/imports/side_effects.py', ['registered_lib2_file_id', 'require_library_v2_registration'], check_env)
    assert check_env['registered_lib2_file_id'](context) is None
    try:
        check_env['require_library_v2_registration'](context)
    except RuntimeError:
        pass
    else:
        raise AssertionError('normal completion gate unexpectedly accepted deleted row')
    # The exception recovery repeats autolink and treats any returned id as proof.
    recovery_env = dict(os=NS(path=NS(isfile=lambda _: True)),
        record_soulsync_library_entry=noop, link_download_into_library_v2=env['link_download_into_library_v2'],
        get_database=lambda: database, config_manager=NS(), schedule_file_track_reconcile=noop,
        notify_pipeline_import_success=noop, notify_manual_grab_import_success=noop)
    load_functions('core/imports/pipeline.py', ['_recover_moved_file_bookkeeping'], recovery_env)
    recovered = recovery_env['_recover_moved_file_bookkeeping'](context)
    assert recovered is True and check_env['registered_lib2_file_id'](context) is None
    print('REACTIVATION:', json.dumps(dict(row)), 'normal gate=reject, recovery=True, active row=None')
    conn.close()


def phantom_completion():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE acquisition_imports(id TEXT PRIMARY KEY,status TEXT,result_json TEXT,error TEXT,updated_at TEXT,completed_at TEXT)')
    match = {'relative_path': 'song.flac', 'track_id': 1}
    result = {'quarantined': [{**match, 'reason': 'Upgrade rejected'}]}
    conn.execute('INSERT INTO acquisition_imports VALUES(?,?,?,?,NULL,NULL)', ('imp','importing',json.dumps(result),None))
    def get_import(c, ident):
        row = c.execute('SELECT status,result_json FROM acquisition_imports WHERE id=?',(ident,)).fetchone()
        return NS(id=ident,status=row[0],result=json.loads(row[1]),matches=[match],request_id='req',candidate_id='cand',download_id='dl',resolved_path='/virtual/source')
    events=[]
    env = dict(json=json,Mapping=Mapping,get_import=get_import,_reload_import=get_import,
               transition_request=lambda c,*a,**kw: events.append(('request',a,kw)),
               record_history_event=lambda c,*a,**kw: events.append(('history',a,kw)),
               close_retry_state=lambda c,**kw: events.append(('retry',kw)))
    load_functions('core/acquisition/imports.py', ['record_pipeline_file_completed'], env)
    def callback(context, **kw):
        # Exactly the forwarding done by notify_pipeline_import_success.
        env['record_pipeline_file_completed'](conn,'imp',relative_path='song.flac',final_path=context['_final_processed_path'],track_id=1)
    tasks={}
    def failed_processor(key,context,staged,task_id,*args):
        # Real pipeline sets the target at 1881/1917, then rejects at 1920+.
        context['_final_processed_path']='/virtual/library/song.flac'
        context['_upgrade_rejected']=True
        context['_upgrade_failure_msg']='Upgrade rejected'
        tasks[task_id]['status']='failed'
    bridge_env = dict(get_import=get_import,get_grab=lambda *a: {'source':'torrent'},
        _pipeline_context=lambda *a,**k:{'track_info':{},'username':'torrent','_acquisition_manual_pick':False},
        docker_resolve_path=lambda p:p, _safe_source_path=lambda *a:'/virtual/source/song.flac',
        _stage_working_copy=lambda *a,**k:'/virtual/work/song.flac',download_tasks=tasks,tasks_lock=threading.Lock(),
        notify_pipeline_import_success=callback,redact_sensitive_text=str,dataclass=dataclass)
    load_functions('core/acquisition/main_pipeline_bridge.py', ['BridgeDispatchResult','dispatch_import_to_main_pipeline'], bridge_env)
    dispatched=bridge_env['dispatch_import_to_main_pipeline'](lambda:NonClosingConnection(conn),'imp',config_get=lambda k,d:d,processor=failed_processor,runtime=NS())
    record=get_import(conn,'imp')
    assert record.status=='completed' and not record.result['quarantined']
    assert dispatched.waiting==('song.flac',) and tasks['acq-imp-1']['status']=='failed'
    assert any(e[0]=='retry' and e[1]['status']=='completed' for e in events)
    print('PUBLICATION:', 'task=failed, bridge=waiting, persisted import=completed, quarantined=[], retry=completed; no file was published')
    conn.close()


def working_copy():
    data={'/src/song.flac':b'ORIGINAL','/work/imp_1_song.flac':b'RETAGGED'}
    copies=[]
    class MemoryPath:
        def __init__(self,p):self.p=str(p)
        def __str__(self):return self.p
        def __truediv__(self,p):return MemoryPath(self.p+'/'+str(p))
        @property
        def name(self):return self.p.rsplit('/',1)[-1]
        def mkdir(self,**kw):pass
        def is_file(self):return self.p in data
        def stat(self):return NS(st_size=len(data[self.p]))
        def open(self,mode):return io.BytesIO(data[self.p])
    env=dict(Path=MemoryPath,sanitize_filename=lambda p:p,hashlib=hashlib,_HASH_CHUNK_SIZE=1024)
    load_functions('core/acquisition/main_pipeline_bridge.py', ['_stage_working_copy','_content_hash'], env)
    for _ in range(2):
        try:
            env['_stage_working_copy'](MemoryPath('/src/song.flac'),transfer_dir='/work',import_id='imp',track_id=1,copier=lambda *a:copies.append(a))
        except ValueError as exc:
            assert str(exc)=='existing acquisition working copy has different content'
        else:
            raise AssertionError('retagged retry unexpectedly accepted')
    assert not copies and data['/src/song.flac']==b'ORIGINAL'
    print('WORKING COPY:', 'two retries both reject a retagged copy; copier calls=0; original bytes unchanged')

if __name__=='__main__':
    reactivation()
    phantom_completion()
    working_copy()
