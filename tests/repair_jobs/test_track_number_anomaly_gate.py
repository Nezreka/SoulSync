"""Track Number Repair looks only at an album where several files share one
(disc, track) number, the all-tracks-say-01 bug it exists for (3.6.1's
anomaly gate). A normally numbered album is never resolved or renumbered."""

from __future__ import annotations

import json

from core.repair_jobs import track_number_repair
from core.repair_jobs.base import JobContext
from database.music_database import MusicDatabase


def _subject(album_id, n, *, track, disc=None):
    return {'album_id': album_id, 'path': f'/music/A{album_id}/{n:02d}.flac',
            'artist_name': 'Artist', 'file_id': album_id * 100 + n,
            'track_id': album_id * 100 + n,
            'tags_json': json.dumps({'track_number': track, 'disc_number': disc})}


def _asked(tmp_path, monkeypatch, subjects, settings=None):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    monkeypatch.setattr(track_number_repair, 'active_file_subjects', lambda *_a, **_k: subjects)
    asked = []
    monkeypatch.setattr('core.library2.completeness.resolve_tracklist',
                        lambda _cfg, _conn, album_id: asked.append(album_id))
    cfg = type('Cfg', (), {'get': lambda self, key, default=None: (
        settings if settings is not None and key.endswith('.settings') else default)})()
    result = track_number_repair.TrackNumberRepairJob().scan(
        JobContext(db=db, transfer_folder=str(tmp_path), config_manager=cfg))
    return asked, result


def test_only_the_album_whose_files_all_say_01_is_looked_at(tmp_path, monkeypatch):
    broken = [_subject(1, n, track='1/12') for n in range(1, 5)]
    fine = [_subject(2, n, track=n) for n in range(1, 5)]

    asked, result = _asked(tmp_path, monkeypatch, broken + fine)

    assert asked == [1]
    assert result.scanned >= len(fine)


def test_a_flat_multi_disc_album_is_not_an_anomaly(tmp_path, monkeypatch):
    """#1009: disc 1 track 1, disc 2 track 1, ... repeat each number once per disc"""
    box = [_subject(3, d * 10 + t, track=t, disc=d) for d in range(1, 6) for t in range(1, 4)]

    asked, _result = _asked(tmp_path, monkeypatch, box)

    assert asked == []


def test_the_threshold_setting_is_honoured(tmp_path, monkeypatch):
    two_dupes = [_subject(4, 1, track=1), _subject(4, 2, track=1), _subject(4, 3, track=3)]

    assert _asked(tmp_path, monkeypatch, two_dupes)[0] == []
    assert _asked(tmp_path / 'b', monkeypatch, two_dupes,
                  {'anomaly_threshold': 2, 'dry_run': True})[0] == [4]
