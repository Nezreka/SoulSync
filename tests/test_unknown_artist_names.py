"""what the Fix Unknown Artists job counts as unknown.

storm (discord): "Navidrome's default unknown artist is '[Unknown Artist]',
so the tool doesn't pick up any tracks." true: the job knew exactly three
spellings, none bracketed. navidrome and plex bracket their placeholder,
jellyfin does not, some tag sets say "Unknown Artists". the sql lists every
form it knows, bare and bracketed, plus whatever the user adds under the
job's unknown_names setting; the python side folds brackets and case.
"""

from __future__ import annotations

from core.repair_jobs.base import JobContext
from core.repair_jobs.unknown_artist_fixer import (
    UnknownArtistFixerJob,
    fold_artist_name,
    is_unknown_artist_name,
    unknown_name_variants,
)
from database.music_database import MusicDatabase


def test_brackets_and_case_are_folded_away():
    assert fold_artist_name('[Unknown Artist]') == 'unknown artist'
    assert fold_artist_name('  (UNKNOWN)  ') == 'unknown'
    assert fold_artist_name('<Unknown Artists>') == 'unknown artists'
    assert fold_artist_name('Radiohead') == 'radiohead'


def test_every_servers_placeholder_reads_as_unknown():
    for name in ('Unknown Artist', '[Unknown Artist]', 'unknown artists', '[Unknown]', '', '   '):
        assert is_unknown_artist_name(name), name
    for name in ('Radiohead', 'The Unknown', 'Unknown Mortal Orchestra', '[Radiohead]'):
        assert not is_unknown_artist_name(name), name


def test_user_added_names_count_too():
    assert is_unknown_artist_name('Artiste Inconnu', extra=['artiste inconnu'])
    assert is_unknown_artist_name('[Artiste Inconnu]', extra=['Artiste Inconnu'])
    assert not is_unknown_artist_name('Artiste Inconnu')


def test_the_sql_list_carries_every_form_bare_and_bracketed():
    names = unknown_name_variants(['Artiste Inconnu'])
    for expected in ('unknown artist', '[unknown artist]', '(unknown artist)',
                     'unknown artists', '[unknown artists]', 'unknown', '[unknown]',
                     '', 'artiste inconnu', '[artiste inconnu]'):
        assert expected in names, expected


class _Config:
    def __init__(self, settings=None):
        self.settings = settings or {}

    def get(self, key, default=None):
        if key == 'repair.jobs.unknown_artist_fixer.settings':
            return self.settings
        return default


def _seed(db, artist_name, track_id='t1'):
    with db._get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO artists (id, name, server_source) VALUES (?, ?, 'test')",
                     (f'ar-{track_id}', artist_name))
        conn.execute("INSERT OR IGNORE INTO albums (id, artist_id, title, server_source) VALUES (?, ?, 'Album', 'test')",
                     (f'al-{track_id}', f'ar-{track_id}'))
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source) "
                     "VALUES (?, ?, ?, 'Song', ?, 'test')",
                     (track_id, f'al-{track_id}', f'ar-{track_id}', f'/music/{track_id}.flac'))
        conn.commit()


def _scope(db, settings=None):
    context = JobContext(db=db, transfer_folder='/music', config_manager=_Config(settings),
                         create_finding=lambda **kw: True, should_stop=lambda: False,
                         is_paused=lambda: False, report_progress=lambda **kw: None)
    return UnknownArtistFixerJob().estimate_scope(context)


def test_navidromes_bracketed_placeholder_is_found(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db, '[Unknown Artist]', 't1')
    _seed(db, 'Unknown Artist', 't2')
    _seed(db, 'Radiohead', 't3')
    assert _scope(db) == 2


def test_a_users_own_placeholder_is_found_when_configured(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    _seed(db, 'Artiste Inconnu', 't1')
    _seed(db, '[Artiste Inconnu]', 't2')
    assert _scope(db) == 0
    assert _scope(db, {'unknown_names': 'Artiste Inconnu, Sconosciuto'}) == 2
