"""Tests for SoulSync issue #1621 — Spotify playlist sync always matches 0.

A 3,228-track Spotify "Liked Songs" sync matched 0 library tracks. Root
cause: the candidate-pool empty-list trap. `_get_or_fetch_artist_candidates`
returns [] (not None) when an artist has no pool rows, and
`check_track_exists(candidate_tracks=[])` took the batched path — scoring
zero candidates is a guaranteed miss — while the legacy per-variation SQL
loop (the pre-#1289 behavior) was skipped. Empty list meant "no
information" but was treated as "nothing matches".

Contract pinned here:
  * [] (no flag)            -> legacy variation loop ("no information")
  * [] + exhaustive=True    -> fast miss, no SQL ("proven empty" — the
                               discography pre-fetch's owns-nothing case)
  * non-empty pool          -> batched path; a pool that scores zero stays
                               a miss (no behavior change)
  * get_artist_tracks_indexed widens to exact tracks.track_artist_norm
    credits (compilation appearances) — indexed, bounded, no false
    positives; punctuation/diacritic variants still resolve via the
    legacy path (the safety net)
  * _wishlist_unmatched logs WARNING on 0-of-N adds
  * _drop_skipped_unmatched_wishlist surfaces the organize-by-playlist
    skip in the automation progress output
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from database.music_database import MusicDatabase
from services.sync_service import PlaylistSyncService


# ---------------------------------------------------------------------------
# Library fixture — realistic shapes
# ---------------------------------------------------------------------------

def _seed(db: MusicDatabase):
    """AC/DC own an album; a Various Artists compilation carries per-track
    credits (Nirvana has NO artist row of its own; AC/DC guest-appears);
    Drake owns one track."""
    conn = db._get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO artists (id, name, server_source) VALUES ('a-acdc','AC/DC','plex')")
    c.execute("INSERT INTO artists (id, name, server_source) VALUES ('a-va','Various Artists','plex')")
    c.execute("INSERT INTO artists (id, name, server_source) VALUES ('a-drake','Drake','plex')")
    c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES ('al-bib','a-acdc','Back in Black','plex')")
    c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES ('al-party','a-va','VA Party Mix','plex')")
    c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES ('al-fatd','a-drake','For All The Dogs','plex')")
    rows = [
        ('t1', 'al-bib', 'a-acdc', 'Hells Bells', None),
        ('t2', 'al-bib', 'a-acdc', 'Back in Black', None),
        ('t3', 'al-bib', 'a-acdc', 'You Shook Me All Night Long', None),
        ('t4', 'al-party', 'a-va', 'Smells Like Teen Spirit', 'Nirvana'),
        ('t5', 'al-party', 'a-va', 'Come As You Are', 'Nirvana'),
        ('t6', 'al-party', 'a-va', 'Thunderstruck', 'AC/DC'),
        ('t7', 'al-fatd', 'a-drake', 'First Person Shooter', None),
    ]
    for tid, alid, aid, title, tartist in rows:
        c.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, track_artist, server_source)"
            " VALUES (?,?,?,?,?,?)",
            (tid, alid, aid, title, tartist, 'plex'),
        )
    conn.commit()
    conn.close()
    db.ensure_norm_backfilled()


@pytest.fixture()
def db(tmp_path):
    database = MusicDatabase(str(tmp_path / "music.db"))
    _seed(database)
    return database


def _check(db, title, artist, **kwargs):
    kwargs.setdefault('confidence_threshold', 0.7)
    kwargs.setdefault('server_source', 'plex')
    return db.check_track_exists(title, artist, **kwargs)


# ---------------------------------------------------------------------------
# (a) empty pool -> legacy path still matches
# ---------------------------------------------------------------------------

class TestEmptyPoolFallsThroughToLegacy:
    def test_empty_list_matches_compilation_credit_via_legacy(self, db):
        """The #1621 shape: Nirvana has no artist row, so the pool is [];
        the track lives on a VA compilation via per-track credit. Pre-fix
        this returned (None, 0.0) — the batched path scored zero candidates
        and the legacy loop never ran."""
        track, conf = _check(db, 'Smells Like Teen Spirit', 'Nirvana', candidate_tracks=[])
        assert track is not None, "empty pool must fall through to the legacy path"
        assert track.title == 'Smells Like Teen Spirit'
        assert conf >= 0.7

    def test_empty_list_matches_own_row_via_legacy(self, db):
        track, conf = _check(db, 'Hells Bells', 'AC/DC', candidate_tracks=[])
        assert track is not None
        assert track.title == 'Hells Bells'
        assert conf >= 0.7

    def test_none_still_takes_legacy_path(self, db):
        """None keeps its original meaning — unchanged by this fix."""
        track, conf = _check(db, 'Smells Like Teen Spirit', 'Nirvana', candidate_tracks=None)
        assert track is not None
        assert conf >= 0.7

    def test_empty_list_genuine_miss_stays_miss(self, db):
        """Legacy path finding nothing is still a miss — the fix adds recall,
        it doesn't invent matches."""
        track, conf = _check(db, 'Song That Does Not Exist', 'Nirvana', candidate_tracks=[])
        assert track is None
        assert conf < 0.7


# ---------------------------------------------------------------------------
# (b) pool narrower-than-legacy divergences
# ---------------------------------------------------------------------------

class TestPoolWidening:
    def test_indexed_pool_covers_per_track_artist_credits(self, db):
        """Nirvana has no artist row — only compilation credits. The widened
        indexed lookup finds both without the slow LIKE fallback."""
        pool = db.get_artist_tracks_indexed('Nirvana', server_source='plex')
        assert sorted(t.title for t in pool) == ['Come As You Are', 'Smells Like Teen Spirit']

    def test_indexed_pool_merges_own_rows_and_guest_appearances(self, db):
        pool = db.get_artist_tracks_indexed('AC/DC', server_source='plex')
        assert sorted(t.title for t in pool) == [
            'Back in Black', 'Hells Bells', 'Thunderstruck', 'You Shook Me All Night Long',
        ]

    def test_widened_pool_matches_at_batched_path(self, db):
        """The compilation guest appearance now matches WITHOUT the legacy
        loop — the widened pool carries it into the batched scorer."""
        pool = db.get_artist_tracks_indexed('AC/DC', server_source='plex')
        assert pool, "pool must be non-empty for the batched path"
        track, conf = _check(db, 'Thunderstruck', 'AC/DC', candidate_tracks=pool)
        assert track is not None
        assert track.title == 'Thunderstruck'
        assert conf >= 0.7

    def test_widened_pool_matches_identically_to_legacy(self, db):
        """No false positives from the wider pool: batched outcomes equal the
        legacy path's on the compilation shapes."""
        for title, artist in [('Smells Like Teen Spirit', 'Nirvana'),
                              ('Thunderstruck', 'AC/DC'),
                              ('Hells Bells', 'AC/DC')]:
            pool = db.get_artist_tracks_indexed(artist, server_source='plex')
            batched, batched_conf = _check(db, title, artist, candidate_tracks=pool)
            legacy, legacy_conf = _check(db, title, artist, candidate_tracks=None)
            assert (batched is None) == (legacy is None)
            if batched and legacy:
                assert batched.title == legacy.title

    def test_punctuation_variant_pool_stays_empty_but_legacy_matches(self, db):
        """ACDC (no slash) isn't folded by the indexed pool — that recall lives
        in the legacy variation loop (name_key). The empty-fallback is the
        safety net for exactly these shapes."""
        assert db.get_artist_tracks_indexed('ACDC', server_source='plex') == []
        track, conf = _check(db, 'Hells Bells', 'ACDC', candidate_tracks=[])
        assert track is not None, "legacy path must resolve the punctuation variant"
        assert track.title == 'Hells Bells'
        assert conf >= 0.7

    def test_absent_artist_pool_stays_empty(self, db):
        assert db.get_artist_tracks_indexed('Nonexistent Artist', server_source='plex') == []


# ---------------------------------------------------------------------------
# (c) non-empty pool scoring zero stays a miss + exhaustive flag contract
# ---------------------------------------------------------------------------

class TestBatchedPathUnchanged:
    def test_nonempty_pool_scoring_zero_stays_miss(self, db):
        """A non-empty pool that genuinely scores zero is still a miss — the
        fix must not turn the #1289 optimization into a recall regression."""
        pool = db.get_artist_tracks_indexed('Drake', server_source='plex')
        assert len(pool) == 1
        track, conf = _check(db, 'Totally Made Up Song', 'Drake', candidate_tracks=pool)
        assert track is None
        assert conf < 0.7

    def test_exhaustive_empty_pool_is_fast_miss(self, db):
        """The discography pre-fetch's owns-nothing case: [] + exhaustive=True
        skips the legacy SQL loop and reports (None, 0.0) immediately."""
        track, conf = _check(
            db, 'Smells Like Teen Spirit', 'Nirvana',
            candidate_tracks=[], candidate_tracks_exhaustive=True,
        )
        assert track is None
        assert conf == 0.0

    def test_exhaustive_empty_pool_album_fallback_still_runs(self, db):
        """The exhaustive fast-miss only skips the variation loop — the
        album-aware fallback below it still runs (reviewer 1, MINOR 4)."""
        track, conf = _check(
            db, 'Smells Like Teen Spirit', 'Nirvana', album='VA Party Mix',
            candidate_tracks=[], candidate_tracks_exhaustive=True,
        )
        assert track is not None, "album-aware fallback must run on the exhaustive branch"
        assert track.title == 'Smells Like Teen Spirit'
        assert conf >= 0.7

    def test_exhaustive_empty_pool_runs_no_search_queries(self, db, monkeypatch):
        """The 'fast' in fast-miss: no track/album search queries run on the
        exhaustive branch (reviewer 1, MINOR 4)."""
        calls = []
        monkeypatch.setattr(db, 'search_tracks',
                            lambda *a, **k: calls.append('tracks') or [])
        monkeypatch.setattr(db, 'search_albums',
                            lambda *a, **k: calls.append('albums') or [])
        track, conf = _check(
            db, 'Smells Like Teen Spirit', 'Nirvana',
            candidate_tracks=[], candidate_tracks_exhaustive=True,
        )
        assert track is None
        assert conf == 0.0
        assert calls == [], f"exhaustive branch ran searches: {calls}"

    def test_exhaustive_flag_inert_on_nonempty_pool(self, db):
        """The flag only matters for empty lists — a populated pool still
        takes the batched path."""
        pool = db.get_artist_tracks_indexed('Drake', server_source='plex')
        track, conf = _check(
            db, 'First Person Shooter', 'Drake',
            candidate_tracks=pool, candidate_tracks_exhaustive=True,
        )
        assert track is not None
        assert conf >= 0.7


# ---------------------------------------------------------------------------
# Service-level: PlaylistSyncService
# ---------------------------------------------------------------------------

def _make_service() -> PlaylistSyncService:
    return PlaylistSyncService(
        spotify_client=MagicMock(),
        download_orchestrator=MagicMock(),
        media_server_engine=MagicMock(),
    )


def _unmatched(n):
    out = []
    for i in range(n):
        track = SimpleNamespace(
            id=f'sp-track-{i}', name=f'Missing Song {i}',
            artists=['Missing Artist'], album='Missing Album',
            duration_ms=180000, popularity=50,
            preview_url=None, external_urls={},
        )
        out.append(SimpleNamespace(spotify_track=track))
    return out


class TestWishlistUnmatchedObservability:
    def test_zero_of_n_adds_logs_warning(self, monkeypatch, caplog):
        """#1621's "doesn't seem to try" — every miss dropped silently. A
        0-of-N wishlist add must be WARNING-loud with N + playlist identity."""
        svc = _make_service()
        wishlist = MagicMock()
        wishlist.add_spotify_track_to_wishlist.return_value = False  # swallowed
        monkeypatch.setattr('core.wishlist_service.get_wishlist_service', lambda: wishlist)

        playlist = SimpleNamespace(name='Liked Songs', id='pl-123')
        with caplog.at_level(logging.WARNING, logger='services.sync_service'):
            added = svc._wishlist_unmatched(playlist, _unmatched(2))

        assert added == 0
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "expected a WARNING for 0-of-N wishlist adds"
        text = warnings[0].getMessage()
        assert '0 of 2' in text
        assert 'Liked Songs' in text
        assert 'pl-123' in text

    def test_partial_add_stays_info_only(self, monkeypatch, caplog):
        """The warning fires only on a total 0-of-N shutout, not partial adds."""
        svc = _make_service()
        wishlist = MagicMock()
        wishlist.add_spotify_track_to_wishlist.side_effect = [True, False]
        monkeypatch.setattr('core.wishlist_service.get_wishlist_service', lambda: wishlist)

        playlist = SimpleNamespace(name='Liked Songs', id='pl-123')
        with caplog.at_level(logging.WARNING, logger='services.sync_service'):
            added = svc._wishlist_unmatched(playlist, _unmatched(2))

        assert added == 1
        assert not [r for r in caplog.records
                    if r.levelno == logging.WARNING and '0 of 2' in r.getMessage()]

    def test_already_queued_duplicates_do_not_warn(self, monkeypatch, caplog):
        """Re-running a sync whose misses were wishlisted last week: the
        service reports them as already-queued duplicates (benign), not
        drops — no WARNING (reviewer 1, MINOR 2)."""
        svc = _make_service()
        wishlist = MagicMock()
        wishlist.add_spotify_track_to_wishlist.return_value = {
            "status": "skipped", "reason": "duplicate",
            "created": False, "applied": False, "track_id": "sp-track-0",
        }
        monkeypatch.setattr('core.wishlist_service.get_wishlist_service', lambda: wishlist)

        playlist = SimpleNamespace(name='Liked Songs', id='pl-123')
        with caplog.at_level(logging.WARNING, logger='services.sync_service'):
            added = svc._wishlist_unmatched(playlist, _unmatched(2))

        assert added == 0
        assert not [r for r in caplog.records
                    if r.levelno == logging.WARNING and '0 of 2' in r.getMessage()]

    def test_real_drops_warn_with_reasons(self, monkeypatch, caplog):
        """Genuine drops (ignore-list) still warn, and the message names the
        drop reasons."""
        svc = _make_service()
        wishlist = MagicMock()
        wishlist.add_spotify_track_to_wishlist.return_value = {
            "status": "skipped", "reason": "ignore-list",
            "created": False, "applied": False, "track_id": "sp-track-0",
        }
        monkeypatch.setattr('core.wishlist_service.get_wishlist_service', lambda: wishlist)

        playlist = SimpleNamespace(name='Liked Songs', id='pl-123')
        with caplog.at_level(logging.WARNING, logger='services.sync_service'):
            svc._wishlist_unmatched(playlist, _unmatched(2))

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "expected a WARNING for real 0-of-N drops"
        text = warnings[0].getMessage()
        assert '0 of 2' in text
        assert 'ignore-list' in text


class TestWishlistSkipSurfacing:
    def test_skip_surfaces_in_progress_output(self):
        """Organize-by-playlist's wishlist skip must reach the automation
        progress output (the dashboard sync-band renders current_step)."""
        svc = _make_service()
        svc._skip_unmatched_wishlist = True
        seen = []
        svc.set_progress_callback(seen.append, playlist_name='Liked Songs')

        playlist = SimpleNamespace(name='Liked Songs', id='pl-123')
        remaining = svc._drop_skipped_unmatched_wishlist(
            playlist, _unmatched(3), total_tracks=10, matched_tracks=[1] * 7)

        assert remaining == []
        assert seen, "expected a progress update for the skip"
        assert 'Skipped wishlist for 3 unmatched tracks' in seen[0].current_step

    def test_no_skip_leaves_list_and_progress_alone(self):
        svc = _make_service()
        svc._skip_unmatched_wishlist = False
        seen = []
        svc.set_progress_callback(seen.append, playlist_name='Liked Songs')

        playlist = SimpleNamespace(name='Liked Songs', id='pl-123')
        unmatched = _unmatched(2)
        assert svc._drop_skipped_unmatched_wishlist(playlist, unmatched) is unmatched
        assert seen == []
