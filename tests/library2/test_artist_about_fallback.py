"""free sources for an artist's bio, tags and numbers when last.fm has none.

last.fm keys need a pro subscription from december 2026. last.fm stays first
for anyone whose key works; theaudiodb/wikipedia/musicbrainz/listenbrainz/
deezer only fill a field last.fm left empty, and never write the lastfm
bucket. no network here: every client is a stub.
"""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from core.enrichment import lastfm_fallback as fb
from core.library2.artist_about import (
    CHECK_KEY, check_stamp, fallback_due, lastfm_gaps, resolve_artist_about,
)
from core.library2.schema import ensure_library_v2_schema
from tests.lib2_seed import RowDb, row_conn

from .conftest import own_every_track

MBID = "8f6bd1e4-fbe1-4f50-aa9b-94c450ec0f11"
NOW = datetime(2026, 12, 5, tzinfo=timezone.utc)
FULL_LASTFM = {"bio": "From Bristol.", "tags": ["trip-hop"], "listeners": 10, "playcount": 99}


# ── which source wins, per field ─────────────────────────────────────────────

class TestResolve:
    def test_lastfm_wins_every_field_it_has(self):
        about = resolve_artist_about({
            "lastfm": FULL_LASTFM,
            "audiodb": {"bio": "audiodb bio"},
            "wikipedia": {"bio": "wiki bio", "url": "https://en.wikipedia.org/wiki/X"},
            "musicbrainz": {"genres": ["electronic"]},
            "listenbrainz": {"listeners": 5, "listens": 50},
        })
        assert (about["bio"], about["bio_source"]) == ("From Bristol.", "lastfm")
        assert (about["tags"], about["tags_source"]) == (["trip-hop"], "lastfm")
        assert (about["listeners"], about["plays"], about["popularity_source"]) == (10, 99, "lastfm")

    def test_bio_falls_to_theaudiodb_then_wikipedia(self):
        both = {"audiodb": {"bio": "audiodb bio"},
                "wikipedia": {"bio": "wiki bio", "url": "https://en.wikipedia.org/wiki/X"}}
        assert resolve_artist_about(both)["bio_source"] == "audiodb"
        wiki = resolve_artist_about({"lastfm": {"bio": "  "}, "wikipedia": both["wikipedia"]})
        assert (wiki["bio"], wiki["bio_source"], wiki["bio_url"]) == (
            "wiki bio", "wikipedia", "https://en.wikipedia.org/wiki/X")

    def test_tags_fall_to_musicbrainz_genres_then_tags(self):
        about = resolve_artist_about(json.dumps(
            {"musicbrainz": {"genres": ["trip hop"], "tags": ["bristol"]}}))
        assert (about["tags"], about["tags_source"]) == (["trip hop"], "musicbrainz")
        about = resolve_artist_about({"musicbrainz": {"tags": ["bristol"]}})
        assert about["tags"] == ["bristol"]

    def test_listenbrainz_numbers_are_labelled_listenbrainz_not_lastfm(self):
        about = resolve_artist_about({"listenbrainz": {"listeners": 5, "listens": 50},
                                      "deezer": {"fans": 7}})
        assert (about["listeners"], about["plays"], about["popularity_source"]) == (5, 50, "listenbrainz")
        # fans are their own number, never folded into listeners
        assert (about["fans"], about["fans_source"]) == (7, "deezer")

    def test_nothing_stored_is_nothing_shown(self):
        about = resolve_artist_about("{not json")
        assert about["bio"] is None and about["tags"] == [] and about["popularity_source"] is None


# ── when the free sources are asked ──────────────────────────────────────────

class TestDue:
    def test_never_when_lastfm_has_everything(self):
        assert lastfm_gaps({"lastfm": FULL_LASTFM}) == set()
        assert fallback_due({"lastfm": FULL_LASTFM}, MBID, NOW) == set()

    def test_only_the_fields_lastfm_left_empty(self):
        assert fallback_due({"lastfm": {**FULL_LASTFM, "tags": []}}, MBID, NOW) == {"tags"}

    def test_not_again_within_30_days(self):
        stored = {CHECK_KEY: check_stamp(MBID, NOW - timedelta(days=29))}
        assert fallback_due(stored, MBID, NOW) == set()

    def test_again_after_30_days(self):
        stored = {CHECK_KEY: check_stamp(MBID, NOW - timedelta(days=30))}
        assert fallback_due(stored, MBID, NOW) == {"bio", "tags", "popularity"}

    def test_again_when_the_artist_got_an_mbid_since(self):
        stored = {CHECK_KEY: check_stamp(None, NOW - timedelta(days=1))}
        assert fallback_due(stored, None, NOW) == set()
        assert fallback_due(stored, MBID, NOW) == {"bio", "tags", "popularity"}


# ── the fetcher, sources stubbed ─────────────────────────────────────────────

class _MB:
    def __init__(self, artist=None, error=None):
        self.artist, self.error, self.calls = artist, error, 0

    def get_artist(self, mbid, includes=None, raise_on_error=False):
        self.calls += 1
        assert set(includes) >= {"genres", "tags", "url-rels"}
        if self.error:
            raise self.error
        return self.artist


class _AudioDB:
    def __init__(self, bio=None):
        self.bio, self.calls = bio, []

    def lookup_artist_by_id(self, artist_id):
        self.calls.append(("id", artist_id))
        return {"strBiographyEN": self.bio} if self.bio else None

    def lookup_artist_by_mbid(self, mbid):
        self.calls.append(("mbid", mbid))
        return {"strBiographyEN": self.bio} if self.bio else None


class _Deezer:
    def get_artist_info(self, artist_id):
        return {"id": artist_id, "followers": {"total": 4321}}


class _Http:
    """wikidata, wikipedia and listenbrainz answers by url."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, method, url, *, params=None, json_body=None, host=""):
        self.calls.append(url)
        for prefix, answer in self.answers.items():
            if url.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                if callable(answer):
                    return answer(params)
                return answer
        raise AssertionError(f"unexpected request {url}")


MB_ARTIST = {
    "genres": [{"name": "electronic", "count": 3}, {"name": "trip hop", "count": 9}],
    "tags": [{"name": "bristol", "count": 2}, {"name": "seen live", "count": 0}],
    "relations": [{"type": "wikidata",
                   "url": {"resource": "https://www.wikidata.org/wiki/Q11880"}}],
}
def WIKIDATA(params):
    """wikidata answers only what it was asked: the entity's english sitelink."""
    if params.get("action") != "wbgetentities" or params.get("sitefilter") != "enwiki":
        return {"entities": {}}
    return {"entities": {params["ids"]: {"sitelinks": {
        "enwiki": {"title": "Massive Attack"}}}}}
WIKI_PAGE = {"type": "standard", "title": "Massive Attack",
             "extract": "Massive Attack are an English trip hop collective.",
             "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Massive_Attack"}}}
LB = [{"artist_mbid": MBID, "total_listen_count": 812345, "total_user_count": 23456}]


def _http(**over):
    answers = {fb.WIKIDATA_API: WIKIDATA, fb.WIKIPEDIA_SUMMARY: WIKI_PAGE,
               fb.LISTENBRAINZ_POPULARITY: LB, fb.LISTENBRAINZ_TOP_RECORDINGS: []}
    answers.update(over)
    return _Http(answers)


class TestFetch:
    def test_wikipedia_through_musicbrainz_and_wikidata(self):
        http = _http()
        buckets, failed = fb.ArtistAboutFallback(
            mb_client=_MB(MB_ARTIST), audiodb_client=_AudioDB(), http=http,
        ).fetch(needs={"bio"}, mbid=MBID)
        assert not failed
        assert buckets["wikipedia"] == {
            "bio": "Massive Attack are an English trip hop collective.",
            "url": "https://en.wikipedia.org/wiki/Massive_Attack", "title": "Massive Attack"}
        assert http.calls[0] == fb.WIKIDATA_API
        assert http.calls[1] == fb.WIKIPEDIA_SUMMARY + "Massive_Attack"

    def test_a_direct_english_wikipedia_link_skips_wikidata(self):
        artist = {"relations": [{"type": "wikipedia", "url": {
            "resource": "https://en.wikipedia.org/wiki/Portishead_(band)"}}]}
        http = _http()
        buckets, _ = fb.ArtistAboutFallback(
            mb_client=_MB(artist), audiodb_client=_AudioDB(), http=http,
        ).fetch(needs={"bio"}, mbid=MBID)
        assert http.calls == [fb.WIKIPEDIA_SUMMARY + "Portishead_%28band%29"]
        assert buckets["wikipedia"]["bio"]

    def test_a_disambiguation_page_is_no_bio(self):
        buckets, _ = fb.ArtistAboutFallback(
            mb_client=_MB(MB_ARTIST), audiodb_client=_AudioDB(),
            http=_http(**{fb.WIKIPEDIA_SUMMARY: {**WIKI_PAGE, "type": "disambiguation"}}),
        ).fetch(needs={"bio"}, mbid=MBID)
        assert "wikipedia" not in buckets

    def test_theaudiodb_bio_comes_first_and_saves_the_wikipedia_calls(self):
        http = _http()
        audiodb = _AudioDB("AudioDB says hi.")
        buckets, _ = fb.ArtistAboutFallback(
            mb_client=_MB(MB_ARTIST), audiodb_client=audiodb, http=http,
        ).fetch(needs={"bio"}, mbid=MBID, audiodb_id="111")
        assert buckets == {"audiodb": {"bio": "AudioDB says hi."}}
        assert audiodb.calls == [("id", "111")]
        assert http.calls == []

    def test_musicbrainz_genres_by_votes_and_tags_without_zero_votes(self):
        buckets, _ = fb.ArtistAboutFallback(
            mb_client=_MB(MB_ARTIST), http=_http(),
        ).fetch(needs={"tags"}, mbid=MBID)
        assert buckets == {"musicbrainz": {"genres": ["trip hop", "electronic"],
                                           "tags": ["bristol"]}}

    def test_popularity_from_listenbrainz_and_deezer_fans(self):
        mb = _MB(MB_ARTIST)
        buckets, failed = fb.ArtistAboutFallback(
            mb_client=mb, deezer_client=_Deezer(), http=_http(),
        ).fetch(needs={"popularity"}, mbid=MBID, deezer_id="123")
        assert not failed
        assert buckets == {"listenbrainz": {"listeners": 23456, "listens": 812345},
                           "deezer": {"fans": 4321}}
        assert mb.calls == 0  # numbers alone never ask musicbrainz

    def test_a_source_down_is_a_failure_not_an_empty_answer(self):
        buckets, failed = fb.ArtistAboutFallback(
            mb_client=_MB(MB_ARTIST), http=_http(**{fb.LISTENBRAINZ_POPULARITY: fb.SourceError("503")}),
        ).fetch(needs={"tags", "popularity"}, mbid=MBID)
        assert failed
        assert "musicbrainz" in buckets  # one source down costs only its field

    def test_no_ids_no_requests(self):
        http = _http()
        buckets, failed = fb.ArtistAboutFallback(
            mb_client=_MB(error=AssertionError("asked")), audiodb_client=_AudioDB(), http=http,
        ).fetch(needs={"bio", "tags", "popularity"})
        assert (buckets, failed, http.calls) == ({}, False, [])


# ── storing it on the catalogue ──────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "lib2.db")
    conn = row_conn(path)
    ensure_library_v2_schema(conn)
    own_every_track(conn)
    artist = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, musicbrainz_id) "
        "VALUES('Massive Attack','Massive Attack',?)", (MBID,)).lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
        "VALUES(?,'Mezzanine','album')", (artist,)).lastrowid
    conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Angel')", (album,))
    conn.commit()
    conn.close()
    return RowDb(path)


def _enrichment(db, artist_id=1):
    with closing(db._get_connection()) as conn:
        return json.loads(conn.execute(
            "SELECT enrichment FROM lib2_artists WHERE id=?", (artist_id,)).fetchone()[0])


def _set_enrichment(db, value, artist_id=1):
    with closing(db._get_connection()) as conn:
        conn.execute("UPDATE lib2_artists SET enrichment=? WHERE id=?",
                     (json.dumps(value), artist_id))
        conn.commit()


class _CountingFetcher(fb.ArtistAboutFallback):
    def __init__(self, **kw):
        super().__init__(mb_client=_MB(MB_ARTIST), audiodb_client=_AudioDB(),
                         deezer_client=_Deezer(), http=_http(), **kw)
        self.asked = []

    def fetch(self, **kw):
        self.asked.append(kw["needs"])
        return super().fetch(**kw)


class TestFill:
    def test_fills_every_field_with_its_source_when_lastfm_has_nothing(self, db):
        assert fb.fill_artist(db._get_connection, 1, _CountingFetcher())
        stored = _enrichment(db)
        assert "lastfm" not in stored
        about = resolve_artist_about(stored)
        assert about["bio_source"] == "wikipedia"
        assert about["tags_source"] == "musicbrainz"
        assert about["popularity_source"] == "listenbrainz"
        assert stored[CHECK_KEY]["mbid"] == MBID

    def test_never_touches_stored_lastfm_data(self, db):
        lastfm = {"bio": "Last.fm bio", "listeners": 10, "playcount": 99,
                  "url": "https://www.last.fm/music/Massive+Attack"}
        _set_enrichment(db, {"lastfm": lastfm})
        fetcher = _CountingFetcher()
        fb.fill_artist(db._get_connection, 1, fetcher)
        stored = _enrichment(db)
        assert stored["lastfm"] == lastfm
        assert fetcher.asked == [{"tags"}]  # only what last.fm left empty
        assert "wikipedia" not in stored and "listenbrainz" not in stored
        about = resolve_artist_about(stored)
        assert (about["bio_source"], about["tags_source"]) == ("lastfm", "musicbrainz")

    def test_lastfm_data_that_lands_later_takes_over(self, db):
        fb.fill_artist(db._get_connection, 1, _CountingFetcher())
        stored = _enrichment(db)
        stored["lastfm"] = FULL_LASTFM  # the user subscribed; the worker wrote
        _set_enrichment(db, stored)
        about = resolve_artist_about(_enrichment(db))
        assert {about["bio_source"], about["tags_source"], about["popularity_source"]} == {"lastfm"}

    def test_not_asked_again_within_the_ttl(self, db):
        fetcher = _CountingFetcher()
        assert fb.fill_artist(db._get_connection, 1, fetcher)
        assert not fb.fill_artist(db._get_connection, 1, fetcher)
        assert len(fetcher.asked) == 1

    def test_a_failed_source_leaves_it_due(self, db):
        fetcher = fb.ArtistAboutFallback(
            mb_client=_MB(error=RuntimeError("down")), audiodb_client=_AudioDB(),
            deezer_client=_Deezer(), http=_http())
        fb.fill_artist(db._get_connection, 1, fetcher)
        stored = _enrichment(db)
        assert CHECK_KEY not in stored
        assert "listenbrainz" in stored  # what did answer is kept

    def test_a_miss_does_not_blank_an_earlier_answer(self, db):
        _set_enrichment(db, {"wikipedia": {"bio": "kept", "url": "u"},
                             CHECK_KEY: check_stamp(MBID, NOW - timedelta(days=40))})
        fetcher = fb.ArtistAboutFallback(
            mb_client=_MB({"relations": []}), audiodb_client=_AudioDB(),
            deezer_client=_Deezer(), http=_http())
        fb.fill_artist(db._get_connection, 1, fetcher)
        assert _enrichment(db)["wikipedia"]["bio"] == "kept"

    def test_the_sweep_walks_due_artists_then_ends(self, db):
        with closing(db._get_connection()) as conn:
            row, cursor = fb.next_due_artist(conn, 0)
            assert (row["id"], cursor) == (1, 1)
            assert fb.next_due_artist(conn, cursor) == (None, None)
        fb.fill_artist(db._get_connection, 1, _CountingFetcher())
        with closing(db._get_connection()) as conn:
            assert fb.next_due_artist(conn, 0) == (None, None)


# ── the last.fm worker drives it ─────────────────────────────────────────────

class _NoKeyClient:
    api_key = ""


class _MissClient:
    api_key = "k"

    def get_artist_info(self, name):
        return None


@pytest.fixture
def worker(db, monkeypatch):
    import database.music_database as music_database
    monkeypatch.setattr(music_database, "MusicDatabase", lambda: db)
    from core.lastfm_worker import LastFMWorker

    instance = LastFMWorker(db)
    instance._fallback = _CountingFetcher()
    return instance


class TestWorker:
    def test_no_key_the_sweep_fills_artists(self, worker):
        worker.client = _NoKeyClient()
        assert not worker._lastfm_usable()
        assert worker._fallback_step() is True
        assert resolve_artist_about(_enrichment(worker.db))["bio_source"] == "wikipedia"
        # a full pass with nothing left: rest, and say so
        assert worker._fallback_step() is False
        assert worker._fallback_rest_until > 0

    def test_a_rejected_key_counts_as_no_key(self, worker):
        client = _MissClient()
        client.key_rejected = True
        worker.client = client
        assert not worker._lastfm_usable()

    def test_a_lastfm_miss_fills_straight_away(self, worker):
        worker.client = _MissClient()
        worker._process_artist(1, "Massive Attack")
        assert worker._fallback.asked == [{"bio", "tags", "popularity"}]
        assert "lastfm" not in _enrichment(worker.db)


# ── the existing workers keep what the page needs ────────────────────────────

def test_theaudiodb_worker_keeps_the_biography():
    from core.audiodb_worker import AudioDBWorker

    worker = AudioDBWorker.__new__(AudioDBWorker)
    seen = {}
    worker._write = lambda *a, **kw: seen.update(kw)
    worker._update_artist(1, {"idArtist": "111", "strBiographyEN": " A bio. "})
    assert seen["payload"] == {"bio": "A bio."}


def test_deezer_worker_keeps_the_fan_count():
    from core.deezer_worker import DeezerWorker

    worker = DeezerWorker.__new__(DeezerWorker)
    seen = {}
    worker._write = lambda *a, **kw: seen.update(kw)
    worker._update_artist(1, {"id": "123", "followers": {"total": 4321}})
    assert seen["payload"] == {"fans": 4321}


def test_the_library_artist_payload_carries_about(db):
    from core.library2.queries import get_artist

    _set_enrichment(db, {"musicbrainz": {"genres": ["trip hop"]},
                         "wikipedia": {"bio": "wiki", "url": "u"}})
    with closing(db._get_connection()) as conn:
        about = get_artist(conn, 1)["about"]
    assert (about["bio_source"], about["tags_source"]) == ("wikipedia", "musicbrainz")


# ── track popularity from listenbrainz, for the album flame ─────────────────

TOP_RECORDINGS = [
    {"recording_mbid": "rec-angel", "recording_name": "Angel", "total_listen_count": 5400,
     "total_user_count": 900},
    {"recording_mbid": "rec-teardrop", "recording_name": "Teardrop", "total_listen_count": 9100,
     "total_user_count": 1500},
    {"recording_mbid": "rec-inertia", "recording_name": "Inertia Creeps", "total_listen_count": 3000,
     "total_user_count": 700},
]


def _tracks(db):
    with closing(db._get_connection()) as conn:
        return {r[0]: json.loads(r[1] or "{}") for r in conn.execute(
            "SELECT title, enrichment FROM lib2_tracks ORDER BY id")}


def _add_track(db, title, enrichment=None, mbid=None):
    with closing(db._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_tracks(album_id,title,enrichment,musicbrainz_id) "
                     "VALUES(1,?,?,?)", (title, json.dumps(enrichment or {}), mbid))
        conn.commit()


class TestTrackPopularity:
    @pytest.fixture(autouse=True)
    def _listenbrainz_connected(self, monkeypatch):
        """the top recordings need a listenbrainz token"""
        from core import listenbrainz_auth
        monkeypatch.setattr(listenbrainz_auth, "_candidates", lambda: ["lb-token"])

    def _fetcher(self, recordings=TOP_RECORDINGS):
        return fb.ArtistAboutFallback(
            mb_client=_MB(MB_ARTIST), audiodb_client=_AudioDB(), deezer_client=_Deezer(),
            http=_http(**{fb.LISTENBRAINZ_TOP_RECORDINGS: recordings}))

    def test_tracks_without_a_lastfm_count_get_listenbrainz_listens(self, db):
        _add_track(db, "Teardrop (Remastered)", mbid="rec-teardrop")
        _add_track(db, "Inertia Creeps", {"lastfm": {"playcount": 123456}})

        assert fb.fill_artist(db._get_connection, 1, self._fetcher())

        tracks = _tracks(db)
        assert tracks["Angel"]["listenbrainz"] == {"listen_count": 5400, "listeners": 900}
        # by recording mbid when the title differs
        assert tracks["Teardrop (Remastered)"]["listenbrainz"]["listen_count"] == 9100
        # a track last.fm already counts is left to last.fm
        assert "listenbrainz" not in tracks["Inertia Creeps"]
        assert fb.TRACKS_CHECK_KEY in _enrichment(db)

    def test_not_asked_again_within_the_ttl(self, db):
        http = _http(**{fb.LISTENBRAINZ_TOP_RECORDINGS: []})
        fetcher = fb.ArtistAboutFallback(mb_client=_MB(MB_ARTIST), audiodb_client=_AudioDB(),
                                         deezer_client=_Deezer(), http=http)
        fb.fill_artist(db._get_connection, 1, fetcher)
        asked = [u for u in http.calls if u.startswith(fb.LISTENBRAINZ_TOP_RECORDINGS)]
        assert len(asked) == 1
        assert not fb.fill_artist(db._get_connection, 1, fetcher)
        with closing(db._get_connection()) as conn:
            assert fb.next_due_artist(conn, 0) == (None, None)

    def test_an_artist_lastfm_fully_knows_is_still_asked_for_its_tracks(self, db):
        """last.fm has the artist's bio and numbers, but no track counts (a new
        album since the key stopped working): the flame still needs them"""
        _set_enrichment(db, {"lastfm": FULL_LASTFM})
        with closing(db._get_connection()) as conn:
            row, _cursor = fb.next_due_artist(conn, 0)
            assert row is not None and row["id"] == 1
        assert fb.fill_artist(db._get_connection, 1, self._fetcher())
        assert _tracks(db)["Angel"]["listenbrainz"]["listen_count"] == 5400

    def test_listenbrainz_down_leaves_the_tracks_due(self, db):
        fetcher = fb.ArtistAboutFallback(
            mb_client=_MB(MB_ARTIST), audiodb_client=_AudioDB(), deezer_client=_Deezer(),
            http=_http(**{fb.LISTENBRAINZ_TOP_RECORDINGS: fb.SourceError("503")}))
        fb.fill_artist(db._get_connection, 1, fetcher)
        assert fb.TRACKS_CHECK_KEY not in _enrichment(db)

    def test_no_mbid_no_track_request(self, db):
        with closing(db._get_connection()) as conn:
            conn.execute("UPDATE lib2_artists SET musicbrainz_id = NULL")
            conn.commit()
            row = fb.load_artist(conn, 1)
            assert fb.tracks_due(conn, row) is False

    def test_the_album_flames_from_listenbrainz_after_the_fill(self, db):
        from core.library2 import queries as Q
        recordings = [{"recording_mbid": "", "recording_name": f"Song {i}",
                       "total_listen_count": n} for i, n in enumerate([4000, 300, 280, 260, 250])]
        for i in range(5):
            _add_track(db, f"Song {i}")
        fb.fill_artist(db._get_connection, 1, self._fetcher(recordings))
        with closing(db._get_connection()) as conn:
            tracks = Q.get_album(conn, 1)["tracks"]
        flamed = [(t["title"], t["popular_source"]) for t in tracks if t.get("popular")]
        assert flamed == [("Song 0", "listenbrainz")]


class TestTrackPopularityWithoutAToken:
    """listenbrainz answers 401 for top recordings without a token (nov 2026)."""

    def _fetcher(self, http):
        return fb.ArtistAboutFallback(mb_client=_MB(MB_ARTIST), audiodb_client=_AudioDB(),
                                      deezer_client=_Deezer(), http=http)

    def test_no_token_asks_nothing_and_stamps_nothing(self, db):
        _set_enrichment(db, {"lastfm": FULL_LASTFM})
        http = _http(**{fb.LISTENBRAINZ_TOP_RECORDINGS: TOP_RECORDINGS})
        fb.fill_artist(db._get_connection, 1, self._fetcher(http))
        assert not [u for u in http.calls if u.startswith(fb.LISTENBRAINZ_TOP_RECORDINGS)]
        assert fb.TRACKS_CHECK_KEY not in _enrichment(db)
        # due only for its tracks: the sweep doesn't loop on it
        with closing(db._get_connection()) as conn:
            assert fb.next_due_artist(conn, 0) == (None, None)

    def test_connecting_listenbrainz_makes_the_tracks_due_again(self, db, monkeypatch):
        _set_enrichment(db, {"lastfm": FULL_LASTFM})
        fb.fill_artist(db._get_connection, 1, self._fetcher(_http()))
        from core import listenbrainz_auth
        monkeypatch.setattr(listenbrainz_auth, "_candidates", lambda: ["lb-token"])
        listenbrainz_auth.reset()
        with closing(db._get_connection()) as conn:
            row, _cursor = fb.next_due_artist(conn, 0)
            assert row is not None and row["id"] == 1
        http = _http(**{fb.LISTENBRAINZ_TOP_RECORDINGS: TOP_RECORDINGS})
        assert fb.fill_artist(db._get_connection, 1, self._fetcher(http))
        assert _tracks(db)["Angel"]["listenbrainz"]["listen_count"] == 5400

    def test_fill_artist_never_asks_for_the_tracks_without_a_token(self, db):
        """the fill's own gate, apart from the fetcher's"""
        asked = []

        class _Fetcher(fb.ArtistAboutFallback):
            def top_recordings(self, mbid):
                asked.append(mbid)
                return []

        _set_enrichment(db, {"lastfm": FULL_LASTFM})
        fb.fill_artist(db._get_connection, 1, _Fetcher(
            mb_client=_MB(MB_ARTIST), audiodb_client=_AudioDB(), deezer_client=_Deezer(), http=_http()))
        assert asked == []
        assert fb.TRACKS_CHECK_KEY not in _enrichment(db)

    def test_top_recordings_raise_without_a_token(self):
        http = _http()
        with pytest.raises(fb.SourceError):
            self._fetcher(http).top_recordings(MBID)
        assert http.calls == []


class _Response:
    def __init__(self, status, payload=None):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


class TestHttpToken:
    @pytest.fixture
    def sent(self, monkeypatch):
        import requests
        calls, answer = [], {"status": 200}

        def request(method, url, **kw):
            calls.append((url, kw.get("headers") or {}))
            return _Response(answer["status"], [])
        monkeypatch.setattr(requests, "request", request)
        monkeypatch.setattr(fb, "_throttle", lambda host: None)
        return calls, answer

    def test_listenbrainz_gets_the_token_and_wikipedia_does_not(self, sent, monkeypatch):
        from core import listenbrainz_auth
        monkeypatch.setattr(listenbrainz_auth, "_candidates", lambda: ["lb-token"])
        listenbrainz_auth.reset()
        calls, _answer = sent
        fb._http_json("GET", fb.LISTENBRAINZ_TOP_RECORDINGS + MBID, host="api.listenbrainz.org")
        fb._http_json("GET", fb.WIKIPEDIA_SUMMARY + "X", host="en.wikipedia.org")
        assert calls[0][1]["Authorization"] == "Token lb-token"
        assert "Authorization" not in calls[1][1]

    def test_a_401_sets_the_token_aside_and_is_a_source_error(self, sent, monkeypatch):
        from core import listenbrainz_auth
        monkeypatch.setattr(listenbrainz_auth, "_candidates", lambda: ["lb-token"])
        listenbrainz_auth.reset()
        _calls, answer = sent
        answer["status"] = 401
        with pytest.raises(fb.SourceError):
            fb._http_json("GET", fb.LISTENBRAINZ_TOP_RECORDINGS + MBID, host="api.listenbrainz.org")
        assert listenbrainz_auth.token() is None
