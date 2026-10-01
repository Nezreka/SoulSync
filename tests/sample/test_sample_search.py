"""Issue 2: Sample Studio library search was slow and missed obvious titles.

Root cause: ``search_library_tracks`` ran the download matcher's
artist-then-title double cascade (``api_search_tracks`` twice, each with
basic + base-title + fuzzy passes — up to six full-table scans per
keystroke on a big library), and a failed search collapsed to ``[]`` so
the UI could not tell "search failed" from "no matches".

Covered here:
- An obvious owned title ("Not Like Us") comes back, case-insensitively.
- Multiword and artist queries work; exact title ranks before
  prefix/substring matches.
- Word-order differences still resolve via the single term-OR fallback.
- Empty query returns [] (the 400 for a missing q lives one layer up).
- The interactive search never kicks the norm backfill thread (boot owns
  it — a keystroke must not serialize behind a library-sized backfill).
- ``search_library_tracks`` end-to-end returns serialized tracks.
"""

import pytest

import api.sample as sample_api
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    database = MusicDatabase(str(tmp_path / "m.db"))
    database.ensure_norm_backfilled()
    conn = database._get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO artists (id, name, name_norm, name_key) VALUES (?,?,?,?)",
        ("ar1", "Kendrick Lamar", "kendrick lamar", "kendricklamar"),
    )
    cur.execute(
        "INSERT INTO artists (id, name, name_norm, name_key) VALUES (?,?,?,?)",
        ("ar2", "Some Band", "some band", "someband"),
    )
    cur.execute(
        "INSERT INTO albums (id, title, title_norm, artist_id) VALUES (?,?,?,?)",
        ("al1", "GNX", "gnx", "ar1"),
    )
    cur.execute(
        "INSERT INTO albums (id, title, title_norm, artist_id) VALUES (?,?,?,?)",
        ("al2", "Stuff", "stuff", "ar2"),
    )
    tracks = [
        ("t1", "al1", "ar1", "Not Like Us", "not like us"),
        ("t2", "al1", "ar1", "Not Like Us (Remix)", "not like us (remix)"),
        ("t3", "al2", "ar2", "Noteworthy", "noteworthy"),
        ("t4", "al2", "ar2", "Us Against Them", "us against them"),
    ]
    for tid, album, artist, title, norm in tracks:
        cur.execute(
            """INSERT INTO tracks (id, album_id, artist_id, title, title_norm,
               track_artist_norm, track_number, duration, file_path)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (tid, album, artist, title, norm, "", 1, 200000, f"/music/{tid}.flac"),
        )
    conn.commit()
    yield database
    conn.close()


def test_interactive_search_finds_owned_title(db):
    rows = db.search_tracks_interactive("not like us")
    titles = [r["title"] for r in rows]
    assert "Not Like Us" in titles


def test_interactive_search_case_insensitive(db):
    lower = db.search_tracks_interactive("not like us")
    upper = db.search_tracks_interactive("NOT LIKE US")
    mixed = db.search_tracks_interactive("Not Like Us")
    assert [r["id"] for r in lower] == [r["id"] for r in upper] == [r["id"] for r in mixed]


def test_interactive_search_exact_title_ranks_first(db):
    rows = db.search_tracks_interactive("not like us")
    assert rows[0]["title"] == "Not Like Us"


def test_interactive_search_artist_query(db):
    rows = db.search_tracks_interactive("kendrick")
    assert rows and all(r["artist_name"] == "Kendrick Lamar" for r in rows)


def test_interactive_search_multiword_substring(db):
    rows = db.search_tracks_interactive("against them")
    assert [r["title"] for r in rows] == ["Us Against Them"]


def test_interactive_search_word_order_fallback(db):
    # primary substring pass misses; the term-OR fallback still resolves it
    rows = db.search_tracks_interactive("us like not")
    assert "Not Like Us" in [r["title"] for r in rows]


def test_interactive_search_empty_query(db):
    assert db.search_tracks_interactive("") == []
    assert db.search_tracks_interactive("   ") == []


def test_interactive_search_never_kicks_backfill_thread(db, monkeypatch):
    kicked = []
    monkeypatch.setattr(
        db, "_kick_norm_backfill_thread", lambda: kicked.append(True)
    )
    db.search_tracks_interactive("not like us")
    db.search_tracks_interactive("zzz no such track zzz")
    assert kicked == []


def test_interactive_search_returns_full_row_shape(db):
    rows = db.search_tracks_interactive("not like us")
    row = rows[0]
    assert row["artist_name"] == "Kendrick Lamar"
    assert row["album_title"] == "GNX"
    assert row["file_path"] == "/music/t1.flac"


def test_search_library_tracks_end_to_end(db, monkeypatch):
    monkeypatch.setattr(sample_api, "get_database", lambda: db)
    tracks = sample_api.search_library_tracks(q="not like us")
    assert tracks and tracks[0]["title"] == "Not Like Us"
    assert tracks[0]["artist_name"] == "Kendrick Lamar"


def test_search_library_tracks_requires_query(db, monkeypatch):
    monkeypatch.setattr(sample_api, "get_database", lambda: db)
    with pytest.raises(sample_api.SampleHttpError):
        sample_api.search_library_tracks(q="")


def test_search_library_tracks_title_param(db, monkeypatch):
    monkeypatch.setattr(sample_api, "get_database", lambda: db)
    tracks = sample_api.search_library_tracks(title="not like us")
    assert tracks and tracks[0]["title"] == "Not Like Us"
