"""Read-only review reproductions; all database state is sqlite :memory:.
Run from repository root: .venv/bin/python -B docs/reviews/2026-09-05/architecture_repro.py
External settings/provider calls are replaced, no audio is opened or changed.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
import sys
import types

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

def audit(event, args):
    if event == 'sqlite3.connect' and args[0] != ':memory:':
        raise RuntimeError(f'Review prohibits non-memory SQLite: {args[0]}')
    if event in {'socket.connect', 'socket.getaddrinfo'}:
        raise RuntimeError('Review prohibits network access')
    if event == 'open':
        flags = args[2] if len(args) > 2 else 0
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
            raise RuntimeError(f'Review prohibits file writes during repro: {args[0]}')
sys.addaudithook(audit)

settings = types.ModuleType('core.settings')
settings.config_manager = types.SimpleNamespace(get=lambda key, default=None: default)
sys.modules['core.settings'] = settings
metadata = types.ModuleType('core.metadata_service')
def no_provider(*args, **kwargs):
    raise RuntimeError('No provider is used in this repro')
for name in ('get_album_for_source', 'get_album_tracks_for_source', 'get_client_for_source'):
    setattr(metadata, name, no_provider)
metadata.get_primary_source = lambda: 'spotify'
metadata.get_source_priority = lambda source=None: ['spotify', 'deezer', 'itunes']
sys.modules['core.metadata_service'] = metadata

from core.library2.schema import ensure_library_v2_schema
from core.library2 import queries, reorganize_plan, retag
from core.library2.metadata_overrides import set_field_override

conn = sqlite3.connect(':memory:')
conn.row_factory = sqlite3.Row
ensure_library_v2_schema(conn, run_backfills=False)
artist = conn.execute("INSERT INTO lib2_artists(name,name_key) VALUES('Old Name','old name')").lastrowid
album = conn.execute("INSERT INTO lib2_albums(primary_artist_id,title,expected_track_count,monitored) VALUES(?,'Two Discs',2,1)", (artist,)).lastrowid
conn.execute('INSERT INTO lib2_album_artists(album_id,artist_id) VALUES(?,?)', (album, artist))
empty = queries.get_album(conn, album)
print('missing_slots', json.dumps({k: empty[k] for k in ('track_count', 'tracks_present', 'tracks_missing')}))
assert empty['track_count'] == 2 and empty['tracks_missing'] == 4

track1 = conn.execute("INSERT INTO lib2_tracks(album_id,title,track_number,disc_number) VALUES(?,'Disc One Song',1,1)", (album,)).lastrowid
track2 = conn.execute("INSERT INTO lib2_tracks(album_id,title,track_number,disc_number) VALUES(?,'Disc Two Song',1,2)", (album,)).lastrowid
for tid in (track1, track2):
    conn.execute('INSERT INTO lib2_track_artists(track_id,artist_id) VALUES(?,?)', (tid,artist))
file1 = conn.execute("INSERT INTO lib2_track_files(track_id,path,file_state,format,bitrate,sample_rate,bit_depth) VALUES(?,'/synthetic/Disc 1/one.flac','active','flac',900,44100,16)", (track1,)).lastrowid
set_field_override(conn, entity_type='artist', entity_id=artist, field_name='name', value='Corrected Artist')
contexts = []
def fake_path_builder(context, artist_context, album_info, ext, create_dirs=True):
    contexts.append(context)
    return '/synthetic/result.flac', True
plan = reorganize_plan.plan_album_reorganize(conn, album, build_final_path_fn=fake_path_builder,
    transfer_dir='/synthetic', resolve_file_path_fn=lambda path: path)
print('reorganize', json.dumps({'catalogue_max_disc': 2, 'planned_total_discs': contexts[0]['spotify_album']['total_discs'], 'planned_artist': plan['artist']}))
assert contexts[0]['spotify_album']['total_discs'] == 1
assert plan['artist'] == 'Old Name'

tag_context = retag.track_contexts(conn, [track1])[0]['db_data']
shown = queries.get_album(conn, album)['primary_artist']['name']
print('artist_override', json.dumps({'api_artist': shown, 'retag_artist': tag_context['artist_name'], 'retag_track_artist': tag_context['track_artist']}))
assert shown == 'Corrected Artist' and tag_context['artist_name'] == 'Old Name'

# One missing-suspected derivative and one active master stay purely synthetic.
# More cases can be appended as caller tracing confirms them.
print('All review assertions reproduced; no disk/service changes performed.')

# Real shared path builder, dry-run mode. No directory/audio is created.
from core.imports.paths import build_final_path_for_track
settings.config_manager.get = lambda key, default=None: {
    'soulseek.transfer_path': '/synthetic',
}.get(key, default)
actual_before = reorganize_plan.plan_album_reorganize(conn, album,
    build_final_path_fn=build_final_path_for_track,
    transfer_dir='/synthetic', resolve_file_path_fn=lambda path: path)
conn.execute("INSERT INTO lib2_track_files(track_id,path,file_state) VALUES(?,'/synthetic/Disc 2/two.flac','active')", (track2,))
actual_after = reorganize_plan.plan_album_reorganize(conn, album,
    build_final_path_fn=build_final_path_for_track,
    transfer_dir='/synthetic', resolve_file_path_fn=lambda path: path)
before = actual_before['tracks'][0]['new_path_abs']
after = actual_after['tracks'][0]['new_path_abs']
print('real_disc_paths', json.dumps({'disc1_only': before, 'disc2_arrived': after}))
assert before and after and '/Disc 1/' not in before and '/Disc 1/' in after
print('Real shared path-builder dry run also reproduced.')

# MusicBrainz sync with TWO new release groups. The existing tests use one.
from core.library2 import discography, provider_adapters, artwork
sync_conn = sqlite3.connect(':memory:')
sync_conn.row_factory = sqlite3.Row
ensure_library_v2_schema(sync_conn, run_backfills=False)
sync_artist = sync_conn.execute("INSERT INTO lib2_artists(name,name_key) VALUES('Sync Artist','sync artist')").lastrowid
sync_conn.commit()
class ConnectionView:
    def __getattr__(self, name):
        return getattr(sync_conn, name)
    def close(self):
        pass
class MemoryDatabase:
    database_path = ':memory:architecture-sync'
    def _get_connection(self):
        return ConnectionView()
releases = tuple(provider_adapters.DiscographyRelease.from_card({
    'id': f'11111111-1111-4111-8111-{i:012d}',
    'release_group_id': f'11111111-1111-4111-8111-{i:012d}',
    'title': f'New Release {i}', 'album_type': 'album',
    'release_date': f'202{i}-01-01', 'track_count': 10,
}) for i in (1, 2))
sync_result = provider_adapters.DiscographyProviderResult(
    provider='musicbrainz', provider_entity_id=None, releases=releases,
    is_complete=True, cursor=None, page_count=1, etag=None, provider_version=None,
)
provider_adapters.fetch_artist_discography = lambda *args, **kwargs: sync_result
artwork.schedule_missing_artwork = lambda *args, **kwargs: None
try:
    discography.expand_artist_discography(MemoryDatabase(), sync_artist)
except KeyError as error:
    print('musicbrainz_two_new_groups', json.dumps({
        'exception': type(error).__name__, 'key': str(error),
        'uncommitted_albums': sync_conn.execute('SELECT count(*) FROM lib2_albums').fetchone()[0],
        'transaction_open': sync_conn.in_transaction,
    }))
    assert error.args == ('musicbrainz_release_group_id',)
    assert sync_conn.in_transaction
    sync_conn.rollback()
    assert sync_conn.execute('SELECT count(*) FROM lib2_albums').fetchone()[0] == 0
else:
    raise AssertionError('Expected missing release-group key on second new MusicBrainz release')
print('All scoped review repros verified; only in-memory state changed.')
