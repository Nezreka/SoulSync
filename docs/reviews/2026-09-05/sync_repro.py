"""Read-only-review reproductions: actual MusicDatabase + mirror, only disposable DBs.
Run: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/reviews/2026-09-05/sync_repro.py
These assertions characterize the observed bugs, not desired behavior.
"""
from pathlib import Path
import asyncio
import json
import logging
import os
import runpy
import socket
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ.pop('SOULSYNC_TEST_DB_READY', None)
runpy.run_path(str(ROOT / 'tests/conftest.py'))  # isolation BEFORE application imports
assert Path(os.environ['DATABASE_PATH']).is_relative_to('/tmp')

def no_connect(*args, **kwargs):
    raise AssertionError('Network access forbidden in review reproduction')
socket.socket.connect = no_connect
socket.create_connection = no_connect
logging.disable(logging.CRITICAL)

from database.music_database import MusicDatabase
from core.wishlist.service import WishlistService
from core.library2.monitor_rules import record_rule, PROVENANCE_USER
from core.library2.wanted import recompute_wanted
from core.library2.wishlist_mirror import track_wishlist_payload, mirror_projected_tracks_wishlist
from core.library2.monitor_sync import reconcile_track_wishlist, sync_scanned_tracks_wishlist
from core.library2.mirror_outbox import enqueue_tracks, drain
from core.watchlist_scanner import WatchlistScanner
from services.sync_service import PlaylistSyncService

out = {}

def database(name):
    db = MusicDatabase(str(Path(os.environ['DATABASE_PATH']).parent / (name + '.db')))
    with db._get_connection() as conn:
        conn.execute('UPDATE quality_profiles SET is_default=0')
        default = conn.execute("INSERT INTO quality_profiles(name,ranked_targets,upgrade_policy,is_default) VALUES('Review default','[]','none',1)").lastrowid
        specific = conn.execute("INSERT INTO quality_profiles(name,ranked_targets,upgrade_policy,is_default) VALUES('Review lossless','[{\"label\":\"FLAC\",\"format\":\"flac\"}]','acceptable',0)").lastrowid
        conn.commit()
    return db, default, specific

def seed(db, pid, *, sid='review-track', album_sid='review-album'):
    with db._get_connection() as c:
        ar = c.execute("INSERT INTO lib2_artists(name) VALUES('Review artist')").lastrowid
        al = c.execute("INSERT INTO lib2_albums(primary_artist_id,title,spotify_id,album_type) VALUES(?,'Review album',?,'album')", (ar,album_sid)).lastrowid
        t = c.execute("INSERT INTO lib2_tracks(album_id,title,spotify_id,monitored,quality_profile_id,quality_profile_explicit) VALUES(?,'Review song',?,1,?,1)", (al,sid,pid)).lastrowid
        c.execute('INSERT INTO lib2_track_artists(track_id,artist_id) VALUES(?,?)',(t,ar))
        c.execute('INSERT INTO lib2_album_artists(album_id,artist_id) VALUES(?,?)',(al,ar))
        record_rule(c,'track',t,True,PROVENANCE_USER)
        recompute_wanted(c,track_ids=[t])
        c.commit()
    return t

def rows(db):
    with db._get_connection() as c:
        return [dict(r) for r in c.execute('SELECT spotify_track_id,quality_profile_id FROM wishlist_tracks ORDER BY id')]

def payload(db,t):
    with db._get_connection() as c:
        result = track_wishlist_payload(c,t)
        c.commit()
    return {k:v for k,v in result.items() if not k.startswith('_')}

def run_playlist(db, original):
    wishlist = WishlistService(db.database_path)
    wishlist._database = db
    sync = PlaylistSyncService.__new__(PlaylistSyncService)
    sync.syncing_playlists = set()
    sync.progress_callbacks = {}
    sync._original_tracks_map = {original['id']: original}
    sync._get_active_media_client = lambda: (None, 'soulsync')
    sync._find_track_in_media_server = AsyncMock(return_value=(None, 0.0))
    source = SimpleNamespace(id=original['id'],name=original['name'],artists=['Review artist'],album='Review album',duration_ms=123000)
    playlist = SimpleNamespace(id='review-playlist',name='Review playlist',tracks=[source])
    with patch('core.wishlist_service.get_wishlist_service', return_value=wishlist):
        result = asyncio.run(sync.sync_playlist(playlist, profile_id=1))
    assert not result.errors, result.errors
    return result

# SYNC-01: full sync method, real WishlistService, real database and reverse mirror.
db, default, specific = database('sync-01')
t = seed(db, specific)
original = payload(db,t)
assert original['quality_profile_id'] == specific
run_playlist(db, original)
assert rows(db)[0]['quality_profile_id'] == default
first = rows(db)
reconciled = reconcile_track_wishlist(db)
assert rows(db)[0]['quality_profile_id'] == default
with db._get_connection() as c:
    mirror_projected_tracks_wishlist(db,c,[t])
assert rows(db)[0]['quality_profile_id'] == specific
run_playlist(db, original)
assert rows(db)[0]['quality_profile_id'] == specific
out['SYNC-01-playlist'] = {'requested':specific,'default':default,'after_sync':first,'hourly_reconcile':reconciled,'after_forced_mirror_and_repeat_sync':rows(db)}

# The watchlist scanner's real add method also drops its persisted artist profile.
db, default, specific = database('sync-01-watchlist')
scanner = WatchlistScanner.__new__(WatchlistScanner)
scanner._database = db
watch = SimpleNamespace(artist_name='Review artist',spotify_artist_id='review-artist',profile_id=1,quality_profile_id=specific)
assert scanner.add_track_to_wishlist({'id':'watch-track','name':'Watch song','artists':[{'name':'Review artist'}]}, {'id':'watch-album','name':'Watch album'}, watch)
assert rows(db)[0]['quality_profile_id'] == default
out['SYNC-01-watchlist'] = {'requested':specific,'default':default,'after_scan_add':rows(db)}

# SYNC-02: canonical composite row, then a satisfying local file appears.
db, default, specific = database('sync-02')
t = seed(db, specific)
with db._get_connection() as c:
    mirror_projected_tracks_wishlist(db,c,[t])
before = rows(db)
assert before[0]['spotify_track_id'] == 'review-track::review-album'
with db._get_connection() as c:
    c.execute("INSERT INTO lib2_track_files(track_id,path,format,file_state) VALUES(?,'/review-not-accessed/song.flac','flac','active')",(t,))
    c.commit()
    assert track_wishlist_payload(c,t)['_should_queue'] is False
sync_stats = sync_scanned_tracks_wishlist(db,[t])
assert rows(db) == before
# Positive control: old bare-key row is actually withdrawn by the same mirror.
with db._get_connection() as c:
    c.execute("UPDATE wishlist_tracks SET spotify_track_id='review-track'")
    c.commit()
sync_scanned_tracks_wishlist(db,[t])
assert rows(db) == []
out['SYNC-02-satisfying'] = {'canonical_before':before,'after_satisfying_file_and_scan':before,'scan_result':sync_stats,'bare_key_control_removed':True}

# Counter-direction: a release-specific unmonitor passes bare ID to broad cleanup.
db, default, specific = database('sync-02-siblings')
t = seed(db,specific)
t2 = seed(db,specific,album_sid='review-other-album')
with db._get_connection() as c:
    mirror_projected_tracks_wishlist(db,c,[t,t2])
before = rows(db)
assert len(before) == 1
with db._get_connection() as c:
    statuses = [r[0] for r in c.execute('SELECT status FROM lib2_mirror_outbox ORDER BY id')]
assert statuses == ['superseded','done']
out['SYNC-03-release-supersession'] = {'requested_tracks':[t,t2],'after_bulk_mirror':before,'outbox_statuses':statuses}
# Setup correction: drain separately so both release rows actually exist.
with db._get_connection() as c:
    mirror_projected_tracks_wishlist(db,c,[t])
before = rows(db)
assert len(before) == 2
with db._get_connection() as c:
    record_rule(c,'track',t,False,PROVENANCE_USER)
    recompute_wanted(c,track_ids=[t])
    c.commit()
    mirror_projected_tracks_wishlist(db,c,[t])
assert rows(db) == []
out['SYNC-02-release-scope'] = {'before':before,'unmonitor_only_track':t,'other_track':t2,'after':rows(db)}

# SYNC-04: default QUALITY profile is passed as the USER profile namespace.
from core.library2 import autolink
from core.library2.materialize import materialize_track_intent
from core.library2.wanted import track_wanted_states, wanted_projection_status, ensure_wanted_projection
from core.library2.wanted_views import list_cutoff_unmet
from core.quality.model import AudioQuality
db, default, specific = database('sync-04-autolink')
existing = seed(db,default)
with db._get_connection() as c:
    c.execute("UPDATE quality_profiles SET ranked_targets='[{\"label\":\"FLAC\",\"format\":\"flac\"}]', upgrade_policy='acceptable' WHERE id=?",(default,))
    album_id = c.execute('SELECT album_id FROM lib2_tracks WHERE id=?',(existing,)).fetchone()[0]
    record_rule(c,'album',album_id,True,PROVENANCE_USER,profile_id=1)
    c.commit()
ctx = {
    '_final_processed_path':str(Path(db.database_path).parent/'autolink-new.mp3'),
    'lib2_entity':{'album_id':album_id},
    'track_info':{'name':'Autolink new song','artists':[{'name':'Review artist'}],'track_number':2},
}
with patch('database.music_database.get_database',return_value=db), patch.object(autolink,'_warm_new_artwork'), patch('core.imports.file_ops.probe_audio_quality',return_value=AudioQuality('mp3',bitrate=128)):
    file_id = autolink.link_download_into_library_v2(ctx,raise_on_error=True)
assert file_id is not None
with db._get_connection() as c:
    track_id = c.execute('SELECT track_id FROM lib2_track_files WHERE id=?',(file_id,)).fetchone()[0]
    projected = [dict(r) for r in c.execute('SELECT profile_id,wanted,effective_profile_id FROM lib2_wanted_tracks WHERE track_id=?',(track_id,))]
    assert len(projected) == 1 and projected[0]['profile_id'] == default and default != 1
    before = wanted_projection_status(c,profile_id=1)
    assert before['missing'] == 1 and not before['consumer_ready']
    try:
        track_wanted_states(c,[track_id],profile_id=1)
    except RuntimeError as error:
        consumer_error = str(error)
    else:
        raise AssertionError('Missing admin projection must be observable')
    assert list_cutoff_unmet(c,profile_id=1)[1] == 0
    ensure_wanted_projection(c)
    assert wanted_projection_status(c,profile_id=1)['missing'] == 1
    recompute_wanted(c,profile_id=1,track_ids=[track_id])
    assert track_wanted_states(c,[track_id],profile_id=1)[track_id] is True
    assert list_cutoff_unmet(c,profile_id=1)[1] == 1
    # An already materialized intent has an admin projection; autolink does
    # not delete it, but appends the extra wrong-namespace row.
    materialized = materialize_track_intent(c,artist_name='Review artist',album_title='Review album',track_title='Materialized song',track_number=3)
    c.commit()
ctx2 = {'_final_processed_path':str(Path(db.database_path).parent/'materialized.mp3'),
        'lib2_entity':{'track_id':materialized['track_id']},'track_info':{'name':'Materialized song'}}
with patch('database.music_database.get_database',return_value=db), patch.object(autolink,'_warm_new_artwork'), patch('core.imports.file_ops.probe_audio_quality',return_value=AudioQuality('mp3',bitrate=128)):
    assert autolink.link_download_into_library_v2(ctx2,raise_on_error=True) is not None
with db._get_connection() as c:
    materialized_profiles = [r[0] for r in c.execute('SELECT profile_id FROM lib2_wanted_tracks WHERE track_id=? ORDER BY profile_id',(materialized['track_id'],))]
    assert materialized_profiles == [1,default]
out['SYNC-04-autolink-profile-namespace'] = {'quality_default':default,'new_track_id':track_id,'projection_after_autolink':projected,'admin_status_before':before,'consumer_error':consumer_error,'schema_ensure_repairs_admin':False,'cutoff_unmet_before':0,'cutoff_unmet_after_admin_recompute':1,'prematerialized_control_profiles':materialized_profiles}
print(json.dumps(out,indent=2))
