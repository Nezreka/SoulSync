import json
import sqlite3

from core.auto_import_worker import AutoImportWorker, FolderCandidate


class _DB:
    def __init__(self, path):
        self.path = path
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE auto_import_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    folder_name TEXT NOT NULL, folder_path TEXT NOT NULL,
                    folder_hash TEXT, status TEXT NOT NULL,
                    confidence REAL DEFAULT 0, album_id TEXT, album_name TEXT,
                    artist_name TEXT, image_url TEXT, total_files INTEGER DEFAULT 0,
                    matched_files INTEGER DEFAULT 0, match_data TEXT,
                    identification_method TEXT, error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP
                )
            """)

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class _Config:
    def get(self, key, default=None):
        if key in {'auto_import.auto_process', 'import.folder_artist_override'}:
            return False
        return default


def _worker(tmp_path, callback=lambda *_args: None):
    return AutoImportWorker(
        _DB(tmp_path / 'auto-import.db'),
        staging_path=str(tmp_path),
        process_callback=callback,
        config_manager=_Config(),
    )


def _candidate(tmp_path):
    source = tmp_path / 'track.flac'
    source.write_bytes(b'audio')
    return FolderCandidate(
        path=str(tmp_path), name='Album', audio_files=[str(source)],
        folder_hash='content-hash',
    )


def _seed_pending(worker, candidate):
    with worker.database._get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO auto_import_history "
            "(folder_name, folder_path, folder_hash, status, match_data) "
            "VALUES (?, ?, ?, 'pending_review', ?)",
            (candidate.name, candidate.path, candidate.folder_hash,
             json.dumps({'matches': [{'file': 'track.flac'}]})),
        )
        return cursor.lastrowid


def _identification():
    return {
        'source': 'deezer', 'artist_name': 'Artist', 'artist_id': 'artist-1',
        'album_name': 'Album', 'album_id': 'album-1', 'method': 'tags',
    }


def _match(candidate):
    return {
        'confidence': 0.75, 'matched_count': 1, 'total_tracks': 1,
        'album_data': {'id': 'album-1', 'total_tracks': 1},
        'matches': [{
            'track': {'id': 'track-1', 'name': 'Track', 'track_number': 1},
            'file': candidate.audio_files[0], 'confidence': 0.75,
        }],
    }


def test_approval_token_is_bound_and_consumed_exactly_once(tmp_path):
    worker = _worker(tmp_path)
    candidate = _candidate(tmp_path)
    item_id = _seed_pending(worker, candidate)

    assert worker.approve_item(item_id)['success'] is True
    assert worker.approve_item(item_id)['success'] is False
    assert worker._consume_approval(candidate) == item_id
    assert worker._consume_approval(candidate) is None


def test_approval_triggers_one_import_even_when_auto_processing_is_disabled(tmp_path, monkeypatch):
    calls = []

    def succeed(_key, context, path):
        calls.append(path)
        context['_final_processed_path'] = path
        context['_pipeline_import_succeeded'] = True

    worker = _worker(tmp_path, succeed)
    candidate = _candidate(tmp_path)
    item_id = _seed_pending(worker, candidate)
    worker.approve_item(item_id)
    monkeypatch.setattr(worker, '_resolve_rematch_hint', lambda _candidate: (None, None))
    monkeypatch.setattr(worker, '_identify_folder', lambda _candidate: _identification())
    monkeypatch.setattr(worker, '_match_tracks', lambda _candidate, _ident: _match(candidate))

    worker._process_one_candidate(candidate)

    assert calls == candidate.audio_files
    with worker.database._get_connection() as conn:
        statuses = [row[0] for row in conn.execute(
            "SELECT status FROM auto_import_history ORDER BY id"
        )]
    assert statuses == ['completed']


def test_approval_does_not_bypass_pipeline_safety_rejection(tmp_path, monkeypatch):
    def reject(_key, context, _path):
        context['_integrity_failure_msg'] = 'truncated audio'

    worker = _worker(tmp_path, reject)
    candidate = _candidate(tmp_path)
    item_id = _seed_pending(worker, candidate)
    worker.approve_item(item_id)
    monkeypatch.setattr(worker, '_resolve_rematch_hint', lambda _candidate: (None, None))
    monkeypatch.setattr(worker, '_identify_folder', lambda _candidate: _identification())
    monkeypatch.setattr(worker, '_match_tracks', lambda _candidate, _ident: _match(candidate))

    worker._process_one_candidate(candidate)

    with worker.database._get_connection() as conn:
        rows = list(conn.execute(
            "SELECT status, error_message, match_data FROM auto_import_history ORDER BY id"
        ))
    assert [row['status'] for row in rows] == ['failed']
    assert 'integrity check failed' in rows[-1]['error_message']
    assert json.loads(rows[-1]['match_data'])['matches'][0]['import_status'] == 'failed'
