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
- (Upstream's norm-backfill guard is moot here: Library v2 keeps no norm
  columns.)
- ``search_library_tracks`` end-to-end returns serialized tracks.
"""

import pytest

import api.sample as sample_api
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    # Library v2 catalogue; the search folds case and accents on the fly
    # (unidecode_lower), no stored norm columns
    from tests.lib2_seed import track

    database = MusicDatabase(str(tmp_path / "m.db"))
    conn = database._get_connection()
    for artist, album, title in (
        ("Kendrick Lamar", "GNX", "Not Like Us"),
        ("Kendrick Lamar", "GNX", "Not Like Us (Remix)"),
        ("Some Band", "Stuff", "Noteworthy"),
        ("Some Band", "Stuff", "Us Against Them"),
    ):
        track(conn, artist, album, title, path=f"/music/{title}.flac",
              track_number=1, duration=200000)
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


def test_interactive_search_returns_full_row_shape(db):
    rows = db.search_tracks_interactive("not like us")
    row = rows[0]
    assert row["artist_name"] == "Kendrick Lamar"
    assert row["album_title"] == "GNX"
    assert row["file_path"] == "/music/Not Like Us.flac"


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
