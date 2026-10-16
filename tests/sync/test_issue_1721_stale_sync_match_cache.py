"""Tests for SoulSync issue #1721 — the wrong song from #1715 stayed on the
playlist after updating to 3.5.6 and re-syncing.

Report shape: kevin2xk's playlist went from 1 song to 103 after the #1715
fix, but the one wrong song (same title, same album name, completely
different artist) was STILL on it. The gated ``check_track_exists`` no
longer matches it — verified by the #1715 suite — so the pairing had to be
coming from somewhere that never re-asks the matcher.

Root cause: both sync matchers consult ``sync_match_cache`` FIRST and
returned the cached pairing unchecked. The broken pre-fix sync had cached
the wrong pairing at confidence 1.0 (the old fallback's title-only score),
and nothing invalidates cache rows except a manual database-admin clear.
Every re-sync re-materialized the wrong track from the cache, so the
server playlist (rebuilt in replace/reconcile mode from the matched set)
kept it forever.

Contract pinned here:
  * a cached AUTO-match whose title resembles the source title but which
    today's matcher rejects (different artist, no credit) is dropped —
    the row is deleted, the track is reported missing, and the real
    matcher gets its say on the same run
  * the title resemblance is scored the way the writers scored it —
    max(raw, cleaned) — so poison cached from a title carrying a
    feat./(with …) qualifier is dropped too, not mistaken for a user pick
  * a cached auto-match today's matcher still makes is honored as before
  * a compilation-credit match (the album fallback's intended shape) is
    honored, with or without a source album: the credit itself is the
    artist evidence, read off the cached row's track_artist
  * a row with NO artist evidence (Various Artists / placeholder filing,
    no per-track credit) is unverifiable, not wrong — the #1715 gate
    itself still matches that shape on title+album — so it is kept even
    when the source track carries no album to revalidate against
  * a user-confirmed pick is never unpicked by revalidation: a pick whose
    title is nothing like the source ('Track 09', #1289) is not shaped
    like an auto-match and is honored as stored, and a pick that IS
    title-shaped survives through the durable manual match (#787) even
    when its cache row is dropped
  * the database-only matcher (no media server connected) behaves the same
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from database.music_database import MusicDatabase


# ---------------------------------------------------------------------------
# Real-DB seeding (mirrors the #1715 suite's harness)
# ---------------------------------------------------------------------------

def _make_db(tmp_path, artists, albums, tracks):
    db = MusicDatabase(str(tmp_path / "music.db"))
    conn = db._get_connection()
    c = conn.cursor()
    for r in artists:
        c.execute("INSERT INTO artists (id, name, server_source) VALUES (?,?,?)", r)
    for r in albums:
        c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (?,?,?,?)", r)
    for r in tracks:
        c.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, track_artist, server_source)"
            " VALUES (?,?,?,?,?,?)", r)
    conn.commit()
    conn.close()
    db.ensure_norm_backfilled()
    return db


def _wrong_song_db(tmp_path):
    """Metallica owns 'Blue Sky' on 'Greatest Hits'; Taylor Swift (whose
    playlist track is being synced) owns nothing."""
    return _make_db(
        tmp_path,
        artists=[('a-met', 'Metallica', 'navidrome')],
        albums=[('al-gh', 'a-met', 'Greatest Hits', 'navidrome')],
        tracks=[('t-met', 'al-gh', 'a-met', 'Blue Sky', None, 'navidrome')],
    )


def _poison_cache(db):
    """The row the broken pre-fix sync wrote for the wrong pairing."""
    db.save_sync_match_cache('dz-123', 'blue sky', 'taylor swift',
                             'navidrome', 't-met', 'Blue Sky', 1.0)


def _same_db(db):
    """``MusicDatabase()`` hands back this real db. (Subclass trick from the
    #1289 suite: patching the name with a MagicMock breaks the scorer,
    which calls MusicDatabase._clean_track_title_cached by class name.)"""
    class _SameDB(type(db)):
        def __new__(cls, *a, **k):
            return db
    return _SameDB


def _config():
    cm = MagicMock()
    cm.get_active_media_server.return_value = 'navidrome'
    cm.get.side_effect = lambda key, default=None: default
    return cm


def _media_match(db, track):
    from services.sync_service import PlaylistSyncService
    svc = PlaylistSyncService(spotify_client=MagicMock(), download_orchestrator=MagicMock(),
                              media_server_engine=MagicMock())
    client = MagicMock(); client.is_connected.return_value = True
    svc._get_active_media_client = lambda: (client, 'navidrome')
    svc._cancelled = False
    with patch('database.music_database.MusicDatabase', _same_db(db)), \
         patch('core.settings.config_manager', _config()), \
         patch('core.artists.map.get_current_profile_id', return_value=1):
        return asyncio.run(svc._find_track_in_media_server(track, candidate_pool={}))


def _db_only_match(db, track):
    import core.discovery.sync as sync_mod
    with patch('database.music_database.MusicDatabase', _same_db(db)), \
         patch('core.settings.config_manager', _config()), \
         patch('core.artists.map.get_current_profile_id', return_value=1):
        return asyncio.run(sync_mod._database_only_find_track(track, candidate_pool={}))


def _track(**kw):
    from core.spotify_client import Track as SpotifyTrack
    base = dict(id='dz-123', name='Blue Sky', artists=['Taylor Swift'],
                album='Greatest Hits', duration_ms=0, popularity=0,
                source_track_id='sp-123')
    base.update(kw)
    return SpotifyTrack(**base)


# ---------------------------------------------------------------------------
# The reported bug — media-server matcher
# ---------------------------------------------------------------------------

class TestMediaServerMatcher:
    def test_poisoned_cache_no_longer_returns_the_wrong_song(self, tmp_path):
        db = _wrong_song_db(tmp_path)
        _poison_cache(db)
        match, conf = _media_match(db, _track())
        assert match is None
        assert conf == 0.0

    def test_poisoned_cache_row_is_deleted(self, tmp_path):
        db = _wrong_song_db(tmp_path)
        _poison_cache(db)
        _media_match(db, _track())
        assert db.read_sync_match_cache('dz-123', 'navidrome') is None
        # and a second sync does not resurrect it
        match, _ = _media_match(db, _track())
        assert match is None

    def test_legit_auto_cache_is_honored(self, tmp_path):
        db = _make_db(
            tmp_path,
            artists=[('a-ts', 'Taylor Swift', 'navidrome')],
            albums=[('al-gh', 'a-ts', 'Greatest Hits', 'navidrome')],
            tracks=[('t-ts', 'al-gh', 'a-ts', 'Blue Sky', None, 'navidrome')],
        )
        db.save_sync_match_cache('dz-123', 'blue sky', 'taylor swift',
                                 'navidrome', 't-ts', 'Blue Sky', 0.98)
        match, conf = _media_match(db, _track())
        assert match is not None and str(match.id) == 't-ts'
        assert conf == 0.98
        assert db.read_sync_match_cache('dz-123', 'navidrome') is not None

    def test_compilation_credit_cache_is_honored(self, tmp_path):
        """The album fallback's intended shape (#825): the cached track sits
        on a Various Artists album, credited to the searched artist. The
        credit on the row (track_artist, plumbed through get_track_by_id)
        is agreeing evidence, so the row validates — the album on the
        source lets the fallback confirm it too."""
        db = _make_db(
            tmp_path,
            artists=[('a-va', 'Various Artists', 'navidrome')],
            albums=[('al-mix', 'a-va', 'Party Mix', 'navidrome')],
            tracks=[('t-va', 'al-mix', 'a-va', 'Blue Sky', 'Taylor Swift', 'navidrome')],
        )
        db.save_sync_match_cache('dz-123', 'blue sky', 'taylor swift',
                                 'navidrome', 't-va', 'Blue Sky', 0.95)
        match, conf = _media_match(db, _track(album='Party Mix'))
        assert match is not None and str(match.id) == 't-va'
        assert conf == 0.95

    def test_divergent_title_user_pick_is_honored(self, tmp_path):
        """#1289's pinned shape: Find & Add pointed the source at a file
        tagged 'Track 09' — nothing like the source title. That row is not
        shaped like an auto-match, so revalidation must not touch it.
        The library filing is under a DIFFERENT artist than the source on
        purpose (round-2 mutation check): with the filing under the source
        artist, gate 2's agreement would save the row even if the title
        gate were deleted — this way only the title gate can save it."""
        db = _make_db(
            tmp_path,
            artists=[('a-ps', 'The Postal Service', 'navidrome')],
            albums=[('al-gu', 'a-ps', 'Give Up (Deluxe)', 'navidrome')],
            tracks=[('lib-9', 'al-gu', 'a-ps', 'Track 09', None, 'navidrome')],
        )
        from core.sync.match_overrides import record_manual_match
        assert record_manual_match(db, source_track_id='sp-123', server_source='navidrome',
                                   server_track_id='lib-9')
        track = _track(id='wing_it_deadbeef', name='Such Great Heights',
                       artists=['Iron & Wine'], album='')
        match, conf = _media_match(db, track)
        assert match is not None and str(match.id) == 'lib-9'
        assert conf == 1.0
        assert db.read_sync_match_cache('sp-123', 'navidrome') is not None

    def test_title_shaped_user_pick_survives_via_durable_match(self, tmp_path):
        """A user deliberately picked the same-titled, different-artist file
        (mis-tagged library). Its cache row IS auto-shaped, so revalidation
        drops the row — but Find & Add also wrote a durable manual match
        (#787), which the same sync honors at 1.0. The pick survives.
        The cache-row deletion assertion is what separates the rescue path
        from the row simply being honored unchecked."""
        db = _wrong_song_db(tmp_path)
        _poison_cache(db)
        from core.library import manual_library_match as mlm
        assert mlm.save_match(db, 1, 'spotify', 'sp-123', 't-met',
                              source_title='Blue Sky', source_artist='Taylor Swift',
                              server_source='navidrome', library_file_path='/m/blue.flac')
        match, conf = _media_match(db, _track())
        assert match is not None and str(match.id) == 't-met'
        assert conf == 1.0
        # the poison-shaped cache row WAS dropped (not silently honored);
        # the match above came from the durable row under 'sp-123'
        assert db.read_sync_match_cache('dz-123', 'navidrome') is None

    def test_cleaned_title_poison_is_dropped(self, tmp_path):
        """Round-1 review: the pre-fix fallback cached on max(raw, cleaned)
        title similarity. A source title carrying a (with …) qualifier has
        LOW raw similarity to the cached bare title, so a raw-only gate
        mistakes the poison for a user pick and keeps it forever."""
        db = _wrong_song_db(tmp_path)
        _poison_cache(db)
        track = _track(name='Blue Sky (with Justin Bieber)')
        match, conf = _media_match(db, track)
        assert match is None
        assert conf == 0.0
        assert db.read_sync_match_cache('dz-123', 'navidrome') is None

    def test_poison_under_the_second_lookup_id_is_dropped(self, tmp_path):
        """Cache rows are consulted under the discovered id AND the
        playlist's own source_track_id (#1289); poison under the second
        id must be dropped just the same."""
        db = _wrong_song_db(tmp_path)
        db.save_sync_match_cache('sp-123', 'blue sky', 'taylor swift',
                                 'navidrome', 't-met', 'Blue Sky', 1.0)
        match, conf = _media_match(db, _track())
        assert match is None
        assert conf == 0.0
        assert db.read_sync_match_cache('sp-123', 'navidrome') is None

    def test_va_no_credit_cache_is_kept_without_a_source_album(self, tmp_path):
        """Round-1 review: a Various Artists track with NO per-track credit
        carries no artist evidence — the #1715 gate itself matches that
        shape on title+album. With no album on the source track there is
        nothing to revalidate against; unverifiable is not wrong, and
        dropping the row would lose a legitimate match (the fresh matcher
        cannot re-make it either) and re-download a duplicate."""
        db = _make_db(
            tmp_path,
            artists=[('a-va', 'Various Artists', 'navidrome')],
            albums=[('al-mix', 'a-va', 'Party Mix', 'navidrome')],
            tracks=[('t-va', 'al-mix', 'a-va', 'Believe', None, 'navidrome')],
        )
        db.save_sync_match_cache('dz-123', 'believe', 'cher',
                                 'navidrome', 't-va', 'Believe', 0.99)
        track = _track(name='Believe', artists=['Cher'], album='')
        match, conf = _media_match(db, track)
        assert match is not None and str(match.id) == 't-va'
        assert conf == 0.99
        assert db.read_sync_match_cache('dz-123', 'navidrome') is not None

    @pytest.mark.parametrize('va_name', ['[Various Artists]', 'Various Artists.', 'Various-Artists'])
    def test_punctuated_va_spellings_are_kept(self, tmp_path, va_name):
        """Round-2 review: punctuated/bracketed VA spellings are not in the
        compilation helper's exact VA set, so they were treated as a
        specific conflicting artist and the no-credit row was dropped —
        the same harm as the canonical spelling. VA is VA, punctuated
        or not: no evidence, row kept."""
        db = _make_db(
            tmp_path,
            artists=[('a-va', va_name, 'navidrome')],
            albums=[('al-mix', 'a-va', 'Party Mix', 'navidrome')],
            tracks=[('t-va', 'al-mix', 'a-va', 'Believe', None, 'navidrome')],
        )
        db.save_sync_match_cache('dz-123', 'believe', 'cher',
                                 'navidrome', 't-va', 'Believe', 0.99)
        track = _track(name='Believe', artists=['Cher'], album='')
        match, conf = _media_match(db, track)
        assert match is not None and str(match.id) == 't-va'
        assert conf == 0.99
        assert db.read_sync_match_cache('dz-123', 'navidrome') is not None

    def test_placeholder_filing_cache_is_kept(self, tmp_path):
        """Same no-evidence shape via a placeholder album artist."""
        db = _make_db(
            tmp_path,
            artists=[('a-unk', '[Unknown]', 'navidrome')],
            albums=[('al-x', 'a-unk', 'Rips', 'navidrome')],
            tracks=[('t-unk', 'al-x', 'a-unk', 'Believe', None, 'navidrome')],
        )
        db.save_sync_match_cache('dz-123', 'believe', 'cher',
                                 'navidrome', 't-unk', 'Believe', 0.99)
        track = _track(name='Believe', artists=['Cher'], album='')
        match, conf = _media_match(db, track)
        assert match is not None and str(match.id) == 't-unk'
        assert conf == 0.99

    def test_feat_split_credit_cache_is_honored_without_album(self, tmp_path):
        """The credit names the searched artist as a featured partner; the
        evidence is on the row's track_artist, not the album artist."""
        db = _make_db(
            tmp_path,
            artists=[('a-va', 'Various Artists', 'navidrome')],
            albums=[('al-mix', 'a-va', 'Party Mix', 'navidrome')],
            tracks=[('t-va', 'al-mix', 'a-va', 'Blue Sky', 'Someone feat. Taylor Swift', 'navidrome')],
        )
        db.save_sync_match_cache('dz-123', 'blue sky', 'taylor swift',
                                 'navidrome', 't-va', 'Blue Sky', 0.95)
        match, conf = _media_match(db, _track(album=''))
        assert match is not None and str(match.id) == 't-va'
        assert conf == 0.95

    def test_soundtrack_credit_cache_is_honored_without_album(self, tmp_path):
        """Filed under the composer, credited to the performer being
        searched — the soundtrack shape the artist scorer handles via
        track_artist. The validation candidate must carry the credit."""
        db = _make_db(
            tmp_path,
            artists=[('a-lmm', 'Lin-Manuel Miranda', 'navidrome')],
            albums=[('al-va', 'a-lmm', 'Vaiana (OST)', 'navidrome')],
            tracks=[('t-va', 'al-va', 'a-lmm', 'How Far I\'ll Go', 'Alessia Cara', 'navidrome')],
        )
        db.save_sync_match_cache('dz-123', 'how far i\'ll go', 'alessia cara',
                                 'navidrome', 't-va', 'How Far I\'ll Go', 0.95)
        track = _track(name='How Far I\'ll Go', artists=['Alessia Cara'], album='')
        match, conf = _media_match(db, track)
        assert match is not None and str(match.id) == 't-va'
        assert conf == 0.95

    def test_joint_album_artist_evidences_each_partner(self, tmp_path):
        """'Simon & Garfunkel' filing, searched as 'Garfunkel' — the joint
        name evidences each partner (#1715), so the cache row is kept."""
        db = _make_db(
            tmp_path,
            artists=[('a-sg', 'Simon & Garfunkel', 'navidrome')],
            albums=[('al-botw', 'a-sg', 'Bridge over Troubled Water', 'navidrome')],
            tracks=[('t-sg', 'al-botw', 'a-sg', 'The Boxer', None, 'navidrome')],
        )
        db.save_sync_match_cache('dz-123', 'the boxer', 'garfunkel',
                                 'navidrome', 't-sg', 'The Boxer', 0.97)
        track = _track(name='The Boxer', artists=['Garfunkel'],
                       album='Bridge over Troubled Water')
        match, conf = _media_match(db, track)
        assert match is not None and str(match.id) == 't-sg'
        assert conf == 0.97


# ---------------------------------------------------------------------------
# The database-only matcher (no media server connected)
# ---------------------------------------------------------------------------

class TestDatabaseOnlyMatcher:
    def test_poisoned_cache_no_longer_returns_the_wrong_song(self, tmp_path):
        db = _wrong_song_db(tmp_path)
        _poison_cache(db)
        match, conf = _db_only_match(db, _track())
        assert match is None
        assert conf == 0.0
        assert db.read_sync_match_cache('dz-123', 'navidrome') is None

    def test_legit_auto_cache_is_honored(self, tmp_path):
        db = _make_db(
            tmp_path,
            artists=[('a-ts', 'Taylor Swift', 'navidrome')],
            albums=[('al-gh', 'a-ts', 'Greatest Hits', 'navidrome')],
            tracks=[('t-ts', 'al-gh', 'a-ts', 'Blue Sky', None, 'navidrome')],
        )
        db.save_sync_match_cache('dz-123', 'blue sky', 'taylor swift',
                                 'navidrome', 't-ts', 'Blue Sky', 0.98)
        match, conf = _db_only_match(db, _track())
        assert match is not None and str(match.id) == 't-ts'
        assert conf == 0.98

    def test_cleaned_title_poison_is_dropped(self, tmp_path):
        db = _wrong_song_db(tmp_path)
        _poison_cache(db)
        match, conf = _db_only_match(db, _track(name='Blue Sky (with Justin Bieber)'))
        assert match is None
        assert conf == 0.0
        assert db.read_sync_match_cache('dz-123', 'navidrome') is None

    def test_va_no_credit_cache_is_kept_without_a_source_album(self, tmp_path):
        db = _make_db(
            tmp_path,
            artists=[('a-va', 'Various Artists', 'navidrome')],
            albums=[('al-mix', 'a-va', 'Party Mix', 'navidrome')],
            tracks=[('t-va', 'al-mix', 'a-va', 'Believe', None, 'navidrome')],
        )
        db.save_sync_match_cache('dz-123', 'believe', 'cher',
                                 'navidrome', 't-va', 'Believe', 0.99)
        match, conf = _db_only_match(db, _track(name='Believe', artists=['Cher'], album=''))
        assert match is not None and str(match.id) == 't-va'
        assert conf == 0.99


# ---------------------------------------------------------------------------
# Plex — validation judges the DB row, the returned object is the live item
# ---------------------------------------------------------------------------

def _plex_match(db, track):
    from services.sync_service import PlaylistSyncService
    svc = PlaylistSyncService(spotify_client=MagicMock(), download_orchestrator=MagicMock(),
                              media_server_engine=MagicMock())
    client = MagicMock(); client.is_connected.return_value = True
    titles = {'42': 'Blue Sky', '43': 'Blue Sky'}
    client.server.fetchItem.side_effect = \
        lambda i: SimpleNamespace(ratingKey=i, title=titles[str(i)]) if str(i) in titles else None
    svc._get_active_media_client = lambda: (client, 'plex')
    svc._cancelled = False
    cm = MagicMock(); cm.get_active_media_server.return_value = 'plex'
    cm.get.side_effect = lambda key, default=None: default
    with patch('database.music_database.MusicDatabase', _same_db(db)), \
         patch('core.settings.config_manager', cm), \
         patch('core.artists.map.get_current_profile_id', return_value=1):
        return asyncio.run(svc._find_track_in_media_server(track, candidate_pool={}))


def _plex_db(tmp_path):
    return _make_db(
        tmp_path,
        artists=[('a-met', 'Metallica', 'plex'), ('a-ts', 'Taylor Swift', 'plex')],
        albums=[('al-met', 'a-met', 'Greatest Hits', 'plex'),
                ('al-ts', 'a-ts', 'Greatest Hits', 'plex')],
        tracks=[('42', 'al-met', 'a-met', 'Blue Sky', None, 'plex'),
                ('43', 'al-ts', 'a-ts', 'Blue Sky', None, 'plex')],
    )


class TestPlexMatcher:
    def test_poisoned_cache_is_dropped(self, tmp_path):
        """The fixture also seeds Taylor Swift's real track ('43'): after
        the poisoned row ('42', Metallica's) is dropped, the fresh matcher
        finds the real one in the same run and the cache is re-saved with
        the CORRECT pairing — the wrong song is gone from the playlist,
        not just uncached."""
        db = _plex_db(tmp_path)
        db.save_sync_match_cache('dz-123', 'blue sky', 'taylor swift',
                                 'plex', '42', 'Blue Sky', 1.0)
        match, conf = _plex_match(db, _track())
        assert match is not None and str(match.ratingKey) == '43'
        row = db.read_sync_match_cache('dz-123', 'plex')
        assert row is not None and str(row['server_track_id']) == '43'

    def test_legit_cache_returns_the_live_plex_item(self, tmp_path):
        db = _plex_db(tmp_path)
        db.save_sync_match_cache('dz-123', 'blue sky', 'taylor swift',
                                 'plex', '43', 'Blue Sky', 0.97)
        match, conf = _plex_match(db, _track())
        assert match is not None and str(match.ratingKey) == '43'
        assert conf == 0.97


# ---------------------------------------------------------------------------
# Call-site thresholds — validation must use each site's own fresh threshold
# ---------------------------------------------------------------------------

class TestValidationThresholds:
    def _spy(self, db):
        calls = []
        orig = db.check_track_exists
        def wrapper(*a, **k):
            calls.append(k.get('confidence_threshold'))
            return orig(*a, **k)
        db.check_track_exists = wrapper
        return calls

    def test_media_site_validates_at_0_7(self, tmp_path):
        db = _wrong_song_db(tmp_path)
        _poison_cache(db)
        calls = self._spy(db)
        _media_match(db, _track())
        assert calls and calls[0] == 0.7

    def test_db_only_site_validates_at_0_8(self, tmp_path):
        db = _wrong_song_db(tmp_path)
        _poison_cache(db)
        calls = self._spy(db)
        _db_only_match(db, _track())
        assert calls and calls[0] == 0.80


# ---------------------------------------------------------------------------
# The revalidation helper — conclusive evidence only
# ---------------------------------------------------------------------------

class TestCachedMatchIsStale:
    def _db(self, check_result):
        import difflib
        db = SimpleNamespace()
        db._normalize_for_comparison = lambda s: (s or '').lower().strip()
        db._string_similarity = lambda a, b: difflib.SequenceMatcher(None, a, b).ratio()
        db.check_track_exists = MagicMock(return_value=check_result)
        return db

    def _row(self, title='Blue Sky', tid='t-1', artist_name='Metallica', track_artist=None):
        return SimpleNamespace(id=tid, title=title, artist_name=artist_name,
                               track_artist=track_artist)

    def _call(self, db, **kw):
        from core.sync.match_overrides import cached_match_is_stale
        base = dict(title='Blue Sky', artists=['Taylor Swift'], album='Greatest Hits',
                    server_source='navidrome', cached_track_id='t-1',
                    cached_track_row=self._row(), threshold=0.7)
        base.update(kw)
        return cached_match_is_stale(db, **base)

    def test_matcher_still_picks_the_cached_track(self):
        db = self._db((self._row(), 0.99))
        assert self._call(db) is False

    def test_matcher_rejects_the_cached_track(self):
        db = self._db((None, 0.15))
        assert self._call(db) is True

    def test_matcher_picks_a_different_track(self):
        db = self._db((self._row(tid='t-other'), 0.99))
        assert self._call(db) is True

    def test_second_artist_can_still_validate(self):
        import difflib
        db = SimpleNamespace()
        db._normalize_for_comparison = lambda s: (s or '').lower().strip()
        db._string_similarity = lambda a, b: difflib.SequenceMatcher(None, a, b).ratio()
        row = self._row()
        db.check_track_exists = MagicMock(side_effect=[(None, 0.1), (row, 0.9)])
        assert self._call(db, artists=['Wrong Name', 'Taylor Swift']) is False

    def test_dissimilar_title_is_a_user_pick_shape(self):
        db = self._db((None, 0.0))
        assert self._call(db, cached_track_row=self._row(title='Track 09')) is False
        db.check_track_exists.assert_not_called()

    def test_no_artist_evidence_is_unverifiable_not_stale(self):
        """VA filing with no credit: nothing on the row disagrees with the
        source artist, so the matcher is never even consulted."""
        db = self._db((None, 0.0))
        row = self._row(artist_name='Various Artists', track_artist=None)
        assert self._call(db, cached_track_row=row) is False
        db.check_track_exists.assert_not_called()

    def test_agreeing_credit_is_not_stale(self):
        db = self._db((None, 0.0))
        row = self._row(artist_name='Various Artists', track_artist='Taylor Swift')
        assert self._call(db, cached_track_row=row) is False
        db.check_track_exists.assert_not_called()

    def test_namesake_evidence_conflicts_on_exact_keys(self):
        """'John William' vs 'John Williams': fuzzy similarity is high but
        the #1715 evidence semantics are exact-key — a namesake is a
        different artist, so with the matcher also rejecting, it's stale.
        Helper-level pin (round-2 note): with the REAL matcher the direct
        score picks a namesake at ~0.98, so gate 3 keeps such a row —
        consistent with what a fresh sync would do. This test pins the
        helper's verdict when the matcher side rejects."""
        db = self._db((None, 0.2))
        row = self._row(artist_name='John William')
        assert self._call(db, artists=['John Williams'], cached_track_row=row) is True

    def test_garbage_artist_name_is_no_evidence(self):
        """Round-2 review: an artist name that keys to nothing ('!!!')
        produced an empty-string evidence key, which then 'conflicted'
        with everything. No key, no evidence, no conviction."""
        db = self._db((None, 0.0))
        row = self._row(artist_name='!!!')
        assert self._call(db, cached_track_row=row) is False
        db.check_track_exists.assert_not_called()

    def test_non_string_artist_name_does_not_raise(self):
        """Round-2 review: the helper's contract is 'inconclusive returns
        False' — a malformed (non-string) artist field must not raise."""
        db = self._db((None, 0.0))
        row = self._row(artist_name=123)
        assert self._call(db, cached_track_row=row) is False

    def test_cleaned_title_shape_counts_as_auto_match(self):
        """The gate scores titles like the writers did: max(raw, cleaned).
        A (with …) qualifier crushes raw similarity but cleans to equal."""
        db = self._db((None, 0.15))
        db._clean_track_title_for_comparison = \
            lambda s: (s or '').lower().split(' (with ')[0].strip()
        row = self._row(title='Blue Sky')
        assert self._call(db, title='Blue Sky (with Justin Bieber)',
                          cached_track_row=row) is True

    def test_missing_row_is_not_proven_stale(self):
        db = self._db((None, 0.0))
        assert self._call(db, cached_track_row=None) is False

    def test_scoring_error_is_not_proven_stale(self):
        db = self._db((None, 0.0))
        db.check_track_exists = MagicMock(side_effect=RuntimeError('boom'))
        assert self._call(db) is False

    def test_stub_track_result_is_inconclusive(self):
        db = self._db((SimpleNamespace(id=MagicMock()), 0.5))
        assert self._call(db) is False

    def test_db_without_helpers_is_inconclusive(self):
        assert self._call(SimpleNamespace()) is False


# ---------------------------------------------------------------------------
# Targeted cache deletion
# ---------------------------------------------------------------------------

class TestDeleteSyncMatchCache:
    def test_deletes_only_the_targeted_row(self, tmp_path):
        db = _wrong_song_db(tmp_path)
        db.save_sync_match_cache('dz-123', 'blue sky', 'taylor swift',
                                 'navidrome', 't-met', 'Blue Sky', 1.0)
        db.save_sync_match_cache('dz-123', 'blue sky', 'taylor swift',
                                 'jellyfin', 't-met', 'Blue Sky', 1.0)
        db.save_sync_match_cache('dz-999', 'x', 'y', 'navidrome', 't-met', 'Blue Sky', 1.0)
        assert db.delete_sync_match_cache('dz-123', 'navidrome') == 1
        assert db.read_sync_match_cache('dz-123', 'navidrome') is None
        assert db.read_sync_match_cache('dz-123', 'jellyfin') is not None
        assert db.read_sync_match_cache('dz-999', 'navidrome') is not None
        assert db.delete_sync_match_cache('dz-123', 'navidrome') == 0
