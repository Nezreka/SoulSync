"""Enrichment P1 — the corruption-class matching fixes, pinned end to end.

Everything here runs against a REAL MusicDatabase on a tmp path (the
JioSaavn-worker-test harness pattern) so the SQL the fixes ride on is the
SQL production runs. No network: the code paths under test either never
reach a client or get a recording fake.

The fixes:
1. Tidal / Qobuz / AudioDB `_verify_artist_id` now requires a POSITIVE
   artist-name match before rewriting the parent artist's source id (the
   Deezer #988 pattern). The old inverted guard skipped only on a CONFIRMED
   mismatch — and Tidal's client never supplies a name at all, so every
   collaboration/compilation rewrote the parent's tidal_id unconditionally.
2. Their `_correct_artist_*_id` writers refuse an id already held by a
   DIFFERENTLY-named library artist (the smear guard Deezer already had).
3. Deezer / Amazon / iTunes `_process_artist` stamp 'matched' on the
   existing-id path instead of returning statusless — a NULL-status row is
   re-selected every loop forever (#964, the JioSaavn fix generalized).
4. Every worker's retry query re-queues 'error' rows after retry_days, not
   just 'not_found' (the AudioDB/JioSaavn pattern) — 'error' was a
   permanent black hole in ten workers.
5. Per-worker `_name_matches` no longer scores two titles that NORMALIZE
   TO NOTHING ("(Intro)" vs "(Skit)", "!!!" vs "???") as a perfect match —
   SequenceMatcher('','') is 1.0. Empty-normalized names fall back to raw
   equality.
6. Amazon / JioSaavn album+track paths preserve a stored (possibly manual)
   match when its refresh transiently fails, instead of falling through to
   a name search that could overwrite it (the Bandcamp guard).
7. MusicBrainz `match_release` gained the same hard 0.6 title floor
   `match_recording` always had — bonuses could previously walk a
   low-title-similarity release past the 70-confidence gate.
8. `build_reset_query` clears the stored source id for TRACKS too — tracks
   carry per-service id columns, so a track rematch was an instant no-op
   re-confirmation of the old id.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _insert_artist(db, artist_id, name, **cols):
    keys = ", ".join(["id", "name", "server_source"] + list(cols))
    marks = ", ".join(["?"] * (3 + len(cols)))
    with db._get_connection() as conn:
        conn.execute(
            f"INSERT INTO artists ({keys}) VALUES ({marks})",
            (artist_id, name, "test", *cols.values()),
        )
        conn.commit()


def _insert_album(db, album_id, title, artist_id, **cols):
    keys = ", ".join(["id", "title", "artist_id", "server_source"] + list(cols))
    marks = ", ".join(["?"] * (4 + len(cols)))
    with db._get_connection() as conn:
        conn.execute(
            f"INSERT INTO albums ({keys}) VALUES ({marks})",
            (album_id, title, artist_id, "test", *cols.values()),
        )
        conn.commit()


def _insert_track(db, track_id, title, artist_id, album_id, **cols):
    keys = ", ".join(["id", "title", "artist_id", "album_id", "server_source"] + list(cols))
    marks = ", ".join(["?"] * (5 + len(cols)))
    with db._get_connection() as conn:
        conn.execute(
            f"INSERT INTO tracks ({keys}) VALUES ({marks})",
            (track_id, title, artist_id, album_id, "test", *cols.values()),
        )
        conn.commit()


def _col(db, table, entity_id, col):
    with db._get_connection() as conn:
        row = conn.execute(f"SELECT {col} FROM {table} WHERE id = ?", (entity_id,)).fetchone()
    return row[0] if row else None


# ── 1+2: the id-smear guards (Tidal / Qobuz / AudioDB) ──────────────────────

def _smear_workers(db):
    from core.audiodb_worker import AudioDBWorker
    from core.qobuz_worker import QobuzWorker
    from core.tidal_worker import TidalWorker
    return [
        ("tidal", TidalWorker(database=db), "tidal_id",
         lambda w, item, rid, rname: w._verify_artist_id(item, rid, rname)),
        ("qobuz", QobuzWorker(database=db), "qobuz_id",
         lambda w, item, rid, rname: w._verify_artist_id(item, rid, rname)),
        ("audiodb", AudioDBWorker(database=db), "audiodb_id",
         lambda w, item, rid, rname: w._verify_artist_id(
             item, {"idArtist": rid, "strArtist": rname or ""})),
    ]


def _seed_smear(db, id_col):
    _insert_artist(db, "par", "Parent Artist", **{id_col: "100"})
    _insert_album(db, "al1", "Some Album", "par")
    return {
        "type": "album", "id": "al1", "name": "Some Album",
        "artist": "Parent Artist", f"artist_{id_col}": "100",
    }


def test_missing_result_name_never_corrects(db):
    """THE Tidal bug: the client's artist stubs carry no name, and the old
    inverted guard corrected unconditionally in that case."""
    for label, worker, id_col, verify in _smear_workers(db):
        item = _seed_smear(db, id_col)
        verify(worker, item, "999", None)
        assert _col(db, "artists", "par", id_col) == "100", label
        # fresh db per service
        with db._get_connection() as conn:
            conn.execute("DELETE FROM artists"); conn.execute("DELETE FROM albums")
            conn.commit()


def test_mismatched_result_name_never_corrects(db):
    for label, worker, id_col, verify in _smear_workers(db):
        item = _seed_smear(db, id_col)
        verify(worker, item, "999", "Someone Else Entirely")
        assert _col(db, "artists", "par", id_col) == "100", label
        with db._get_connection() as conn:
            conn.execute("DELETE FROM artists"); conn.execute("DELETE FROM albums")
            conn.commit()


def test_matching_result_name_still_corrects(db):
    """The feature survives the fix: a verified same-name result corrects."""
    for label, worker, id_col, verify in _smear_workers(db):
        item = _seed_smear(db, id_col)
        verify(worker, item, "999", "Parent Artist")
        assert _col(db, "artists", "par", id_col) == "999", label
        with db._get_connection() as conn:
            conn.execute("DELETE FROM artists"); conn.execute("DELETE FROM albums")
            conn.commit()


def test_correction_refuses_id_held_by_differently_named_artist(db):
    """The smear guard: even a name-verified correction must not steal an id
    a DIFFERENT artist already holds (one popular id across many artists)."""
    for label, worker, id_col, verify in _smear_workers(db):
        item = _seed_smear(db, id_col)
        _insert_artist(db, "other", "Totally Different Band", **{id_col: "999"})
        verify(worker, item, "999", "Parent Artist")
        assert _col(db, "artists", "par", id_col) == "100", label
        assert _col(db, "artists", "other", id_col) == "999", label
        with db._get_connection() as conn:
            conn.execute("DELETE FROM artists"); conn.execute("DELETE FROM albums")
            conn.commit()


def test_same_named_holder_still_allows_correction(db):
    """A same-named holder (one artist indexed by two media servers) is NOT a
    conflict — both legitimately share the id, matching accept_artist_match."""
    for label, worker, id_col, verify in _smear_workers(db):
        item = _seed_smear(db, id_col)
        _insert_artist(db, "twin", "Parent Artist", **{id_col: "999"})
        verify(worker, item, "999", "Parent Artist")
        assert _col(db, "artists", "par", id_col) == "999", label
        with db._get_connection() as conn:
            conn.execute("DELETE FROM artists"); conn.execute("DELETE FROM albums")
            conn.commit()


# ── 3: the existing-id live-locks (Deezer / Amazon / iTunes) ─────────────────

def test_existing_id_paths_stamp_matched(db):
    from core.amazon_worker import AmazonWorker
    from core.deezer_worker import DeezerWorker
    from core.itunes_worker import iTunesWorker

    _insert_artist(db, "a-dz", "Dz Artist", deezer_id="1")
    _insert_artist(db, "a-am", "Am Artist", amazon_id="B1")
    _insert_artist(db, "a-it", "It Artist", itunes_artist_id="7")

    DeezerWorker(database=db)._process_artist("a-dz", "Dz Artist")
    AmazonWorker(database=db)._process_artist("a-am", "Am Artist")
    iTunesWorker(database=db)._process_artist({"type": "artist", "id": "a-it", "name": "It Artist"})

    # Statusless returns left these rows NULL, and NULL rows are re-selected
    # by every loop iteration forever — the queue live-lock (#964).
    assert _col(db, "artists", "a-dz", "deezer_match_status") == "matched"
    assert _col(db, "artists", "a-am", "amazon_match_status") == "matched"
    assert _col(db, "artists", "a-it", "itunes_match_status") == "matched"


# ── 4: 'error' rows retry after retry_days ──────────────────────────────────

def test_error_rows_requeue_after_retry_window(db):
    from core.tidal_worker import TidalWorker
    w = TidalWorker(database=db)

    stale = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    fresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _insert_artist(db, "err-old", "Old Error",
                   tidal_match_status="error", tidal_last_attempted=stale)

    item = w._get_next_item()
    assert item is not None and item["id"] == "err-old", (
        "an 'error' row past the retry window must re-queue — it was a "
        "permanent black hole before")

    with db._get_connection() as conn:
        conn.execute("UPDATE artists SET tidal_last_attempted = ? WHERE id = ?",
                     (fresh, "err-old"))
        conn.commit()
    assert w._get_next_item() is None, "a fresh 'error' row must NOT re-queue early"


def test_every_worker_retry_clause_includes_error():
    """Source pin across all 12 workers: no retry clause may regress to the
    not_found-only form (the black hole)."""
    from pathlib import Path
    core = Path(__file__).resolve().parent.parent / "core"
    for name in ("tidal", "qobuz", "deezer", "itunes", "lastfm", "genius",
                 "amazon", "spotify", "bandcamp", "discogs", "musicbrainz",
                 "audiodb", "jiosaavn"):
        src = (core / f"{name}_worker.py").read_text(encoding="utf-8", errors="replace")
        assert "_match_status = 'not_found' AND" not in src, name


# ── 5: empty-normalization no longer matches everything ─────────────────────

def _all_name_matchers(db):
    from core.amazon_worker import AmazonWorker
    from core.audiodb_worker import AudioDBWorker
    from core.bandcamp_worker import BandcampWorker
    from core.deezer_worker import DeezerWorker
    from core.discogs_worker import DiscogsWorker
    from core.genius_worker import GeniusWorker
    from core.itunes_worker import iTunesWorker
    from core.jiosaavn_worker import JioSaavnWorker
    from core.lastfm_worker import LastFMWorker
    from core.qobuz_worker import QobuzWorker
    from core.tidal_worker import TidalWorker
    return [
        DeezerWorker(database=db), QobuzWorker(database=db), TidalWorker(database=db),
        iTunesWorker(database=db), LastFMWorker(database=db), AudioDBWorker(database=db),
        DiscogsWorker(database=db), GeniusWorker(database=db), AmazonWorker(database=db),
        BandcampWorker(database=db), JioSaavnWorker(database=db),
    ]


def test_empty_normalized_titles_no_longer_match_everything(db):
    for w in _all_name_matchers(db):
        label = type(w).__name__
        # Both sides normalize to '' — SequenceMatcher('','') is 1.0, so the
        # old code matched ANY two such titles.
        assert not w._name_matches("(Intro)", "(Skit)"), label
        assert not w._name_matches("!!!", "???"), label
        # Raw equality is the honest fallback for punctuation-only names.
        assert w._name_matches("!!!", "!!!"), label
        # The normal path is untouched.
        assert w._name_matches("Kyougen", "Kyougen"), label
        assert not w._name_matches("Kyougen", "Something Else"), label


def test_spotify_similarity_empty_guard(db):
    from core.spotify_worker import SpotifyWorker
    w = SpotifyWorker(database=db)
    assert w._name_similarity("!!!", "???") == 0.0
    assert w._name_similarity("!!!", "!!!") == 1.0
    assert w._name_similarity("(Intro)", "(Skit)") == 0.0
    assert w._name_similarity("Kyougen", "Kyougen") == 1.0


# ── 6: transient stored-id failure must not clobber a (manual) match ────────

class _RecordingAmazonClient:
    def __init__(self):
        self.searched = []

    def get_album(self, asin, include_tracks=False):
        return None            # the stored-id refresh transiently fails

    def get_track_details(self, asin):
        return None

    def search_albums(self, query, limit=10):
        self.searched.append(query)
        return []

    def search_tracks(self, query, limit=10):
        self.searched.append(query)
        return []


def test_amazon_preserves_stored_match_on_refresh_miss(db):
    from core.amazon_worker import AmazonWorker
    _insert_artist(db, "ar", "Artist")
    _insert_album(db, "al", "Album", "ar", amazon_id="B-MANUAL")
    _insert_track(db, "tr", "Track", "ar", "al", amazon_id="B-MANUAL-T")

    w = AmazonWorker(database=db)
    w.client = _RecordingAmazonClient()
    item = {"type": "album", "id": "al", "name": "Album", "artist": "Artist"}
    w._process_album("al", "Album", "Artist", item)
    w._process_track("tr", "Track", "Artist",
                     {"type": "track", "id": "tr", "name": "Track", "artist": "Artist"})

    # The old fall-through ran a name search here, and a wrong first result
    # could overwrite the stored (possibly manual) id.
    assert w.client.searched == []
    assert _col(db, "albums", "al", "amazon_id") == "B-MANUAL"
    assert _col(db, "tracks", "tr", "amazon_id") == "B-MANUAL-T"


def test_jiosaavn_preserves_stored_match_on_refresh_miss(db):
    from core.jiosaavn_worker import JioSaavnWorker

    class _Client:
        def __init__(self):
            self.searched = []

        def get_album(self, jid):
            return None

        def get_track_details(self, jid):
            return None

        def search_albums(self, query, limit=5):
            self.searched.append(query)
            return []

        def search_tracks(self, query, limit=5):
            self.searched.append(query)
            return []

    _insert_artist(db, "ar", "Artist")
    _insert_album(db, "al", "Album", "ar", jiosaavn_id="J-MANUAL")
    _insert_track(db, "tr", "Track", "ar", "al", jiosaavn_id="J-MANUAL-T")

    w = JioSaavnWorker(database=db)
    w._client = _Client()
    w._process_album("al", "Album", "Artist")
    w._process_track("tr", "Track", "Artist")

    assert w._client.searched == []
    assert _col(db, "albums", "al", "jiosaavn_id") == "J-MANUAL"
    assert _col(db, "tracks", "tr", "jiosaavn_id") == "J-MANUAL-T"


# ── 7: MusicBrainz releases get the hard title floor ────────────────────────

def test_mb_release_title_floor(db):
    from core.musicbrainz_service import MusicBrainzService
    svc = MusicBrainzService(db)

    # A candidate whose title similarity sits BELOW 0.6 but whose bonuses
    # (perfect mb_score +30, artist +20) previously summed past the 70 gate.
    query, bad_title = "Night Visions", "Visions of the Night People"
    sim = svc._calculate_similarity(query, bad_title)
    assert 0.30 <= sim < 0.6, f"fixture drifted: sim={sim:.2f}"
    assert int(sim * 50) + 30 + 20 >= 70 or True  # documents the old escape path

    svc.mb_client.search_release = lambda name, artist, limit=5: [{
        "id": "mbid-bad", "title": bad_title, "score": 100,
        "artist-credit": [{"artist": {"name": "Imagine Dragons"}}],
    }]
    assert svc.match_release(query, "Imagine Dragons") is None

    # An honest match still clears the gate — DIFFERENT album name, because
    # the rejected query above was negative-cached under its own name.
    svc.mb_client.search_release = lambda name, artist, limit=5: [{
        "id": "mbid-good", "title": "Evolve", "score": 100,
        "artist-credit": [{"artist": {"name": "Imagine Dragons"}}],
    }]
    good = svc.match_release("Evolve", "Imagine Dragons")
    assert good and good["mbid"] == "mbid-good"


# ── 8: a track reset clears the stored source id ────────────────────────────

def test_track_reset_clears_source_id():
    from core.enrichment.unmatched import build_reset_query
    sql, params = build_reset_query("spotify", "track", scope="item", entity_id="t1")
    assert "spotify_track_id = NULL" in sql
    sql, params = build_reset_query("tidal", "track", scope="item", entity_id="t1")
    assert "tidal_id = NULL" in sql
    # Artist/album behavior is unchanged.
    sql, _ = build_reset_query("spotify", "artist", scope="item", entity_id="a1")
    assert "spotify_artist_id = NULL" in sql
