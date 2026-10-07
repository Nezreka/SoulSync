"""Expired Download Cleaner job: scan protection + findings vs auto-delete,
and the shared delete helper.

The pure expiry logic is tested in tests/library/test_expired_cleanup.py; this
covers the job's fact-gathering (play_count, active-mirror/watch protection)
and the two modes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.repair_jobs.expired_download_cleaner import (
    ExpiredDownloadCleanerJob,
    delete_origin_download,
)

OLD = (datetime.now(timezone.utc) - timedelta(days=120)).strftime("%Y-%m-%d %H:%M:%S")
NEW = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")


class _DB:
    def __init__(self, candidates, mirrored=None, watched=None, mirror_tracks=None):
        self._candidates = candidates
        self._mirrored = mirrored or []
        self._watched = watched or []
        # {playlist_id: [track dicts]} ; a None value means the read fails
        # (membership unknown → name-level protection). omitted ids mean the
        # mirror row has no id (also unreadable).
        self._mirror_tracks = mirror_tracks if mirror_tracks is not None else {}
        self.deleted_paths = []
        self.deleted_history = []

    def get_all_profiles(self):
        return [{'id': 1}]

    def get_origin_cleanup_candidates(self):
        return [dict(c) for c in self._candidates]

    def get_mirrored_playlists(self, profile_id=1):
        return [dict(m) if isinstance(m, dict) else {'name': m}
                for m in self._mirrored]

    def get_mirrored_playlist_tracks(self, playlist_id, profile_id=None):
        if playlist_id not in self._mirror_tracks:
            raise RuntimeError(f"no tracks recorded for playlist {playlist_id}")
        tracks = self._mirror_tracks[playlist_id]
        if tracks is None:
            raise RuntimeError("mirror track list unreadable")
        return [dict(t) for t in tracks]

    def get_watchlist_artists(self, profile_id=1):
        return [SimpleNamespace(artist_name=n) for n in self._watched]

    def delete_track_by_file_path(self, p):
        self.deleted_paths.append(p)
        return 1

    def delete_library_history_rows(self, ids):
        self.deleted_history.extend(ids)
        return len(ids)


class _DBNoTrackReader(_DB):
    # a database that predates get_mirrored_playlist_tracks: the job must
    # fall back to name-level protection, not crash or expose downloads.
    get_mirrored_playlist_tracks = None


def _mirror(pid, name, custom_name=None):
    d = {'id': pid, 'name': name}
    if custom_name:
        d['custom_name'] = custom_name
    return d


def _ctx(db, settings, findings):
    return SimpleNamespace(
        db=db,
        config_manager=SimpleNamespace(get=lambda k, d=None: settings if k.endswith('.settings') else d),
        check_stop=lambda: False, wait_if_paused=lambda: False,
        update_progress=lambda *a, **k: None, report_progress=lambda *a, **k: None,
        create_finding=lambda **kw: (findings.append(kw) or True),
        transfer_folder=None,
    )


def _cand(eid, origin="playlist", created=OLD, play_count=0, ctx="Some Playlist", path=None):
    return {"id": eid, "origin": origin, "origin_context": ctx, "created_at": created,
            "file_path": path or f"/music/{eid}.flac", "title": f"T{eid}",
            "artist_name": "Artist", "play_count": play_count}


# ── scan: findings mode + protections ────────────────────────────────────────

def test_scan_noop_when_both_retentions_off():
    db = _DB([_cand(1)])
    findings = []
    res = ExpiredDownloadCleanerJob().scan(_ctx(db, {}, findings))   # defaults: both off
    assert res.findings_created == 0 and findings == []


def test_scan_creates_findings_for_expired():
    db = _DB([
        _cand(1, created=OLD, play_count=0),            # expired
        _cand(2, created=NEW, play_count=0),            # too new
        _cand(3, created=OLD, play_count=5),            # listened → keep
    ])
    findings = []
    res = ExpiredDownloadCleanerJob().scan(_ctx(
        db, {'playlist_retention': '2mo', 'keep_if_played_at_least': 2}, findings))
    assert res.findings_created == 1
    assert findings[0]['details']['history_id'] == 1
    assert findings[0]['finding_type'] == 'expired_download'


def test_scan_protects_actively_mirrored_playlist():
    # track still in the mirror's current track list → protected by
    # membership, not merely by the playlist name.
    db = _DB(
        [_cand(1, origin="playlist", ctx="My Mix", created=OLD)],
        mirrored=[_mirror(1, "My Mix")],
        mirror_tracks={1: [{'artist_name': 'Artist', 'track_name': 'T1'}]},
    )
    findings = []
    ExpiredDownloadCleanerJob().scan(_ctx(db, {'playlist_retention': '1w'}, findings))
    assert findings == []   # still in the playlist → protected


def test_scan_protects_despite_title_drift():
    # the mirror row carries the playlist source's title ("T1 (Remastered)")
    # while the download was recorded under the discovery provider's matched
    # title ("T1") — qualifier drift must not unprotect the track.
    db = _DB(
        [_cand(1, origin="playlist", ctx="My Mix", created=OLD)],
        mirrored=[_mirror(1, "My Mix")],
        mirror_tracks={1: [{'artist_name': 'Artist',
                            'track_name': 'T1 (Remastered)'}]},
    )
    findings = []
    ExpiredDownloadCleanerJob().scan(_ctx(db, {'playlist_retention': '1w'}, findings))
    assert findings == []


def test_scan_track_rotated_out_of_mirrored_playlist_expires():
    # the reported scenario: playlist still mirrored, but this track is no
    # longer in its current track list → not protected → finding created.
    db = _DB(
        [_cand(1, origin="playlist", ctx="Your Listening Mix", created=OLD,
               play_count=0)],
        mirrored=[_mirror(7, "Your Listening Mix")],
        mirror_tracks={7: [{'artist_name': 'Other Artist',
                            'track_name': 'Other Song'}]},
    )
    findings = []
    res = ExpiredDownloadCleanerJob().scan(
        _ctx(db, {'playlist_retention': '1w'}, findings))
    assert res.findings_created == 1
    assert findings[0]['details']['history_id'] == 1


def test_scan_track_still_in_mirrored_playlist_protected():
    db = _DB(
        [_cand(1, origin="playlist", ctx="Your Listening Mix", created=OLD,
               play_count=0)],
        mirrored=[_mirror(7, "Your Listening Mix")],
        # differently cased/padded — the membership key is normalized
        mirror_tracks={7: [{'artist_name': '  ARTIST ', 'track_name': 't1'}]},
    )
    findings = []
    ExpiredDownloadCleanerJob().scan(_ctx(db, {'playlist_retention': '1w'}, findings))
    assert findings == []   # still in the playlist → protected


def test_scan_custom_name_matches_membership():
    db = _DB(
        [_cand(1, origin="playlist", ctx="Renamed Mix", created=OLD,
               play_count=0)],
        mirrored=[_mirror(7, "Upstream Name", custom_name="Renamed Mix")],
        mirror_tracks={7: [{'artist_name': 'Someone Else',
                            'track_name': 'Some Song'}]},
    )
    findings = []
    res = ExpiredDownloadCleanerJob().scan(
        _ctx(db, {'playlist_retention': '1w'}, findings))
    assert res.findings_created == 1  # renamed mirror, track rotated out


def test_scan_unreadable_mirror_tracks_keep_name_protection():
    db = _DB(
        [_cand(1, origin="playlist", ctx="My Mix", created=OLD, play_count=0)],
        mirrored=[_mirror(7, "My Mix")],
        mirror_tracks={7: None},  # read fails → membership unknown → keep
    )
    findings = []
    ExpiredDownloadCleanerJob().scan(_ctx(db, {'playlist_retention': '1w'}, findings))
    assert findings == []


def test_scan_no_track_reader_method_keeps_name_protection():
    db = _DBNoTrackReader(
        [_cand(1, origin="playlist", ctx="My Mix", created=OLD, play_count=0)],
        mirrored=["My Mix"],
    )
    findings = []
    ExpiredDownloadCleanerJob().scan(_ctx(db, {'playlist_retention': '1w'}, findings))
    assert findings == []


def test_scan_unidentifiable_candidate_stays_protected():
    c = _cand(1, origin="playlist", ctx="My Mix", created=OLD, play_count=0)
    c['title'] = ''
    c['artist_name'] = ''
    db = _DB([c], mirrored=[_mirror(7, "My Mix")],
             mirror_tracks={7: [{'artist_name': 'X', 'track_name': 'Y'}]})
    findings = []
    ExpiredDownloadCleanerJob().scan(_ctx(db, {'playlist_retention': '1w'}, findings))
    assert findings == []   # cannot prove the track left → keep


def test_scan_origin_playlist_no_longer_mirrored_expires():
    db = _DB(
        [_cand(1, origin="playlist", ctx="Deleted Mix", created=OLD,
               play_count=0)],
        mirrored=[_mirror(7, "Your Listening Mix")],
        mirror_tracks={7: []},
    )
    findings = []
    res = ExpiredDownloadCleanerJob().scan(
        _ctx(db, {'playlist_retention': '1w'}, findings))
    assert res.findings_created == 1


def test_scan_protects_watched_artist():
    db = _DB([_cand(1, origin="watchlist", ctx="Drake", created=OLD)],
             watched=["Drake"])
    findings = []
    ExpiredDownloadCleanerJob().scan(_ctx(db, {'watchlist_retention': '1w'}, findings))
    assert findings == []   # still watched → protected


def test_scan_dry_run_default_is_findings_only():
    # No dry_run in settings → defaults to True → findings, never deletes.
    db = _DB([_cand(1, created=OLD, path="/music/x.flac")])
    findings = []
    res = ExpiredDownloadCleanerJob().scan(_ctx(db, {'playlist_retention': '2mo'}, findings))
    assert res.findings_created == 1 and db.deleted_history == []   # nothing deleted


def test_scan_auto_delete_when_dry_run_off():
    db = _DB([_cand(1, created=OLD, path="/music/x.flac")])
    findings = []
    res = ExpiredDownloadCleanerJob().scan(_ctx(
        db, {'playlist_retention': '2mo', 'dry_run': False}, findings))
    assert findings == []                       # no findings in auto mode
    assert res.auto_fixed == 1
    assert 1 in db.deleted_history             # history row removed
    assert "/music/x.flac" in db.deleted_paths # track row removed


# ── delete helper ────────────────────────────────────────────────────────────

def test_delete_origin_download_missing_file(tmp_path):
    # File doesn't exist → still cleans up the history row (orphan), no error.
    db = _DB([])
    entry = {"id": 9, "file_path": str(tmp_path / "gone.flac")}
    cfg = SimpleNamespace(get=lambda k, d=None: d)
    res = delete_origin_download(db, entry, cfg)
    assert res["error"] is None and res["file_deleted"] is False
    assert db.deleted_history == [9]


def test_delete_origin_download_keeps_navidrome_virtual_path():
    db = _DB([])
    entry = {"id": 12, "file_path": "Muse/The Wow! Signal/01-06 - Hexagons.flac"}
    cfg = SimpleNamespace(
        get=lambda k, d=None: d,
        get_active_media_server=lambda: "navidrome",
    )
    res = delete_origin_download(db, entry, cfg)
    assert res["removed"] == 0
    assert res["file_deleted"] is False
    assert "Report Real Path" in res["error"]
    assert db.deleted_history == []
    assert db.deleted_paths == []


def test_delete_origin_download_removes_real_file(tmp_path):
    f = tmp_path / "song.flac"; f.write_bytes(b"x")
    db = _DB([])
    entry = {"id": 5, "file_path": str(f)}
    cfg = SimpleNamespace(get=lambda k, d=None: d)
    res = delete_origin_download(db, entry, cfg)
    assert res["file_deleted"] is True and not f.exists()
    assert db.deleted_history == [5]


# ── #1416: every profile's mirrors and watchlists protect, under every name ──

@pytest.fixture()
def real_db(tmp_path):
    from database.music_database import MusicDatabase

    class _Real(MusicDatabase):
        candidates = []

        def get_origin_cleanup_candidates(self):
            return [dict(c) for c in self.candidates]

    return _Real(str(tmp_path / 'm.db'))


def _expired_ids(db):
    findings = []
    ctx = _ctx(db, {'playlist_retention': '2mo', 'watchlist_retention': '2mo',
                    'keep_if_played_at_least': 2, 'use_curation_signals': False}, findings)
    ctx.config_manager.get_active_media_server = lambda: 'navidrome'
    ExpiredDownloadCleanerJob().scan(ctx)
    return {f['details']['history_id'] for f in findings}


def test_another_profiles_mirror_and_watchlist_protect_their_downloads(real_db):
    thomas = real_db.create_profile('ThomasClan')
    real_db.mirror_playlist('spotify', 'p-t', 'Thomas Mix', [], profile_id=thomas)
    real_db.add_artist_to_watchlist('art-1', 'Kavinsky', profile_id=thomas)
    real_db.candidates = [_cand(1, ctx='Thomas Mix'), _cand(2, origin='watchlist', ctx='Kavinsky'),
                          _cand(3, ctx='Deleted Playlist')]
    assert _expired_ids(real_db) == {3}


def test_a_renamed_or_suffixed_mirror_still_protects(real_db):
    thomas = real_db.create_profile('ThomasClan')
    mid = real_db.mirror_playlist('spotify', 'p-r', 'Upstream Name', [], profile_id=thomas)
    real_db.set_mirrored_playlist_custom_name(mid, 'My Rename', profile_id=thomas)
    real_db.mirror_playlist('spotify', 'rr-a', 'Release Radar', [], profile_id=1)
    real_db.mirror_playlist('spotify', 'rr-t', 'Release Radar', [], profile_id=thomas)
    real_db.candidates = [_cand(1, ctx='My Rename'), _cand(2, ctx='Release Radar - ThomasClan'),
                          _cand(3, ctx='Upstream Name')]
    assert _expired_ids(real_db) == set()


def test_1416_membership_nonempty_other_profile_mirror_protects(real_db):
    # same as test_another_profiles_mirror_and_watchlist_protect_their_downloads
    # but with a real track list: protection must come from membership, and a
    # non-member of a still-mirrored playlist must expire.
    thomas = real_db.create_profile('ThomasClan')
    real_db.mirror_playlist('spotify', 'p-t', 'Thomas Mix',
                            [{'track_name': 'T1', 'artist_name': 'Artist'}],
                            profile_id=thomas)
    real_db.candidates = [_cand(1, ctx='Thomas Mix'),
                          _cand(2, ctx='Thomas Mix'),
                          _cand(3, ctx='Deleted Playlist')]
    # candidate 2 is T2 — not in the mirror's track list → expires
    assert _expired_ids(real_db) == {2, 3}


def test_1416_membership_nonempty_rename_and_suffixed_names_protect(real_db):
    # rename / upstream / collision-suffixed names all resolve to the same
    # mirror's track list; a track not in that list still expires.
    thomas = real_db.create_profile('ThomasClan')
    mid = real_db.mirror_playlist('spotify', 'p-r', 'Upstream Name',
                                  [{'track_name': 'T1', 'artist_name': 'Artist'}],
                                  profile_id=thomas)
    real_db.set_mirrored_playlist_custom_name(mid, 'My Rename', profile_id=thomas)
    # a same-named mirror on profile 1 forces the collision-suffixed sync name
    # 'Release Radar - ThomasClan' for the thomas mirror.
    real_db.mirror_playlist('spotify', 'rr-a', 'Release Radar',
                            [{'track_name': 'T9', 'artist_name': 'Artist'}],
                            profile_id=1)
    real_db.mirror_playlist('spotify', 'rr-t', 'Release Radar',
                            [{'track_name': 'T2', 'artist_name': 'Artist'}],
                            profile_id=thomas)
    real_db.candidates = [_cand(1, ctx='My Rename'),
                          _cand(2, ctx='Release Radar - ThomasClan'),
                          _cand(3, ctx='Upstream Name')]
    # candidate 3 is T3 — the 'Upstream Name' mirror only lists T1 → expires
    assert _expired_ids(real_db) == {3}


def test_1416_membership_nonempty_rotated_out_track_expires(real_db):
    thomas = real_db.create_profile('ThomasClan')
    real_db.mirror_playlist('spotify', 'p-t', 'Thomas Mix',
                            [{'track_name': 'Other Song', 'artist_name': 'Other Artist'}],
                            profile_id=thomas)
    real_db.candidates = [_cand(1, ctx='Thomas Mix')]
    assert _expired_ids(real_db) == {1}


# ── matched-id membership: the provider credits the artist differently ──────

def test_scan_still_in_playlist_by_matched_id_protected():
    # real shape from a discover weekly mirror: the mirror row says
    # "Good Times Ahead", the deezer match (and so the download) says "GTA".
    # artist|title misses, the matched id must still protect it.
    cand = _cand(1, ctx="Discover Weekly", created=OLD)
    cand.update(artist_name="GTA", title="Little Bit of This (feat. Vince Staples)",
                source_track_id="132123164")
    db = _DB(
        [cand],
        mirrored=[_mirror(7, "Discover Weekly")],
        mirror_tracks={7: [{
            'artist_name': 'Good Times Ahead',
            'track_name': 'Little Bit of This (feat. Vince Staples)',
            'source_track_id': '1K0VQGwAHUSavDdPZ2aTAY',
            'extra_data': '{"matched_data": {"id": "132123164"}, '
                          '"spotify_hint": {"id": "1K0VQGwAHUSavDdPZ2aTAY"}}',
        }]},
    )
    findings = []
    ExpiredDownloadCleanerJob().scan(_ctx(db, {'playlist_retention': '1w'}, findings))
    assert findings == []


def test_scan_rotated_out_track_with_unrelated_id_still_expires():
    # the id check only adds protection for a real id hit; a rotated-out
    # track whose id is nowhere in the list still expires.
    cand = _cand(1, ctx="Discover Weekly", created=OLD)
    cand.update(source_track_id="999")
    db = _DB(
        [cand],
        mirrored=[_mirror(7, "Discover Weekly")],
        mirror_tracks={7: [{'artist_name': 'Other', 'track_name': 'Song',
                            'source_track_id': 'abc',
                            'extra_data': '{"matched_data": {"id": "123"}}'}]},
    )
    findings = []
    res = ExpiredDownloadCleanerJob().scan(_ctx(db, {'playlist_retention': '1w'}, findings))
    assert res.findings_created == 1


def test_origin_cleanup_candidates_carry_source_track_id(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO library_history (event_type, title, artist_name, file_path, "
            "origin, origin_context, source_track_id) "
            "VALUES ('download', 'T', 'A', '/m/t.flac', 'playlist', 'Mix', '132123164')")
        conn.commit()
    rows = db.get_origin_cleanup_candidates()
    assert rows and rows[0]['source_track_id'] == '132123164'
