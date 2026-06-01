"""Guard: the test suite must NEVER resolve the real database/music_library.db.

Tests exercise modules that call get_database() with no path. If that resolves
to the live DB, test writes can corrupt a real library (this happened, over a
WSL-mounted Windows drive). conftest.py sets DATABASE_PATH to a temp file before
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
