"""T-12 — Library Reorganize minted bare numeric ids for native rows.

The job reads ``lib2_albums``/``lib2_tracks`` (its ``data_basis`` is honest)
but wrote the row id into ``entity_id`` unprefixed. Since T-01 a bare numeric
entity id is interpreted as a *legacy* id and resolved through
``lib2_*.legacy_*_id``, so a native id that happens to equal some other row's
legacy back-reference links the finding to a foreign track — and
``annotate_finding_details`` bakes that wrong id into the stored details at
creation time.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reorganize import LibraryReorganizeJob


class _Cfg:
    def get(self, key, default=None):
        if key == 'features.library_v2':
            return True
        return default


@pytest.fixture
def reorg_db(tmp_path: Path):
    db_path = str(tmp_path / 'lib2.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists(id, name) VALUES(1, 'Artist')")
    conn.execute("INSERT INTO lib2_albums(id, primary_artist_id, title, legacy_album_id) "
                 "VALUES(7, 1, 'Album', 700)")
    conn.execute("INSERT INTO lib2_tracks(id, album_id, title, track_number, "
                 "legacy_track_id) VALUES(4, 7, 'Real Track', 1, 400)")
    # The decoy: its LEGACY back-reference equals the native id of the row the
    # finding is really about. A bare "4" resolves to this row instead.
    conn.execute("INSERT INTO lib2_tracks(id, album_id, title, track_number, "
                 "legacy_track_id) VALUES(9, 7, 'Unrelated Track', 2, 4)")
    conn.execute("INSERT INTO lib2_track_files(id, track_id, path) "
                 "VALUES(11, 4, ?)", (str(tmp_path / 'old' / 'track.flac'),))
    conn.execute("INSERT INTO lib2_track_files(id, track_id, path) "
                 "VALUES(12, 9, ?)", (str(tmp_path / 'old' / 'other.flac'),))
    conn.commit()
    conn.close()

    class _DB:
        database_path = db_path

        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    return _DB()


def _ctx(db, findings):
    return JobContext(
        db=db, transfer_folder='/tmp', config_manager=_Cfg(),
        create_finding=lambda **kw: findings.append(kw) or True,
        should_stop=lambda: False, is_paused=lambda: False,
    )


def _stub_preview(monkeypatch, tmp_path: Path):
    src = tmp_path / 'old' / 'track.flac'
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b'audio')
    dst = tmp_path / 'new' / 'track.flac'

    def _preview(db, config_manager, album_id, mode='api'):
        # `track_id` is the LIB2 track id. The preview is built by
        # core/library_reorganize.load_album_and_tracks, which selects
        # `t.* FROM lib2_tracks t` and copies `track['id']` into `track_id`.
        # This stub used to return the LEGACY id (400), which encoded a
        # contract production never produces -- and so proved a mapping step
        # that made the job find nothing in real use.
        return {
            'status': 'planned',
            'source': 'tags',
            'tracks': [{
                'track_id': 4, 'title': 'Real Track', 'matched': True,
                'unchanged': False, 'file_exists': True,
                'current_path': 'old/track.flac', 'new_path': 'new/track.flac',
                'current_path_abs': str(src), 'new_path_abs': str(dst),
            }],
        }

    import core.library2.reorganize_bridge as bridge
    monkeypatch.setattr(bridge, 'preview_album_reorganize', _preview)


def test_path_mismatch_finding_names_the_native_track_unambiguously(
        reorg_db, tmp_path, monkeypatch):
    _stub_preview(monkeypatch, tmp_path)
    findings = []

    LibraryReorganizeJob().scan(_ctx(reorg_db, findings))

    assert len(findings) == 1
    assert findings[0]['entity_id'] == 'lib2:4'
    assert findings[0]['details']['lib2_track_id'] == 4


def test_the_finding_does_not_link_the_decoy_track(reorg_db, tmp_path, monkeypatch):
    from core.library2.maintenance_sync import annotate_finding_details

    _stub_preview(monkeypatch, tmp_path)
    findings = []
    LibraryReorganizeJob().scan(_ctx(reorg_db, findings))
    finding = findings[0]

    details = annotate_finding_details(
        reorg_db, _Cfg(),
        entity_type=finding['entity_type'],
        entity_id=finding['entity_id'],
        file_path=finding['file_path'],
        details=finding['details'],
    )

    assert details['library_v2']['track_ids'] == [4], (
        'track 9 (legacy_track_id=4) must never be dragged in'
    )


def test_a_lib2_native_album_is_reorganized_not_refused(
        reorg_db, tmp_path, monkeypatch):
    """`legacy_album_id IS NULL` is the normal case, not an error case.

    That back-reference is written only by the one-shot upgrade importer. Every
    album downloaded after the migration -- and every album on a fresh install
    -- is lib2-native and has none, while still owning files. The job used to
    refuse those with a `reorganize_unavailable` warning that had no fix
    handler and no UI, so the feature was inert on any post-cutover library.
    """
    _stub_preview(monkeypatch, tmp_path)
    conn = reorg_db._get_connection()
    conn.execute("UPDATE lib2_albums SET legacy_album_id=NULL WHERE id=7")
    conn.commit()
    conn.close()
    findings = []

    LibraryReorganizeJob().scan(_ctx(reorg_db, findings))

    assert [f['finding_type'] for f in findings] == ['path_mismatch']
    assert findings[0]['entity_id'] == 'lib2:4'


def test_a_missing_track_id_is_an_error_not_a_wrong_link(
        reorg_db, tmp_path, monkeypatch):
    """A preview row without a usable id must not silently link somewhere."""
    src = tmp_path / 'old' / 'track.flac'
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b'audio')

    def _preview(db, config_manager, album_id, mode='api'):
        return {
            'status': 'planned', 'source': 'tags',
            'tracks': [{
                'track_id': None, 'title': 'Real Track', 'matched': True,
                'unchanged': False, 'file_exists': True,
                'current_path': 'old/track.flac', 'new_path': 'new/track.flac',
                'current_path_abs': str(src),
                'new_path_abs': str(tmp_path / 'new' / 'track.flac'),
            }],
        }

    import core.library2.reorganize_bridge as bridge
    monkeypatch.setattr(bridge, 'preview_album_reorganize', _preview)
    findings = []

    result = LibraryReorganizeJob().scan(_ctx(reorg_db, findings))

    assert findings == []
    assert result.errors == 1
