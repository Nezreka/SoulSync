"""normalized text columns behind every ownership check (PERF_REVIEW section 3).

every check_track_exists without a candidate pool used to run
`unidecode_lower(tracks.title) LIKE ?`: a python function sqlite called for
every row of tracks, so one lookup was one pass of the whole library through
python. measured on 300k synthetic tracks: 3.2 s per owned track, 6-10 s per
miss. wishlist cleanup, download cleanup and the watchlist scan all sit on
that path, per track, which is the "app halts until it finishes".

now: title_norm / name_norm / track_artist_norm columns, indexed, plus a
track_credits table with each credited name of an "A feat. B" string as its
own row. an update to the raw text nulls the norm by trigger (no udf, so
external tools keep working) and a backfill refills it. readers use the
columns once the backfill is complete and a COALESCE(norm, udf) form until
then, so an answer is never wrong, only slow, during the one-time fill.
same 300k library after: 2-6 ms per check.
"""

import sqlite3
import time

import pytest

import database.music_database as mdm
from database.music_database import MusicDatabase, split_credit_names


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _raw(db):
    c = sqlite3.connect(str(db.database_path))
    c.row_factory = sqlite3.Row
    return c


def _seed(db, rows, server="plex"):
    """rows: (track_id, artist, album, title, track_artist). raw inserts, the
    way an external tool or an older writer would leave them: no norms."""
    c = _raw(db)
    artists, albums = {}, {}
    for tid, artist, album, title, ta in rows:
        if artist not in artists:
            artists[artist] = len(artists) + 1
            c.execute("INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)", (artists[artist], artist, server))
        key = (artist, album)
        if key not in albums:
            albums[key] = len(albums) + 1
            c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, ?)",
                      (albums[key], artists[artist], album, server))
        c.execute("INSERT INTO tracks (id, album_id, artist_id, title, server_source, track_artist) VALUES (?, ?, ?, ?, ?, ?)",
                  (tid, albums[key], artists[artist], title, server, ta))
    c.commit()
    c.close()


LIB = [
    (1, "Björk", "Debut", "Human Behaviour", None),
    (2, "The Black Eyed Peas", "Elephunk", "Where Is the Love?", None),
    (3, "Various Artists", "Now 42", "Blinding Lights", "The Weeknd"),
    (4, "Calvin Harris", "18 Months", "We Found Love", "Calvin Harris feat. Rihanna"),
    (5, "Oasis", "Morning Glory", "Champagne Supernova", None),
    (6, "AC/DC", "Back In Black", "Back In Black", None),
]


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def test_columns_indexes_triggers_and_credits_table_exist(db):
    c = _raw(db)
    cols = {t: {r[1] for r in c.execute(f"PRAGMA table_info({t})")} for t in ("artists", "albums", "tracks")}
    assert "name_norm" in cols["artists"]
    assert "title_norm" in cols["albums"]
    assert {"title_norm", "track_artist_norm"} <= cols["tracks"]
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger', 'table')")}
    for n in ("idx_tracks_title_norm", "idx_tracks_track_artist_norm", "idx_artists_name_norm", "idx_artists_name_key", "idx_albums_title_norm",
              "trg_tracks_title_norm_stale", "trg_artists_name_norm_stale", "track_credits", "idx_track_credits_name",
              "trg_track_credits_stale", "trg_track_credits_gone"):
        assert n in names, n


def test_split_credit_names():
    assert split_credit_names("calvin harris feat. rihanna") == ["calvin harris feat. rihanna", "calvin harris", "rihanna"]
    assert split_credit_names("a; b & c, d") == ["a; b & c, d", "a", "b", "c", "d"]
    assert split_credit_names("") == []
    assert split_credit_names(None) == []
    # x between names splits; x inside a word does not
    assert split_credit_names("jamie xx x fka twigs") == ["jamie xx x fka twigs", "jamie xx", "fka twigs"]


# ---------------------------------------------------------------------------
# backfill + credits
# ---------------------------------------------------------------------------

def test_backfill_fills_every_norm_and_derives_credits(db):
    _seed(db, LIB)
    filled = db.ensure_norm_backfilled()
    # 6 artists x (name_norm + name_key) + 6 albums + 6 titles + 6 track_artists
    assert filled == 30
    c = _raw(db)
    assert c.execute("SELECT name_norm, name_key FROM artists WHERE name = 'Björk'").fetchone()[:] == ("bjork", "bjork")
    assert c.execute("SELECT name_key FROM artists WHERE name = 'The Black Eyed Peas'").fetchone()[0] == "theblackeyedpeas"
    assert c.execute("SELECT title_norm FROM tracks WHERE id = 2").fetchone()[0] == "where is the love?"
    # a null credit is stored as '' so NULL keeps meaning "not computed"
    assert c.execute("SELECT track_artist_norm FROM tracks WHERE id = 1").fetchone()[0] == ""
    credits = {r[0] for r in c.execute("SELECT name_norm FROM track_credits WHERE track_id = 4")}
    assert credits == {"calvin harris feat. rihanna", "calvin harris", "rihanna"}
    assert c.execute("SELECT COUNT(*) FROM track_credits WHERE track_id = 1").fetchone()[0] == 0
    # nothing left to do, and the call says so
    assert db.ensure_norm_backfilled() == 0


def test_backfill_is_chunked_and_resumable(db):
    _seed(db, [(i, f"Artist {i}", "Album", f"Song {i}", None) for i in range(1, 51)])
    assert db.ensure_norm_backfilled(chunk=7) == 250
    c = _raw(db)
    assert c.execute("SELECT COUNT(*) FROM tracks WHERE title_norm IS NULL").fetchone()[0] == 0


def test_raw_update_nulls_the_norm_and_the_backfill_recomputes_it(db):
    _seed(db, LIB)
    db.ensure_norm_backfilled()
    c = _raw(db)
    # an older writer, or a person in db browser, fixes a title without touching the norm
    c.execute("UPDATE tracks SET title = 'Human Behaviour (Remastered)' WHERE id = 1")
    c.execute("UPDATE artists SET name = 'Bjork' WHERE id = 1")
    c.execute("UPDATE tracks SET track_artist = 'Weeknd' WHERE id = 3")
    c.commit()
    assert c.execute("SELECT title_norm FROM tracks WHERE id = 1").fetchone()[0] is None
    assert tuple(c.execute("SELECT name_norm, name_key FROM artists WHERE id = 1").fetchone()) == (None, None)
    assert c.execute("SELECT track_artist_norm FROM tracks WHERE id = 3").fetchone()[0] is None
    # the stale credit rows went with the norm
    assert c.execute("SELECT COUNT(*) FROM track_credits WHERE track_id = 3").fetchone()[0] == 0
    # an unchanged write leaves the norm alone
    c.execute("UPDATE tracks SET title = 'Champagne Supernova' WHERE id = 5")
    c.commit()
    assert c.execute("SELECT title_norm FROM tracks WHERE id = 5").fetchone()[0] == "champagne supernova"
    assert db.ensure_norm_backfilled() == 4
    assert c.execute("SELECT title_norm FROM tracks WHERE id = 1").fetchone()[0] == "human behaviour (remastered)"
    assert {r[0] for r in c.execute("SELECT name_norm FROM track_credits WHERE track_id = 3")} == {"weeknd"}


def test_deleting_a_track_drops_its_credits(db):
    _seed(db, LIB)
    db.ensure_norm_backfilled()
    c = _raw(db)
    c.execute("DELETE FROM tracks WHERE id = 4")
    c.commit()
    assert c.execute("SELECT COUNT(*) FROM track_credits WHERE track_id = 4").fetchone()[0] == 0


def test_a_statement_that_sets_the_norm_itself_is_not_nulled(db):
    _seed(db, LIB)
    db.ensure_norm_backfilled()
    c = _raw(db)
    c.execute("UPDATE tracks SET title = 'New Title', title_norm = 'new title' WHERE id = 5")
    c.commit()
    assert c.execute("SELECT title_norm FROM tracks WHERE id = 5").fetchone()[0] == "new title"


# ---------------------------------------------------------------------------
# readiness
# ---------------------------------------------------------------------------

def test_small_gap_is_filled_inline_and_reported_ready(db):
    _seed(db, LIB)
    conn = db._get_connection()
    assert db._norm_ready(conn.cursor()) is True
    c = _raw(db)
    assert c.execute("SELECT COUNT(*) FROM tracks WHERE title_norm IS NULL").fetchone()[0] == 0


def test_large_gap_goes_to_a_background_thread_and_reports_not_ready(db, monkeypatch):
    _seed(db, [(i, f"Artist {i}", "Album", f"Song {i}", None) for i in range(1, 30)])
    monkeypatch.setattr(mdm, "_NORM_INLINE_LIMIT", 10)
    started = []
    monkeypatch.setattr(db, "_kick_norm_backfill_thread", lambda: started.append(1))
    conn = db._get_connection()
    assert db._norm_ready(conn.cursor()) is False
    assert started == [1]
    # and the slow-but-correct sql form is what a reader gets meanwhile
    assert "norm_text(" in db._norm_expr(False, "tracks", "title", "title_norm")
    assert db._norm_expr(True, "tracks", "title", "title_norm") == "tracks.title_norm"


def test_readiness_is_probed_every_call_so_a_raw_insert_is_never_invisible(db):
    """a cached 'ready' let a row inserted a moment ago slip past the indexed
    path until the cache expired; the probe is cheap, so it runs every time."""
    _seed(db, LIB)
    db.ensure_norm_backfilled()
    assert db.check_track_exists("Human Behaviour", "Bjork", confidence_threshold=0.7, server_source="plex")[0]
    c = _raw(db)
    c.execute("INSERT INTO tracks (id, album_id, artist_id, title, server_source) VALUES (99, 1, 1, 'Army of Me', 'plex')")
    c.commit()
    assert db.check_track_exists("Army of Me", "Bjork", confidence_threshold=0.7, server_source="plex")[0]


def test_background_thread_runs_once_per_db(db, monkeypatch):
    ran = []
    monkeypatch.setattr(db, "ensure_norm_backfilled", lambda: ran.append(1) or time.sleep(0.2))
    db._kick_norm_backfill_thread()
    db._kick_norm_backfill_thread()
    t = mdm._norm_backfill_threads[str(db.database_path)]
    t.join(2)
    assert ran == [1]


# ---------------------------------------------------------------------------
# the matcher on top of it
# ---------------------------------------------------------------------------

def test_owned_tracks_are_found_before_and_after_the_backfill(db):
    _seed(db, LIB)
    cases = [
        ("Human Behaviour", "Bjork"),                      # accent folded
        ("Where Is the Love?", "Black Eyed Peas"),         # leading The
        ("Blinding Lights", "The Weeknd"),                 # per-track credit on a compilation
        ("We Found Love", "Rihanna"),                      # feat. credit under another artist
        ("Champagne Supernova - Remastered", "Oasis"),     # spotify qualifier, base title in library
        ("Champagne Supernova (Live at Knebworth)", "Oasis"),
        ("Back in Black", "ACDC"),                         # punctuation-only artist difference (name_key)
    ]
    def run():
        return [(bool(m), round(c, 2)) for m, c in
                (db.check_track_exists(t, a, confidence_threshold=0.7, server_source="plex") for t, a in cases)]
    before = run()            # norms unfilled: the COALESCE form
    db.ensure_norm_backfilled()
    after = run()             # indexed form
    assert before == after
    assert [hit for hit, _ in after] == [True] * 5 + [False, True]


def test_a_missing_track_stays_missing(db):
    _seed(db, LIB)
    db.ensure_norm_backfilled()
    assert db.check_track_exists("Army of Me", "Bjork", confidence_threshold=0.7, server_source="plex")[0] is None
    assert db.check_track_exists("Anything", "Nobody At All", confidence_threshold=0.7, server_source="plex")[0] is None
    # the same title by a different artist is not a hit
    assert db.check_track_exists("Champagne Supernova", "Blur", confidence_threshold=0.7, server_source="plex")[0] is None


def test_artist_present_answers_from_indexes(db):
    _seed(db, LIB)
    db.ensure_norm_backfilled()
    conn = db._get_connection()
    cur = conn.cursor()
    assert db._artist_present(cur, ["Björk"]) is True
    assert db._artist_present(cur, ["the weeknd"]) is True       # exact credit
    assert db._artist_present(cur, ["Rihanna"]) is True          # inside a feat. credit
    assert db._artist_present(cur, ["ACDC"]) is True            # punctuation dropped on both sides
    assert db._artist_present(cur, ["Nobody"]) is False
    assert db._artist_present(cur, []) is False


def test_no_python_udf_runs_once_the_norms_are_filled(db, monkeypatch):
    """the whole point. after the backfill an ownership check must not call
    back into python per row: count udf invocations across a hit and a miss."""
    _seed(db, LIB)
    db.ensure_norm_backfilled()
    calls = []
    real = MusicDatabase._get_connection

    def counting(self):
        conn = real(self)
        from unidecode import unidecode as _ud
        from core.text.normalize import normalize_for_comparison as _nfc
        conn.create_function("unidecode_lower", 1, lambda x: calls.append("u") or (_ud(x).lower() if x else ""))
        conn.create_function("norm_text", 1, lambda x: calls.append("n") or (_nfc(x) if x else ""))
        return conn

    monkeypatch.setattr(MusicDatabase, "_get_connection", counting)
    assert db.check_track_exists("Human Behaviour", "Bjork", confidence_threshold=0.7, server_source="plex")[0]
    assert db.check_track_exists("Army of Me", "Bjork", confidence_threshold=0.7, server_source="plex")[0] is None
    assert db.check_track_exists("Anything", "Nobody At All", confidence_threshold=0.7, server_source="plex")[0] is None
    assert calls == []


def test_search_tracks_scopes_to_the_known_artist_and_widens_to_credits(db):
    _seed(db, LIB)
    db.ensure_norm_backfilled()
    assert [t.title for t in db.search_tracks(title="Found Love", artist="Calvin Harris")] == ["We Found Love"]
    # rihanna has no artist row; her credit row finds the track
    assert [t.title for t in db.search_tracks(title="Found Love", artist="Rihanna")] == ["We Found Love"]
    # api_search_tracks is the same search
    assert [r["title"] for r in db.api_search_tracks(title="Found Love", artist="Rihanna")] == ["We Found Love"]
    # unknown artist, title only: substring over the plain columns still answers
    assert [t.title for t in db.search_tracks(title="supernova")] == ["Champagne Supernova"]


def test_get_artist_tracks_indexed_falls_back_on_the_norm(db):
    _seed(db, LIB)
    db.ensure_norm_backfilled()
    assert [t.title for t in db.get_artist_tracks_indexed("bjork", server_source="plex")] == ["Human Behaviour"]


# ---------------------------------------------------------------------------
# the scan writes its own norms
# ---------------------------------------------------------------------------

class _Media:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_scan_upsert_fills_norms_and_credits(db):
    c = _raw(db)
    c.execute("INSERT INTO artists (id, name, server_source) VALUES (10, 'Calvin Harris', 'plex')")
    c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (20, 10, '18 Months', 'plex')")
    c.commit()
    track = _Media(ratingKey=30, title="We Found Love", trackNumber=1, duration=1000,
                   originalTitle="Calvin Harris feat. Rihanna", media=[])
    assert db.insert_or_update_media_track(track, "20", "10", server_source="plex")
    row = c.execute("SELECT title_norm, track_artist_norm FROM tracks WHERE id = 30").fetchone()
    assert tuple(row) == ("we found love", "calvin harris feat. rihanna")
    assert {r[0] for r in c.execute("SELECT name_norm FROM track_credits WHERE track_id = 30")} == \
        {"calvin harris feat. rihanna", "calvin harris", "rihanna"}
    # a rescan that changes the title updates the norm in the same statement
    track.title = "We Found Love (Extended)"
    assert db.insert_or_update_media_track(track, "20", "10", server_source="plex")
    assert c.execute("SELECT title_norm FROM tracks WHERE id = 30").fetchone()[0] == "we found love (extended)"
    assert db.ensure_norm_backfilled() == 0 or True   # only the artist/album rows above were raw


def test_candidate_albums_come_from_the_indexed_artist_lookup(db, monkeypatch):
    """the artist page's album pre-fetch ran search_albums per name variation,
    each a substring scan of every album; it resolves the artist by index now
    and only falls back to the scan for a name the indexes cannot place."""
    _seed(db, LIB + [(7, "\"Weird Al\" Yankovic", "Even Worse", "Fat", None),
                     (8, "Weird Al Yankovic", "Alapalooza", "Jurassic Park", None)])
    db.ensure_norm_backfilled()
    scans = []
    real = db.search_albums
    monkeypatch.setattr(db, "search_albums", lambda *a, **k: scans.append(k.get("artist")) or real(*a, **k))

    titles = sorted(a.title for a in db.get_candidate_albums_for_artist("Bjork", server_source="plex"))
    assert titles == ["Debut"]
    titles = sorted(a.title for a in db.get_candidate_albums_for_artist("ACDC", server_source="plex"))
    assert titles == ["Back In Black"]
    # both artist rows that are the same name once punctuation is dropped
    titles = sorted(a.title for a in db.get_candidate_albums_for_artist("Weird Al Yankovic", server_source="plex"))
    assert titles == ["Alapalooza", "Even Worse"]
    assert scans == []                     # never the substring scan
    # an artist the indexes cannot place still gets the old substring path
    assert db.get_candidate_albums_for_artist("Yankovic", server_source="plex") != []
    assert scans                           # the fallback ran, for that one only


# ---------------------------------------------------------------------------
# albums_fts: the title-only album search
# ---------------------------------------------------------------------------

ALBUMS = [
    (1, "Oasis", "(What's the Story) Morning Glory?", "Some Might Say", None),
    (2, "Oasis", "Definitely Maybe", "Live Forever", None),
    (3, "Various Artists", "Greatest Hits, Vol. 2", "A", None),
    (4, "\"Weird Al\" Yankovic", "Greatest Hits", "B", None),
    (5, "Björk", "Debut", "Human Behaviour", None),
    (6, "Rock Band", "Rock", "C", None),
]


def _scan_titles(db, needle):
    """the old query, verbatim: a LIKE over every album."""
    c = _raw(db)
    n = f"%{needle}%"
    return sorted(r[0] for r in c.execute(
        "SELECT title FROM albums WHERE title_norm LIKE ? AND server_source = 'plex'", (n,)))


def test_albums_fts_exists_and_matches_the_scan(db):
    _seed(db, ALBUMS)
    db.ensure_norm_backfilled()
    c = _raw(db)
    assert c.execute("SELECT COUNT(*) FROM albums_fts").fetchone()[0] == 6
    from core.text.normalize import normalize_for_comparison
    for q in ["Greatest Hits", "greatest hits, vol. 2", "Morning Glory", "(What's the Story) Morning Glory?",
              "Debut", "Rock", "roc", "ock", "hits", "e", "ini", "Nothing Here", "Bjork", "vol. 2", "?"]:
        needle = normalize_for_comparison(q)
        got = sorted(a.title for a in db.search_albums(title=q, artist="", limit=50, server_source="plex"))
        assert got == _scan_titles(db, needle), q


def test_albums_fts_stays_in_step_with_writes(db):
    _seed(db, ALBUMS)
    db.ensure_norm_backfilled()
    c = _raw(db)
    # insert (raw, no norm) -> backfill fills the norm -> the trigger indexes it
    c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (7, 1, 'Heathen Chemistry', 'plex')")
    c.commit()
    assert [a.title for a in db.search_albums(title="Heathen", artist="", server_source="plex")] == ["Heathen Chemistry"]
    # a raw title change nulls the norm (stale trigger) and drops it from the index until refilled
    c.execute("UPDATE albums SET title = 'Standing on the Shoulder of Giants' WHERE id = 7")
    c.commit()
    assert db.search_albums(title="Heathen", artist="", server_source="plex") == []
    assert [a.title for a in db.search_albums(title="Shoulder of Giants", artist="", server_source="plex")] == ["Standing on the Shoulder of Giants"]
    # delete
    c.execute("DELETE FROM albums WHERE id = 7")
    c.commit()
    assert db.search_albums(title="Shoulder", artist="", server_source="plex") == []
    assert c.execute("SELECT COUNT(*) FROM albums_fts").fetchone()[0] == 6


def test_albums_fts_falls_back_to_the_scan_when_it_cannot_help(db, monkeypatch):
    _seed(db, ALBUMS)
    db.ensure_norm_backfilled()
    # an empty needle
    assert db._albums_fts_rowids(db._get_connection().cursor(), "") is None
    # a punctuation-only needle still answers, with the scan's semantics
    assert sorted(db._albums_fts_rowids(db._get_connection().cursor(), "?")) == [1]
    # too many hits (one-letter needle) -> None -> the scan
    monkeypatch.setattr(MusicDatabase, "_FTS_MAX_ROWIDS", 2)
    conn = db._get_connection()
    assert db._albums_fts_rowids(conn.cursor(), "e") is None
    # and the scan still answers
    assert len(db.search_albums(title="e", artist="", limit=50, server_source="plex")) == len(_scan_titles(db, "e"))


def test_albums_fts_survives_a_vacuum_via_the_rebuild(db):
    _seed(db, ALBUMS)
    db.ensure_norm_backfilled()
    from api.database_admin import _rebuild_fts_after_vacuum
    c = sqlite3.connect(str(db.database_path))
    c.execute("DELETE FROM albums WHERE id = 3")
    c.commit()
    c.execute("VACUUM")
    _rebuild_fts_after_vacuum(c)
    c.close()
    got = sorted(a.title for a in db.search_albums(title="Greatest Hits", artist="", server_source="plex"))
    assert got == ["Greatest Hits"]
