"""Catalogue-only releases must participate in duplicate review."""
from contextlib import closing
import json

from core.repair_jobs.base import JobContext
from tests.library2.test_native_duplicate_review import library  # noqa: F401


def ark_pair(library, *, isrcs=(None, None), same_release=False):
    db = library[0]
    with closing(db._get_connection()) as conn:
        artist = conn.execute("INSERT INTO lib2_artists(name,spotify_id) VALUES('Star Party','artist')").lastrowid
        conn.execute("""CREATE TABLE IF NOT EXISTS metadata_cache_entities(
            source TEXT,entity_type TEXT,entity_id TEXT,name TEXT,album_id TEXT,isrc TEXT,
            duration_ms INTEGER,raw_json TEXT)""")
        albums, tracks = [], []
        for index in range(2):
            album_pid = 'release-a' if same_release else f'release-{index}'
            album = conn.execute("""INSERT INTO lib2_albums(primary_artist_id,title,album_type,
                spotify_id,release_date,origin,monitored,track_count,expected_track_count,tracklist_status,external_ids)
                VALUES(?,'Ark','single',?,'2016-02-04','library',1,1,1,'ready',?)""",
                (artist, album_pid, json.dumps({'deezer': 'same-deezer'} if same_release else {}))).lastrowid
            conn.execute("INSERT INTO lib2_album_artists(album_id,artist_id,role) VALUES(?,?,'primary')", (album, artist))
            track = conn.execute("""INSERT INTO lib2_tracks(album_id,title,spotify_id,track_number,
                disc_number,duration,isrc,monitored) VALUES(?,'Ark',?,1,1,180800,?,1)""",
                (album, f'track-{index}', isrcs[index])).lastrowid
            conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id,role,position) VALUES(?,?,'primary',0)", (track, artist))
            conn.execute("""INSERT INTO metadata_cache_entities(source,entity_type,entity_id,name,album_id,isrc,duration_ms,raw_json) VALUES('spotify','track',?,'Ark',?,
                'GB2LD2010147',180800,?)""", (f'track-{index}', album_pid,
                json.dumps({'id': f'track-{index}', 'album': {'id': album_pid},
                            'external_ids': {'isrc': 'GB2LD2010147'}})))
            albums.append(album)
            tracks.append(track)
        conn.commit()
    return artist, albums, tracks


def scan(library, **kwargs):
    from core.repair_jobs.native_duplicate_detector import NativeDuplicateDetectorJob
    findings = []
    context = JobContext(db=library[0], config_manager=library[1],
        transfer_folder=str(library[3]), create_finding=lambda **f: findings.append(f) or True,
        playlist_membership=lambda: {}, **kwargs)
    return NativeDuplicateDetectorJob().scan(context), findings


def test_fileless_ark_generates_catalogue_review_without_changing_tracks(library):
    _artist, albums, tracks = ark_pair(library)
    result, findings = scan(library)
    assert result.findings_created == 1
    finding = findings[0]
    assert finding['finding_type'] == 'native_duplicate_releases'
    assert {a['album_id'] for a in finding['details']['albums']} == set(albums)
    assert finding['details']['merge_eligible'] is True
    assert finding['details']['auto_merge_eligible'] is False
    with closing(library[0]._get_connection()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0] == 2
        assert [r[0] for r in conn.execute("SELECT id FROM lib2_tracks ORDER BY id")] == tracks


def test_artist_scope_with_no_files_still_reports_only_that_catalogue(library):
    artist, _albums, _tracks = ark_pair(library)
    result, findings = scan(library, scope={"artist_id": artist, "artist_name": "Star Party", "file_paths": []})
    assert result.findings_created == 1
    result, findings = scan(library, scope={"file_paths": []})
    assert not findings
    result, findings = scan(library, should_stop=lambda: True)
    assert result.scanned == 0 and not findings


def test_wrong_release_cache_and_conflicting_isrc_are_review_only(library):
    _artist, _albums, _tracks = ark_pair(library, isrcs=("DIFFERENT", "GB2LD2010147"))
    result, findings = scan(library)
    assert result.findings_created == 1
    assert findings[0]["details"]["merge_eligible"] is False
    with closing(library[0]._get_connection()) as conn:
        conn.execute("UPDATE lib2_tracks SET isrc=NULL")
        conn.execute("UPDATE metadata_cache_entities SET album_id='another-release'")
        conn.commit()
    result, findings = scan(library)
    assert result.findings_created == 1
    assert findings[0]["details"]["merge_eligible"] is False


def test_manual_merge_retains_editions_and_prevents_identity_recreation(library):
    from core.library2.catalogue_duplicates import merge_catalogue_duplicate
    from core.library2.autolink import find_or_create_album, find_or_create_track
    artist, albums, tracks = ark_pair(library)
    _result, findings = scan(library)
    result = merge_catalogue_duplicate(library[0], findings[0]["details"], albums[0])
    assert result["success"] is True
    with closing(library[0]._get_connection()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 1
        assert {r[0] for r in conn.execute("SELECT spotify_id FROM lib2_release_editions")} == {"release-0", "release-1"}
        assert conn.execute("SELECT COUNT(*) FROM lib2_release_editions WHERE is_default=1").fetchone()[0] == 1
        for index in (0, 1):
            assert find_or_create_album(conn, artist, "Ark", album_type="single", spotify_album_id=f"release-{index}", source="spotify") == albums[0]
            assert find_or_create_track(conn, albums[0], artist, "Ark", track_number=1, spotify_track_id=f"track-{index}", source="spotify") == tracks[0]
        conn.rollback()
    assert scan(library)[1] == []


def test_stale_merge_is_rejected_without_partial_changes(library):
    from core.library2.catalogue_duplicates import merge_catalogue_duplicate
    _artist, albums, tracks = ark_pair(library)
    _result, findings = scan(library)
    with closing(library[0]._get_connection()) as conn:
        conn.execute("UPDATE lib2_tracks SET title='Ark (Live)' WHERE id=?", (tracks[1],))
        conn.commit()
    result = merge_catalogue_duplicate(library[0], findings[0]["details"], albums[0])
    assert result["success"] is False
    assert "changed" in result["error"].lower()
    with closing(library[0]._get_connection()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 2


def test_auto_merge_requires_opt_in_and_two_uncontradicted_release_ids(library):
    _artist, _albums, _tracks = ark_pair(library, same_release=True)
    assert scan(library)[0].auto_fixed == 0
    library[1].values['repair.jobs.native_duplicate_detector.settings'] = {'auto_merge_catalogue': True}
    result, findings = scan(library)
    assert result.auto_fixed == 1
    assert not findings
    with closing(library[0]._get_connection()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0] == 1


def test_worker_requires_explicit_album_selection_and_merges_review(library):
    from core.repair_worker import RepairWorker
    _artist, albums, _tracks = ark_pair(library)
    _result, findings = scan(library)
    candidate = findings[0]['details']
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = library[0]
    denied = worker._execute_fix('native_duplicate_releases', 'album', f'lib2:{albums[0]}', None, candidate)
    assert denied['success'] is False
    applied = worker._execute_fix('native_duplicate_releases', 'album', f'lib2:{albums[0]}', None,
                                  {**candidate, '_fix_action': f'merge-{albums[1]}'})
    assert applied['success'] is True
    assert applied['kept_album_id'] == albums[1]


def test_merge_keeps_files_rules_and_replayed_wishlist_identity(library):
    from core.library2.catalogue_duplicates import merge_catalogue_duplicate
    from core.library2.catalogue_identity import canonicalize_wishlist_identity
    _artist, albums, tracks = ark_pair(library)
    paths = [library[2] / 'one.flac', library[2] / 'two.flac']
    with closing(library[0]._get_connection()) as conn:
        for tid, path in zip(tracks, paths, strict=True):
            path.write_bytes(b'original audio')
            conn.execute("INSERT INTO lib2_track_files(track_id,path,format) VALUES(?,?,'flac')", (tid, str(path)))
            conn.execute("INSERT INTO lib2_monitor_rules(entity_type,entity_id,profile_id,monitored,provenance) VALUES('track',?,1,1,'wishlist_import')", (tid,))
        conn.commit()
    from core.library2.catalogue_duplicates import find_catalogue_duplicates
    candidate = find_catalogue_duplicates(library[0])[0]
    assert merge_catalogue_duplicate(library[0], candidate, albums[0])['success']
    with closing(library[0]._get_connection()) as conn:
        assert {r[0] for r in conn.execute('SELECT track_id FROM lib2_track_files')} == {tracks[0]}
        assert conn.execute('SELECT COUNT(*) FROM lib2_monitor_rules').fetchone()[0] == 1
        data, info = canonicalize_wishlist_identity(conn,
            {'id': 'track-1', 'source': 'spotify', 'name': 'Ark', 'album': {'id': 'release-1'}}, {})
        assert data['id'] == 'track-0'
        assert data['album']['id'] == 'release-0'
        assert info['lib2_track_id'] == tracks[0]
    assert all(p.read_bytes() == b'original audio' for p in paths)


def test_active_acquisition_blocks_both_manual_and_auto(library):
    from core.library2.catalogue_duplicates import merge_catalogue_duplicate
    _artist, albums, tracks = ark_pair(library, same_release=True)
    with closing(library[0]._get_connection()) as conn:
        conn.execute('CREATE TABLE acquisition_requests(scope TEXT,entity_id INTEGER,status TEXT)')
        conn.execute("INSERT INTO acquisition_requests VALUES('track',?,'grabbing')", (tracks[1],))
        conn.commit()
    _result, findings = scan(library)
    assert findings[0]['details']['merge_eligible'] is False
    assert not merge_catalogue_duplicate(library[0], findings[0]['details'], albums[0])['success']


def test_active_legacy_download_blocks_merge(library, monkeypatch):
    from core import runtime_state
    from core.library2.catalogue_duplicates import merge_catalogue_duplicate
    _artist, albums, tracks = ark_pair(library)
    monkeypatch.setattr(runtime_state, "download_tasks", {"active": {
        "status": "post_processing", "track_info": {"source_info": {"lib2_track_id": tracks[1]}}}})
    _result, findings = scan(library)
    assert not findings[0]["details"]["merge_eligible"]
    assert not merge_catalogue_duplicate(library[0], findings[0]["details"], albums[0])["success"]


def test_real_release_group_acquisition_blocks_merge(library):
    from core.acquisition.requests import ensure_acquisition_requests_schema
    _artist, albums, _tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        ensure_acquisition_requests_schema(conn)
        conn.execute("""INSERT INTO acquisition_requests(id,profile_id,scope,entity_id,quality_profile_id,idempotency_key,trigger,status)
            VALUES('active',1,'release_group',?,1,'test-active','manual','grabbing')""", (albums[1],))
        conn.commit()
    assert not scan(library)[1][0]["details"]["merge_eligible"]


def test_merged_identity_does_not_rewrite_an_unreviewed_release(library):
    from core.library2.catalogue_duplicates import merge_catalogue_duplicate
    from core.library2.catalogue_identity import canonicalize_wishlist_identity
    _artist, albums, _tracks = ark_pair(library)
    merge_catalogue_duplicate(library[0], scan(library)[1][0]["details"], albums[0])
    data = {"id": "track-1", "provider": "spotify", "album": {"id": "unreviewed-release"}}
    with closing(library[0]._get_connection()) as conn:
        assert canonicalize_wishlist_identity(conn, data, {}) == (data, {})


def test_confirmed_merge_consolidates_wishlist_and_playlist_replay(library, tmp_path):
    from database.music_database import MusicDatabase
    from core.library2.catalogue_duplicates import find_catalogue_duplicates, merge_catalogue_duplicate
    db = MusicDatabase(str(tmp_path / "full.db"))
    fixture = (db, *library[1:])
    artist, albums, tracks = ark_pair(fixture)
    def payload(index):
        return {"id": f"track-{index}", "provider": "spotify", "name": "Ark", "duration_ms": 180800,
                "external_ids": {"isrc": "GB2LD2010147"},
                "artists": [{"id": "artist", "name": "Star Party"}],
                "album": {"id": f"release-{index}", "name": "Ark", "album_type": "single",
                          "release_date": "2016-02-04", "total_tracks": 1}}
    for index in (0, 1):
        assert db.add_to_wishlist(payload(index), source_type="playlist",
            source_info={"playlist_id": f"playlist-{index}", "lib2_track_id": tracks[index], "lib2_album_id": albums[index]})
    candidate = find_catalogue_duplicates(db)[0]
    assert merge_catalogue_duplicate(db, candidate, albums[0])["success"]
    with closing(db._get_connection()) as conn:
        rows = conn.execute("SELECT * FROM wishlist_tracks").fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0]["source_info"])["merged_wishlist_sources"]
    db.add_to_wishlist(payload(1), source_type="playlist", source_info={"playlist_id": "playlist-1"})
    with closing(db._get_connection()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wishlist_tracks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 1


def test_merge_preserves_old_release_and_track_history(library):
    from core.library2.catalogue_duplicates import merge_catalogue_duplicate
    from core.library2.history_feed import scoped_history
    from core.acquisition.requests import ensure_acquisition_requests_schema
    from core.acquisition.history import record_history_event
    from core.library2.entity_history import record_entity_merge
    artist, albums, tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        ensure_acquisition_requests_schema(conn)
        conn.execute("""INSERT INTO acquisition_requests(id,profile_id,scope,entity_id,quality_profile_id,idempotency_key,trigger,status)
            VALUES('old-release',1,'release_group',?,1,'old-release','manual','completed')""", (albums[1],))
        record_history_event(conn, "grab_submitted", request_id="old-release", message="Original edition download")
        record_entity_merge(conn, source_type="track", source_id=9999,
                            target_type="track", target_id=tracks[1], change_source="earlier_review")
        conn.commit()
    assert merge_catalogue_duplicate(library[0], scan(library)[1][0]["details"], albums[0])["success"]
    with closing(library[0]._get_connection()) as conn:
        for aid in albums:
            assert any(e["event_type"] == "grab_submitted" for e in scoped_history(conn, scope="album", entity_id=aid))
        for tid in tracks:
            events = scoped_history(conn, scope="track", entity_id=tid)
            assert len([e for e in events if e["event_type"] == "entity_merged"]) == 2
        assert conn.execute("SELECT entity_id FROM acquisition_requests WHERE id='old-release'").fetchone()[0] == albums[1]


def test_auto_mode_keeps_different_spotify_editions_for_manual_review(library):
    _artist, _albums, _tracks = ark_pair(library)
    library[1].values['repair.jobs.native_duplicate_detector.settings'] = {'auto_merge_catalogue': True}
    result, findings = scan(library)
    assert result.auto_fixed == 0
    assert len(findings) == 1


def test_pinned_album_and_incomplete_catalogue_cannot_merge(library):
    _artist, albums, _tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        conn.execute("UPDATE lib2_albums SET canonical_locked=1,expected_track_count=2 WHERE id=?", (albums[1],))
        conn.commit()
    finding = scan(library)[1][0]
    assert not finding["details"]["merge_eligible"]
    assert any("manual pin" in s for s in finding["details"]["blocked_reasons"])
    assert any("complete" in s for s in finding["details"]["blocked_reasons"])


def test_merge_rolls_back_all_references_on_late_database_failure(library):
    from core.library2.catalogue_duplicates import merge_catalogue_duplicate
    _artist, albums, tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        path = str(library[2] / "retained.flac")
        conn.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)", (tracks[1], path))
        conn.execute(f"""CREATE TRIGGER reject_album_delete BEFORE DELETE ON lib2_albums
                        WHEN OLD.id={albums[1]} BEGIN SELECT RAISE(ABORT,'test failure'); END""")
        conn.commit()
    result = merge_catalogue_duplicate(library[0], scan(library)[1][0]["details"], albums[0])
    assert not result["success"]
    with closing(library[0]._get_connection()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 2
        assert conn.execute("SELECT track_id FROM lib2_track_files").fetchone()[0] == tracks[1]
        assert conn.execute("SELECT COUNT(*) FROM lib2_catalogue_redirects").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM lib2_release_editions").fetchone()[0] == 0


def test_dismissed_review_prevents_automatic_merge(library):
    _artist, _albums, _tracks = ark_pair(library, same_release=True)
    details = scan(library)[1][0]["details"]
    with closing(library[0]._get_connection()) as conn:
        conn.execute("CREATE TABLE repair_findings(finding_type TEXT,status TEXT,details_json TEXT)")
        conn.execute("INSERT INTO repair_findings VALUES('native_duplicate_releases','dismissed',?)", (json.dumps(details),))
        conn.commit()
    library[1].values['repair.jobs.native_duplicate_detector.settings'] = {'auto_merge_catalogue': True}
    assert scan(library)[0].auto_fixed == 0


def test_conflicting_profile_monitoring_is_review_only(library):
    _artist, _albums, tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        for index, tid in enumerate(tracks):
            conn.execute("""INSERT INTO lib2_monitor_rules(entity_type,entity_id,profile_id,monitored,provenance)
                VALUES('track',?,2,?,'user_explicit')""", (tid, index))
        conn.commit()
    details = scan(library)[1][0]["details"]
    assert not details["merge_eligible"]
    assert "Conflicting monitoring decisions." in details["blocked_reasons"]


def test_inherited_album_profile_wanted_survives_merge(library):
    from core.library2.catalogue_duplicates import merge_catalogue_duplicate
    from core.library2.wanted import recompute_wanted
    _artist, albums, tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        conn.execute("""INSERT INTO lib2_monitor_rules(entity_type,entity_id,profile_id,monitored,provenance)
            VALUES('album',?,2,1,'user_explicit')""", (albums[1],))
        recompute_wanted(conn, profile_id=2, track_ids=tracks)
        conn.commit()
    assert merge_catalogue_duplicate(library[0], scan(library)[1][0]["details"], albums[0])["success"]
    with closing(library[0]._get_connection()) as conn:
        assert conn.execute("SELECT wanted FROM lib2_wanted_tracks WHERE track_id=? AND profile_id=2", (tracks[0],)).fetchone()[0] == 1


def test_listening_links_and_play_statistics_survive_merge(library):
    from core.library2.catalogue_duplicates import merge_catalogue_duplicate
    _artist, albums, tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        conn.execute("CREATE TABLE listening_history(id INTEGER PRIMARY KEY,lib2_track_id INTEGER,played_at TEXT)")
        conn.execute("INSERT INTO listening_history VALUES(1,?,'2026-10-01')", (tracks[1],))
        conn.execute("UPDATE lib2_tracks SET play_count=8,last_played='2026-10-01' WHERE id=?", (tracks[1],))
        conn.commit()
    assert merge_catalogue_duplicate(library[0], scan(library)[1][0]["details"], albums[0])["success"]
    with closing(library[0]._get_connection()) as conn:
        track = conn.execute("SELECT play_count,last_played FROM lib2_tracks WHERE id=?", (tracks[0],)).fetchone()
        assert tuple(track) == (8, '2026-10-01')
        assert tuple(conn.execute("SELECT lib2_track_id,played_at FROM listening_history").fetchone()) == (tracks[0], '2026-10-01')


def test_file_monitor_provenance_survives_derived_album_rule(library):
    from core.library2.catalogue_duplicates import merge_catalogue_duplicate
    from core.library2.wanted import recompute_wanted
    _artist, albums, tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        for aid in albums:
            conn.execute("INSERT INTO lib2_monitor_rules(entity_type,entity_id,profile_id,monitored,provenance) VALUES('album',?,1,0,'legacy_import')", (aid,))
        for index, tid in enumerate(tracks):
            conn.execute("INSERT INTO lib2_monitor_rules(entity_type,entity_id,profile_id,monitored,provenance) VALUES('track',?,1,1,?)",
                         (tid, 'file_import' if index else 'legacy_import'))
        recompute_wanted(conn, track_ids=tracks)
        conn.commit()
    assert merge_catalogue_duplicate(library[0], scan(library)[1][0]["details"], albums[0])["success"]
    with closing(library[0]._get_connection()) as conn:
        assert conn.execute("SELECT wanted FROM lib2_wanted_tracks WHERE track_id=? AND profile_id=1", (tracks[0],)).fetchone()[0] == 1


def test_fileless_private_library_intent_is_visible(library, monkeypatch):
    from core.library2 import sql_util
    artist, _albums, tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        for tid in tracks:
            conn.execute("INSERT INTO lib2_monitor_rules(entity_type,entity_id,profile_id,monitored,provenance) VALUES('track',?,2,1,'wishlist_import')", (tid,))
        conn.commit()
    monkeypatch.setattr(sql_util, "ambient_scope", lambda: 2)
    assert scan(library, scope={"artist_id": artist, "file_paths": []})[0].findings_created == 1
    monkeypatch.setattr(sql_util, "ambient_scope", lambda: 3)
    assert scan(library, scope={"artist_id": artist, "file_paths": []})[0].findings_created == 0


def test_distinct_release_pairs_have_distinct_real_findings(library):
    from core.repair_worker import RepairWorker
    from core.library2.catalogue_duplicates import find_catalogue_duplicates
    artist, albums, _tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        third = conn.execute("""INSERT INTO lib2_albums(primary_artist_id,title,album_type,spotify_id,track_count,expected_track_count,tracklist_status)
            VALUES(?,'Ark','single','release-2',1,1,'ready')""", (artist,)).lastrowid
        conn.execute("INSERT INTO lib2_album_artists(album_id,artist_id) VALUES(?,?)", (third, artist))
        conn.execute("INSERT INTO lib2_tracks(album_id,title,spotify_id,isrc,track_number,disc_number,duration) VALUES(?,'Ark','track-2','CONFLICT',1,1,180800)", (third,))
        conn.execute("""CREATE TABLE repair_findings(id INTEGER PRIMARY KEY,job_id TEXT,finding_type TEXT,severity TEXT,
            status TEXT DEFAULT 'pending',entity_type TEXT,entity_id TEXT,file_path TEXT,title TEXT,description TEXT,
            details_json TEXT, user_action TEXT, resolved_at TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,last_error TEXT)""")
        conn.commit()
    worker = RepairWorker.__new__(RepairWorker)
    worker.db, worker._config_manager = library[0], library[1]
    for candidate in find_catalogue_duplicates(library[0]):
        worker._create_finding(job_id='native_duplicate_detector', finding_type='native_duplicate_releases',
            severity='info', entity_type='album', entity_id=f"lib2:{candidate['recommended_album_id']}",
            file_path=None, title='Ark', description='Review', details=candidate)
    with closing(library[0]._get_connection()) as conn:
        rows = conn.execute("SELECT details_json FROM repair_findings WHERE status='pending'").fetchall()
        assert len(rows) == 3
        assert any({a["album_id"] for a in json.loads(r[0])["albums"]} == set(albums) for r in rows)


def test_merge_preserves_approved_wishlist_and_request_lookup(library, tmp_path):
    from database.music_database import MusicDatabase
    from core.library2.catalogue_duplicates import find_catalogue_duplicates, merge_catalogue_duplicate
    db = MusicDatabase(str(tmp_path / "requests.db"))
    _artist, albums, tracks = ark_pair((db, *library[1:]))
    with closing(db._get_connection()) as conn:
        db._ensure_music_request_schema(conn.cursor())
        for index in (0, 1):
            data = {"id": f"track-{index}", "provider": "spotify", "name": "Ark", "album": {"id": f"release-{index}"}}
            info = {"lib2_track_id": tracks[index], "lib2_album_id": albums[index]}
            conn.execute("""INSERT INTO wishlist_tracks(spotify_track_id,spotify_data,source_info,profile_id,
                request_status,request_resolved_at) VALUES(?,?,?,2,?,?)""",
                (f"track-{index}::release-{index}", json.dumps(data), json.dumps(info),
                 'approved' if index else None, '2026-10-01' if index else None))
        conn.commit()
    assert merge_catalogue_duplicate(db, find_catalogue_duplicates(db)[0], albums[0])["success"]
    with closing(db._get_connection()) as conn:
        row = conn.execute("SELECT request_status,request_resolved_at FROM wishlist_tracks WHERE profile_id=2").fetchone()
        assert tuple(row) == ('approved', '2026-10-01')
    assert db.wishlist_has_track(2, 'track-1::release-1')


def test_conflicting_explicit_track_and_album_intent_blocks_merge(library):
    _artist, albums, tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_monitor_rules(entity_type,entity_id,profile_id,monitored,provenance) VALUES('album',?,2,1,'user_explicit')", (albums[1],))
        conn.execute("INSERT INTO lib2_monitor_rules(entity_type,entity_id,profile_id,monitored,provenance) VALUES('track',?,2,0,'user_explicit')", (tracks[0],))
        conn.commit()
    assert not scan(library)[1][0]["details"]["merge_eligible"]


def test_automatic_scan_merges_all_proven_members_of_a_group(library):
    _artist, albums, tracks = ark_pair(library, same_release=True)
    with closing(library[0]._get_connection()) as conn:
        album = conn.execute("""INSERT INTO lib2_albums(primary_artist_id,title,album_type,spotify_id,external_ids,
            track_count,expected_track_count,tracklist_status) SELECT primary_artist_id,title,album_type,
            spotify_id,external_ids,1,1,'ready' FROM lib2_albums WHERE id=?""", (albums[0],)).lastrowid
        conn.execute("INSERT INTO lib2_album_artists(album_id,artist_id) VALUES(?,?)", (album, _artist))
        track = conn.execute("""INSERT INTO lib2_tracks(album_id,title,spotify_id,isrc,track_number,disc_number,duration)
            VALUES(?,'Ark','track-third','GB2LD2010147',1,1,180800)""", (album,)).lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id) VALUES(?,?)", (track, _artist))
        conn.commit()
    from core.library2.catalogue_duplicates import find_catalogue_duplicates
    with closing(library[0]._get_connection()) as conn:
        conn.execute("""CREATE TABLE repair_findings(id INTEGER PRIMARY KEY,finding_type TEXT,status TEXT,
            details_json TEXT,user_action TEXT,resolved_at TEXT,updated_at TEXT)""")
        for candidate in find_catalogue_duplicates(library[0]):
            conn.execute("INSERT INTO repair_findings(finding_type,status,details_json) VALUES('native_duplicate_releases','pending',?)",
                         (json.dumps(candidate),))
        conn.commit()
    library[1].values['repair.jobs.native_duplicate_detector.settings'] = {'auto_merge_catalogue': True}
    result, findings = scan(library)
    assert result.auto_fixed == 2
    assert not findings
    with closing(library[0]._get_connection()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM repair_findings WHERE status='pending'").fetchone()[0] == 0


def test_pair_refresh_does_not_duplicate_a_changed_recommendation(library):
    from core.repair_worker import RepairWorker
    from core.library2.catalogue_duplicates import find_catalogue_duplicates
    _artist, albums, tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        conn.execute("""CREATE TABLE repair_findings(id INTEGER PRIMARY KEY,job_id TEXT,finding_type TEXT,severity TEXT,
            status TEXT DEFAULT 'pending',entity_type TEXT,entity_id TEXT,file_path TEXT,title TEXT,description TEXT,
            details_json TEXT,user_action TEXT,resolved_at TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,last_error TEXT)""")
        conn.commit()
    worker = RepairWorker.__new__(RepairWorker)
    worker.db, worker._config_manager = library[0], library[1]
    def create():
        candidate = find_catalogue_duplicates(library[0])[0]
        return worker._create_finding(job_id='native_duplicate_detector',finding_type='native_duplicate_releases',
            severity='info',entity_type='album',entity_id=f"lib2:{candidate['recommended_album_id']}",
            file_path=None,title='Ark',description='Review',details=candidate)
    assert create()
    with closing(library[0]._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)", (tracks[1], str(library[2]/'new.flac')))
        conn.commit()
    assert not create()
    with closing(library[0]._get_connection()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM repair_findings").fetchone()[0] == 1


def test_surviving_bare_wishlist_key_still_resolves_and_approves(library, tmp_path):
    from database.music_database import MusicDatabase
    from core.library2.catalogue_duplicates import find_catalogue_duplicates, merge_catalogue_duplicate
    db = MusicDatabase(str(tmp_path / "bare.db"))
    _artist, albums, tracks = ark_pair((db, *library[1:]))
    with closing(db._get_connection()) as conn:
        db._ensure_music_request_schema(conn.cursor())
        data = {"id": "track-0", "provider": "spotify", "name": "Ark", "album": {"id": "release-0"}}
        info = {"lib2_track_id": tracks[0], "lib2_album_id": albums[0]}
        conn.execute("INSERT INTO wishlist_tracks(spotify_track_id,spotify_data,source_info,profile_id) VALUES('track-0',?,?,2)",
                     (json.dumps(data), json.dumps(info)))
        conn.commit()
    assert merge_catalogue_duplicate(db, find_catalogue_duplicates(db)[0], albums[0])["success"]
    assert db.wishlist_has_track(2, "track-0")
    assert db.approve_request_rows(2, ["track-0"]) == 1


def test_repeated_track_positions_block_even_proven_release_ids(library):
    artist, albums, tracks = ark_pair(library, isrcs=("GB2LD2010147", "GB2LD2010147"), same_release=True)
    with closing(library[0]._get_connection()) as conn:
        for album, track in zip(albums, tracks, strict=True):
            duplicate = conn.execute("""INSERT INTO lib2_tracks(album_id,title,spotify_id,isrc,track_number,disc_number,duration)
                SELECT album_id,title,spotify_id,isrc,track_number,disc_number,duration FROM lib2_tracks WHERE id=?""", (track,)).lastrowid
            conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id,role,position) VALUES(?,?,'primary',0)", (duplicate, artist))
            conn.execute("UPDATE lib2_albums SET expected_track_count=2,track_count=2 WHERE id=?", (album,))
        conn.commit()
    candidate = scan(library)[1][0]['details']
    assert not candidate['merge_eligible']
    assert not candidate['auto_merge_eligible']


def test_reimport_through_another_credited_artist_keeps_the_merged_release(library):
    from core.library2.autolink import find_or_create_album
    from core.library2.catalogue_duplicates import find_catalogue_duplicates, merge_catalogue_duplicate
    _artist, albums, _tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        other_artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Zookeepers')").lastrowid
        for album in albums:
            conn.execute("INSERT INTO lib2_album_artists(album_id,artist_id,role) VALUES(?,?,'featured')", (album, other_artist))
        conn.commit()
    assert merge_catalogue_duplicate(library[0], find_catalogue_duplicates(library[0])[0], albums[0])['success']
    with closing(library[0]._get_connection()) as conn:
        assert find_or_create_album(conn, other_artist, 'Ark', album_type='single', spotify_album_id='release-1', source='spotify') == albums[0]


def findings_worker(library):
    from core.repair_worker import RepairWorker
    with closing(library[0]._get_connection()) as conn:
        conn.execute("""CREATE TABLE repair_findings(id INTEGER PRIMARY KEY,job_id TEXT,finding_type TEXT,severity TEXT,
            status TEXT DEFAULT 'pending',entity_type TEXT,entity_id TEXT,file_path TEXT,title TEXT,description TEXT,
            details_json TEXT,user_action TEXT,resolved_at TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,last_error TEXT)""")
        conn.commit()
    worker = RepairWorker.__new__(RepairWorker)
    worker.db, worker._config_manager = library[0], library[1]
    return lambda candidate: worker._create_finding(job_id='native_duplicate_detector',
        finding_type='native_duplicate_releases', severity='info', entity_type='album',
        entity_id=f"lib2:{candidate['recommended_album_id']}", file_path=None, title='Ark',
        description='Review', details=candidate)


def wishlist_db(library, tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / "wishlist.db"))
    return (db, *library[1:]), db


def test_background_churn_keeps_the_review_mergeable(library, tmp_path):
    from core.library2.catalogue_duplicates import find_catalogue_duplicates, merge_catalogue_duplicate
    fixture, db = wishlist_db(library, tmp_path)
    _artist, albums, tracks = ark_pair(fixture)
    with closing(db._get_connection()) as conn:
        conn.execute("INSERT INTO wishlist_tracks(spotify_track_id,spotify_data,source_info,profile_id) VALUES('track-1',?,?,1)",
                     (json.dumps({"id": "track-1", "album": {"id": "release-1"}}), json.dumps({"lib2_track_id": tracks[1]})))
        conn.commit()
    candidate = find_catalogue_duplicates(db)[0]
    with closing(db._get_connection()) as conn:
        conn.execute("UPDATE wishlist_tracks SET retry_count=3,last_attempted=CURRENT_TIMESTAMP")
        conn.execute("UPDATE lib2_tracks SET play_count=4,updated_at='2026-10-11'")
        conn.execute("UPDATE lib2_albums SET tracklist_attempts=2,updated_at='2026-10-11'")
        conn.commit()
    assert merge_catalogue_duplicate(db, candidate, albums[0])["success"]


def test_dismissed_pair_stays_dismissed_after_background_changes(library):
    from core.library2.catalogue_duplicates import find_catalogue_duplicates
    _artist, _albums, tracks = ark_pair(library)
    create = findings_worker(library)
    assert create(find_catalogue_duplicates(library[0])[0])
    with closing(library[0]._get_connection()) as conn:
        conn.execute("UPDATE repair_findings SET status='dismissed'")
        conn.execute("UPDATE lib2_tracks SET play_count=7,updated_at='2026-10-11'")
        conn.commit()
    assert not create(find_catalogue_duplicates(library[0])[0])


def test_catalogue_option_stays_out_of_file_review_settings(library):
    from tests.library2.test_native_duplicate_review import copy
    copy(library, album="Single", fmt="mp3", bitrate=320)
    copy(library, album="Album", fmt="flac", bitrate=0)
    library[1].values['repair.jobs.native_duplicate_detector.settings'] = {'auto_merge_catalogue': True}
    files = [f for f in scan(library)[1] if f['finding_type'] == 'native_duplicate_tracks']
    assert files and 'auto_merge_catalogue' not in files[0]['details']['settings']


def test_settled_downloads_and_waiting_requests_do_not_block(library, monkeypatch):
    from core import runtime_state
    from core.acquisition.requests import ensure_acquisition_requests_schema
    from core.library2.catalogue_duplicates import merge_catalogue_duplicate
    _artist, albums, tracks = ark_pair(library)
    monkeypatch.setattr(runtime_state, "download_tasks", {"gone": {
        "status": "not_found", "track_info": {"source_info": {"lib2_track_id": tracks[1]}}}})
    with closing(library[0]._get_connection()) as conn:
        ensure_acquisition_requests_schema(conn)
        conn.execute("""INSERT INTO acquisition_requests(id,profile_id,scope,entity_id,quality_profile_id,idempotency_key,trigger,status)
            VALUES('wait',1,'release_group',?,1,'wait','monitor','no_candidate')""", (albums[1],))
        conn.commit()
    details = scan(library)[1][0]["details"]
    assert details["merge_eligible"]
    assert merge_catalogue_duplicate(library[0], details, albums[0])["success"]
    with closing(library[0]._get_connection()) as conn:
        assert conn.execute("SELECT status FROM acquisition_requests WHERE id='wait'").fetchone()[0] == "cancelled"


def test_survivor_keeps_its_unmonitor_and_its_own_release_identity(library):
    from core.library2.catalogue_duplicates import merge_catalogue_duplicate
    _artist, albums, _tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        conn.execute("UPDATE lib2_albums SET monitored=0 WHERE id=?", (albums[0],))
        conn.execute("UPDATE lib2_albums SET external_ids='{\"deezer\":\"dz-1\",\"upc\":\"123\"}' WHERE id=?", (albums[1],))
        conn.execute("INSERT INTO lib2_monitor_rules(entity_type,entity_id,profile_id,monitored,provenance) VALUES('album',?,1,0,'user_explicit')", (albums[0],))
        conn.commit()
    assert merge_catalogue_duplicate(library[0], scan(library)[1][0]["details"], albums[0])["success"]
    with closing(library[0]._get_connection()) as conn:
        assert tuple(conn.execute("SELECT monitored,external_ids FROM lib2_albums").fetchone())[0] == 0
        assert "dz-1" not in (conn.execute("SELECT external_ids FROM lib2_albums").fetchone()[0] or "")
        editions = dict(conn.execute("SELECT spotify_id,external_ids FROM lib2_release_editions").fetchall())
        assert "dz-1" not in editions["release-0"] and "dz-1" in editions["release-1"]


def test_other_provider_replay_and_removal_reach_the_merged_row(library, tmp_path):
    from core.library2.catalogue_duplicates import find_catalogue_duplicates, merge_catalogue_duplicate
    fixture, db = wishlist_db(library, tmp_path)
    _artist, albums, tracks = ark_pair(fixture)
    with closing(db._get_connection()) as conn:
        conn.execute("UPDATE lib2_albums SET external_ids='{\"deezer\":\"dzalb\"}' WHERE id=?", (albums[1],))
        conn.execute("UPDATE lib2_tracks SET external_ids='{\"deezer\":\"dz\"}' WHERE id=?", (tracks[1],))
        conn.commit()
    artists = [{"id": "artist", "name": "Star Party"}]
    db.add_to_wishlist({"id": "track-0", "provider": "spotify", "name": "Ark", "artists": artists,
                        "album": {"id": "release-0", "name": "Ark"}}, source_type="playlist",
                       source_info={"lib2_track_id": tracks[0], "lib2_album_id": albums[0]})
    assert merge_catalogue_duplicate(db, find_catalogue_duplicates(db)[0], albums[0])["success"]
    db.add_to_wishlist({"id": "dz", "source": "deezer", "name": "Ark", "artists": artists,
                        "album": {"id": "dzalb", "name": "Ark"}}, source_type="playlist", source_info={})
    count = "SELECT COUNT(*) FROM wishlist_tracks"
    with closing(db._get_connection()) as conn:
        assert conn.execute(count).fetchone()[0] == 1
    assert db.remove_from_wishlist("track-1")
    with closing(db._get_connection()) as conn:
        assert conn.execute(count).fetchone()[0] == 0


def test_unlinked_row_under_the_canonical_key_joins_the_merge(library, tmp_path):
    from core.library2.catalogue_duplicates import find_catalogue_duplicates, merge_catalogue_duplicate
    fixture, db = wishlist_db(library, tmp_path)
    _artist, albums, tracks = ark_pair(fixture)
    with closing(db._get_connection()) as conn:
        for key, info in (("track-0::release-0", {}), ("track-1::release-1", {"lib2_track_id": tracks[1]})):
            track, album = key.split("::")
            conn.execute("INSERT INTO wishlist_tracks(spotify_track_id,spotify_data,source_info,profile_id) VALUES(?,?,?,1)",
                         (key, json.dumps({"id": track, "album": {"id": album}}), json.dumps(info)))
        conn.commit()
    assert merge_catalogue_duplicate(db, find_catalogue_duplicates(db)[0], albums[0])["success"]
    with closing(db._get_connection()) as conn:
        rows = conn.execute("SELECT spotify_track_id,source_info FROM wishlist_tracks").fetchall()
        assert [r[0] for r in rows] == ["track-0::release-0"]
        assert len(json.loads(rows[0][1])["merged_wishlist_sources"]) == 2


def test_ambiguous_provider_alias_falls_back_to_ordinary_matching(library):
    from core.library2.catalogue_identity import provider_alias
    _artist, albums, _tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        conn.executemany("INSERT INTO lib2_catalogue_provider_aliases VALUES('album','spotify','shared',?)", [(a,) for a in albums])
        assert provider_alias(conn, "album", "spotify", "shared") is None


def test_merge_refreshes_the_survivors_other_pending_reviews(library):
    from core.library2.catalogue_duplicates import find_catalogue_duplicates, merge_catalogue_duplicate
    artist, albums, _tracks = ark_pair(library)
    with closing(library[0]._get_connection()) as conn:
        third = conn.execute("""INSERT INTO lib2_albums(primary_artist_id,title,album_type,spotify_id,track_count,expected_track_count,tracklist_status)
            VALUES(?,'Ark','single','release-2',1,1,'ready')""", (artist,)).lastrowid
        conn.execute("INSERT INTO lib2_tracks(album_id,title,spotify_id,isrc,track_number,disc_number,duration) VALUES(?,'Ark','track-2','CONFLICT',1,1,180800)", (third,))
        conn.commit()
    create = findings_worker(library)
    candidates = find_catalogue_duplicates(library[0])
    for candidate in candidates:
        create(candidate)
    pair = next(c for c in candidates if {a["id"] for a in c["albums"]} == set(albums))
    assert merge_catalogue_duplicate(library[0], pair, albums[0])["success"]
    with closing(library[0]._get_connection()) as conn:
        pending = [json.loads(r[0])["snapshot"] for r in conn.execute("SELECT details_json FROM repair_findings WHERE status='pending'")]
    assert pending == [c["snapshot"] for c in find_catalogue_duplicates(library[0])]


def test_routes_answer_a_merged_id_with_its_survivor(library):
    import pytest
    flask = pytest.importorskip("flask")
    from api.library_v2 import register_library_v2_routes
    from core.library2.catalogue_duplicates import merge_catalogue_duplicate
    _artist, albums, tracks = ark_pair(library)
    assert merge_catalogue_duplicate(library[0], scan(library)[1][0]["details"], albums[0])["success"]
    app = flask.Flask(__name__)
    register_library_v2_routes(app, get_database=lambda: library[0], config_get=lambda key, default=None: default,
                               config_manager=None, profile_id_getter=lambda: 1)
    client = app.test_client()
    assert client.get(f"/api/library/v2/albums/{albums[1]}").get_json()["album"]["id"] == albums[0]
    assert client.get(f"/api/library/v2/tracks/{tracks[1]}").get_json()["track"]["id"] == tracks[0]
