"""Guard: the test suite must NEVER resolve the real music OR video library DB.

Tests exercise modules that call get_database()/get_video_db() with no path. If
that resolves to a live DB, test writes can corrupt a real library (this
happened to BOTH the music and the video library, over a WSL-mounted Windows
drive). conftest.py sets DATABASE_PATH + VIDEO_DATABASE_PATH to temp files before
anything imports; these tests prove it sticks for every default-path access.
"""

from __future__ import annotations

import os
from pathlib import Path

_REAL = os.path.join('database', 'music_library.db')


def test_database_path_env_is_isolated():
    p = os.environ.get('DATABASE_PATH', '')
    assert 'soulsync-testdb-' in p, f"DATABASE_PATH not isolated: {p!r}"


def test_musicdatabase_default_path_never_real():
    from database.music_database import MusicDatabase
    resolved = str(Path(MusicDatabase().database_path).resolve())
    assert 'soulsync-testdb-' in resolved, resolved
    assert not resolved.replace('\\', '/').endswith('database/music_library.db'), resolved


def test_get_database_path_never_real():
    from database.music_database import get_database
    resolved = str(Path(get_database().database_path).resolve())
    assert 'soulsync-testdb-' in resolved, resolved


# ── Video side — same hazard, same guarantee ──────────────────────────────

def test_video_database_path_env_is_isolated():
    p = os.environ.get('VIDEO_DATABASE_PATH', '')
    assert 'soulsync-testdb-' in p, f"VIDEO_DATABASE_PATH not isolated: {p!r}"


def test_videodatabase_default_path_never_real():
    from database.video_database import VideoDatabase
    resolved = str(Path(VideoDatabase().database_path).resolve())
    assert 'soulsync-testdb-' in resolved, resolved
    assert not resolved.replace('\\', '/').endswith('database/video_library.db'), resolved


def test_get_video_db_path_never_real():
    from api.video import get_video_db
    resolved = str(Path(get_video_db().database_path).resolve())
    assert 'soulsync-testdb-' in resolved, resolved
