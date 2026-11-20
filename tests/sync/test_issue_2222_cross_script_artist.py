"""#2222: itunes names Despina Vandi "Δέσποινα Βανδή"; the mirrored playlist,
plex and the library all say "Despina Vandi". the sync searched the library
under the greek name, found nothing, and the track stayed off the plex
playlist (not_found, confidence 0) while the same run called it owned. a
sync_match_cache row from an earlier sync under the deezer id never got a
look because the later sync carried the itunes id. the download that
followed filed a second copy under a greek artist folder.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase
from tests.sync.test_issue_1715_album_fallback_artist_gate import _make_db


def _vandi_db(tmp_path, *, aliases=None, server='plex'):
    db = _make_db(
        tmp_path,
        artists=[('25000', 'Despina Vandi', server), ('26000', 'Sakis Rouvas', server)],
        albums=[('al-gia', '25000', "Gia - Collector's Edition", server),
                ('al-shake', '26000', 'Shake It', server)],
        tracks=[('25054', 'al-gia', '25000', 'Anaveis Foties', None, server),
                ('26054', 'al-shake', '26000', 'Shake It (Eurovision Version)', None, server)],
    )
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_tracks SET isrc = 'GRHV10100075' WHERE title = 'Anaveis Foties'")
        if aliases is not None:
            import json
            conn.execute("UPDATE lib2_artists SET aliases = ? WHERE name = 'Despina Vandi'",
                         (json.dumps(aliases, ensure_ascii=False),))
        conn.commit()
    return db


class TestLibraryFindsTheArtistUnderAnotherScript:
    def test_greek_name_finds_the_latin_library_artist(self, tmp_path):
        db = _vandi_db(tmp_path)
        track, conf = db.check_track_exists(
            'Anaveis Foties', 'Δέσποινα Βανδή', confidence_threshold=0.7,
            server_source='plex', album="Gia - Collector's Edition")
        assert track is not None and str(track.id) == '25054'
        assert conf >= 0.7

    def test_sakis_rouvas(self, tmp_path):
        db = _vandi_db(tmp_path)
        track, _ = db.check_track_exists(
            'Shake It (Eurovision Version)', 'Σάκης Ρουβάς', confidence_threshold=0.7,
            server_source='plex')
        assert track is not None and str(track.id) == '26054'

    def test_musicbrainz_alias_bridges_a_spelling_romanization_misses(self, tmp_path):
        # a library artist whose stored musicbrainz names list the source's
        db = _vandi_db(tmp_path, aliases=['Δέσποινα Βανδή-Παπαδοπούλου', 'Vandi, Despina'])
        track, _ = db.check_track_exists(
            'Anaveis Foties', 'Δέσποινα Βανδή-Παπαδοπούλου', confidence_threshold=0.7,
            server_source='plex')
        assert track is not None and str(track.id) == '25054'

    def test_a_different_song_by_the_artist_is_still_no_match(self, tmp_path):
        db = _vandi_db(tmp_path)
        track, _ = db.check_track_exists(
            'Kapou Allou', 'Δέσποινα Βανδή', confidence_threshold=0.7, server_source='plex')
        assert track is None

    def test_latin_library_is_unchanged_for_latin_names(self, tmp_path):
        db = _vandi_db(tmp_path)
        assert db._artist_alias_names('Despina Vandi') == []


class TestOwnedCheckUnderAnotherScript:
    def test_find_owned_match_accepts_the_latin_credit(self, tmp_path):
        from core.wishlist.library_match import find_owned_match
        db = _vandi_db(tmp_path)
        owned = find_owned_match(db, 'Anaveis Foties', [{'name': 'Δέσποινα Βανδή'}],
                                 "Gia - Collector's Edition", 'plex', strict_identity=True)
        assert owned is not None
        assert str(owned[0].id) == '25054'

    def test_strict_identity_still_refuses_an_unrelated_credit(self, tmp_path):
        from types import SimpleNamespace
        from core.wishlist.library_match import _strict_identity_matches
        row = SimpleNamespace(title='Anaveis Foties', album_title='Gia',
                              artist_name='Someone Else', track_artist=None)
        assert not _strict_identity_matches(row, 'Anaveis Foties', 'Δέσποινα Βανδή', None, False,
                                            lambda name: ['Despina Vandi'])
        row.artist_name = 'Despina Vandi'
        assert _strict_identity_matches(row, 'Anaveis Foties', 'Δέσποινα Βανδή', None, False,
                                        lambda name: ['Despina Vandi'])
        assert not _strict_identity_matches(row, 'Anaveis Foties', 'Δέσποινα Βανδή', None, False)


# ---------------------------------------------------------------------------
# the playlist sync, both matchers
# ---------------------------------------------------------------------------

from tests.sync.test_issue_1721_stale_sync_match_cache import (  # noqa: E402
    _db_only_match, _media_match)


def _itunes_track(**kw):
    """the reporter's sync_history track: itunes id, greek artist, the
    mirrored playlist's latin artist riding along."""
    from core.spotify_client import Track as SpotifyTrack
    base = dict(id='1606586044', name='Anaveis Foties', artists=['Δέσποινα Βανδή'],
                album="Gia - Collector's Edition", duration_ms=0, popularity=0,
                source_track_id='spotify-mirror-1', source_artists=['Despina Vandi'])
    base.update(kw)
    return SpotifyTrack(**base)


MATCHERS = [pytest.param(_media_match, id='media'), pytest.param(_db_only_match, id='db-only')]


@pytest.mark.parametrize('match', MATCHERS)
class TestSyncFindsTheOwnedTrack:
    def test_greek_itunes_artist_matches_the_latin_library_track(self, tmp_path, match):
        db = _vandi_db(tmp_path, server='navidrome')
        found, conf = match(db, _itunes_track(source_artists=None))
        assert found is not None and str(found.id) == '25054'
        assert conf >= 0.8

    def test_the_mirrored_artist_alone_finds_it(self, tmp_path, match):
        # an artist the library can't bridge by spelling: the playlist's own
        # name for them still finds the track
        db = _vandi_db(tmp_path, server='navidrome')
        found, _ = match(db, _itunes_track(artists=['D. Vandi (GR)']))
        assert found is not None and str(found.id) == '25054'

    def test_sakis_rouvas(self, tmp_path, match):
        db = _vandi_db(tmp_path, server='navidrome')
        found, _ = match(db, _itunes_track(
            id='itunes-shake', name='Shake It (Eurovision Version)', artists=['Σάκης Ρουβάς'],
            album='Shake It', source_artists=None, source_track_id=None))
        assert found is not None and str(found.id) == '26054'

    def test_another_song_by_the_artist_is_not_matched(self, tmp_path, match):
        db = _vandi_db(tmp_path, server='navidrome')
        found, _ = match(db, _itunes_track(name='Kapou Allou', source_artists=None))
        assert found is None


@pytest.mark.parametrize('match', MATCHERS)
class TestCacheUnderAnotherProviderId:
    def test_a_deezer_pairing_is_found_by_the_itunes_sync(self, tmp_path, match):
        """the reporter's cache row: deezer id 1626527832 -> plex 25054 at 1.0.
        the library file is tagged 'Track 14' (a hand pick), so only the
        cache can pair it: the name matcher never would."""
        db = _vandi_db(tmp_path, server='navidrome')
        with db._get_connection() as conn:
            conn.execute("UPDATE lib2_tracks SET title = 'Track 14', isrc = NULL "
                         "WHERE title = 'Anaveis Foties'")
            conn.commit()
        from core.sync.match_overrides import record_manual_match
        assert record_manual_match(db, source_track_id='1626527832', server_source='navidrome',
                                   server_track_id='25054', source_title='Anaveis Foties',
                                   source_artist='Despina Vandi')
        found, conf = match(db, _itunes_track(source_track_id=None))
        assert found is not None and str(found.id) == '25054'
        assert conf == 1.0

    def test_an_auto_pairing_under_the_greek_name_is_found_too(self, tmp_path, match):
        db = _vandi_db(tmp_path, server='navidrome')
        with db._get_connection() as conn:
            conn.execute("UPDATE lib2_tracks SET title = 'Anaveis Foties (Live)', isrc = NULL "
                         "WHERE title = 'Anaveis Foties'")
            conn.commit()
        from core.matching_engine import MusicMatchingEngine
        me = MusicMatchingEngine()
        db.save_sync_match_cache('1626527832', me.clean_title('Anaveis Foties'),
                                 me.clean_artist('Despina Vandi'), 'navidrome', '25054',
                                 'Anaveis Foties (Live)', 0.9)
        found, _ = match(db, _itunes_track(source_track_id=None, source_artists=None))
        assert found is not None and str(found.id) == '25054'

    def test_a_cached_pairing_for_another_artists_song_is_not_borrowed(self, tmp_path, match):
        db = _vandi_db(tmp_path, server='navidrome')
        with db._get_connection() as conn:
            conn.execute("UPDATE lib2_tracks SET title = 'Track 14' WHERE title = 'Anaveis Foties'")
            conn.commit()
        from core.sync.match_overrides import record_manual_match
        record_manual_match(db, source_track_id='dz-other', server_source='navidrome',
                            server_track_id='25054', source_title='Anaveis Foties',
                            source_artist='Somebody Else')
        found, _ = match(db, _itunes_track(source_track_id=None, source_artists=None))
        assert found is None


@pytest.mark.parametrize('match', MATCHERS)
class TestIsrc:
    def test_isrc_finds_the_track_when_no_name_bridges(self, tmp_path, match):
        # "Δέσποινα" alone romanizes to no library artist; the isrc is the
        # only thing that ties the itunes track to the library row
        db = _vandi_db(tmp_path, server='navidrome')
        found, conf = match(db, _itunes_track(artists=['Δέσποινα'], source_artists=None,
                                              source_track_id=None, isrc='grhv10100075'))
        assert found is not None and str(found.id) == '25054'
        assert conf == 1.0

    def test_isrc_on_a_different_title_is_not_trusted(self, tmp_path, match):
        db = _vandi_db(tmp_path, server='navidrome')
        found, _ = match(db, _itunes_track(name='Kapou Allou', artists=['Δέσποινα'],
                                           source_artists=None, source_track_id=None,
                                           isrc='GRHV10100075'))
        assert found is None


def test_isrc_lookup_runs_on_the_index(tmp_path):
    db = _vandi_db(tmp_path)
    rows = db.get_tracks_by_isrc('grhv10100075', server_source='plex')
    assert [str(r.id) for r in rows] == ['25054']
    with db._get_connection() as conn:
        plan = " ".join(str(tuple(r)) for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM lib2_tracks t WHERE t.isrc = ? COLLATE NOCASE",
            ('GRHV10100075',)).fetchall())
    assert 'idx_lib2_tracks_isrc' in plan


def test_identity_cache_lookup_runs_on_the_index(tmp_path):
    db = _vandi_db(tmp_path)
    with db._get_connection() as conn:
        plan = " ".join(str(tuple(r)) for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT server_track_id FROM sync_match_cache WHERE server_source = ? "
            "AND normalized_title IN (?, ?) AND normalized_artist IN (?, ?)",
            ('plex', 'a', 'b', 'c', 'd')).fetchall())
    assert 'idx_sync_cache_identity' in plan


# ---------------------------------------------------------------------------
# the duplicate detector pairs the two copies
# ---------------------------------------------------------------------------

def test_duplicate_detector_compares_greek_and_latin_credits():
    from core.library2.duplicate_review import _performer_names, _similar_tags
    greek = _performer_names(['Δέσποινα Βανδή'], None, {})
    latin = _performer_names(['Despina Vandi'], None, {})
    assert 'despina vandi' in greek and latin == ['despina vandi']
    a = {"artist_names": greek, "norm_title": "anaveis foties", "path": "/m/Δέσποινα Βανδή/a/14.mp3"}
    b = {"artist_names": latin, "norm_title": "anaveis foties", "path": "/m/Despina Vandi/a/14.mp3"}
    settings = {"artist_similarity": 0.8, "title_similarity": 0.85}
    assert _similar_tags(a, b, settings)
    b["artist_names"] = ['someone else']
    assert not _similar_tags(a, b, settings)


def test_romanized_forms_are_the_common_latin_spellings():
    from core.text.cross_script import romanized_forms
    assert romanized_forms('Δέσποινα Βανδή')[0] == 'Despina Vandi'
    assert romanized_forms('Σάκης Ρουβάς')[0] == 'Sakis Rouvas'
    assert 'Eleni Foureira' in romanized_forms('Ελένη Φουρέιρα')
    assert romanized_forms('Мумий Тролль')[0] == 'Mumiy Troll'
    assert romanized_forms('Despina Vandi') == []
    assert romanized_forms('宇多田ヒカル') == []


def test_the_automation_sync_carries_the_mirrored_artist_and_isrc():
    """the reporter's path: an automation sync of a mirrored playlist builds
    the track from discovery's itunes match. the mirror's own artist rides
    along, and SpotifyTrack keeps it."""
    from core.discovery.sync import _source_artist_names, _track_isrc
    assert _source_artist_names(['Despina Vandi']) == ['Despina Vandi']
    assert _source_artist_names([{'name': 'Despina Vandi'}, 'Despina Vandi', '']) == ['Despina Vandi']
    assert _source_artist_names(None) is None
    assert _track_isrc({'external_ids': {'isrc': 'grhv10100075'}}) == 'GRHV10100075'
    assert _track_isrc({'isrc': ' GRHV10100075 '}) == 'GRHV10100075'
    assert _track_isrc({}) is None


def test_the_automation_sync_hands_the_mirrored_artist_to_the_sync(tmp_path):
    import threading
    from unittest.mock import MagicMock
    from core.automation.handlers.sync_playlist import auto_sync_playlist

    db = MusicDatabase(database_path=str(tmp_path / 'music.db'))
    pk = db.mirror_playlist(
        source='spotify', source_playlist_id='p2222', name='Greek',
        tracks=[{'track_name': 'Anaveis Foties', 'artist_name': 'Despina Vandi',
                 'source_track_id': 'sp-vandi'}],
        profile_id=1)
    row = db.get_mirrored_playlist_tracks(pk)[0]
    db.update_mirrored_track_extra_data(row['id'], {'discovered': True, 'matched_data': {
        'id': '1606586044', 'name': 'Anaveis Foties', 'source': 'itunes',
        'artists': [{'name': 'Δέσποινα Βανδή'}], 'album': "Gia - Collector's Edition",
        'isrc': 'GRHV10100075'}})
    seen = {}
    started = threading.Event()
    deps = MagicMock()
    deps.get_database.return_value = db
    deps.load_sync_status_file.return_value = {}
    deps.config_manager.get.side_effect = lambda key, default=None: default
    deps.config_manager.get_active_media_server.return_value = 'plex'

    def run_sync_task(*args, **kwargs):
        seen['args'], seen['kwargs'] = args, kwargs
        started.set()
    deps.run_sync_task.side_effect = run_sync_task
    auto_sync_playlist({'playlist_id': str(pk)}, deps)
    assert started.wait(10)
    tracks = next(a for a in [*seen['args'], *seen['kwargs'].values()]
                  if isinstance(a, list) and a and isinstance(a[0], dict))
    assert tracks[0]['artists'] == [{'name': 'Δέσποινα Βανδή'}]
    assert tracks[0]['source_artists'] == ['Despina Vandi']
    assert tracks[0]['isrc'] == 'GRHV10100075'
