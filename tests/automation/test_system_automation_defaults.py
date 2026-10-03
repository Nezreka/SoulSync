"""what a brand-new install starts with. a fresh database, the real first-start
seeding, nothing else.

Auto-Scan After Downloads starts the chain that gets a new download into the
library quickly (server scan, then the database update after it). without it a
download waits up to an hour for the hourly update, and a sync in that window
wishlists the song again. it must start switched on; Weekly Cleanup deletes
files for good, so it must start switched off.
"""

from __future__ import annotations

from core.automation_engine import AutomationEngine


def _seeded(tmp_path):
    from database.music_database import MusicDatabase

    db = MusicDatabase(str(tmp_path / 'fresh.db'))
    AutomationEngine(db).ensure_system_automations()
    return {a['action_type']: a for a in db.get_automations(profile_id=1)}


def test_a_new_install_scans_after_downloads(tmp_path):
    autos = _seeded(tmp_path)
    assert autos['scan_library']['name'] == 'Auto-Scan After Downloads'
    assert autos['scan_library']['enabled']
    assert autos['start_database_update']['enabled'], 'the chain stops at the scan without it'


def test_a_new_install_does_not_start_deleting_files(tmp_path):
    assert not _seeded(tmp_path)['library_cleanup']['enabled']
