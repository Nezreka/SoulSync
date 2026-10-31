"""what a file that imported without a match is, by its fingerprint"""

import pytest

from core.library2.unmatched_identify import identify_file
from tests import lib2_seed


@pytest.fixture
def conn(tmp_path):
    from core.library2.schema import ensure_library_v2_schema
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    ensure_library_v2_schema(conn)
    lib2_seed.track(conn, "Kanye West", "Donda 2", "True Love", track_number=1,
                    path=str(tmp_path / "Donda 2" / "01 - True Love.flac"))
    lib2_seed.track(conn, "Kanye West", "Donda 2", "Broken Road", track_number=2,
                    path=str(tmp_path / "Donda 2" / "02 - Broken Road.flac"))
    lib2_seed.track(conn, "Unknown Artist", "Unknown Album", "Suzy (1)",
                    path=str(tmp_path / "Donda 2" / "15 - Suzy (1).flac"))
    lib2_seed.track(conn, "Unknown Artist", "Unknown Album", "Tongue",
                    path=str(tmp_path / "06 - Tongue.flac"))
    lib2_seed.track(conn, "MØ", "Forever Neverland", "Blur",
                    path=str(tmp_path / "MØ" / "Blur.flac"))
    conn.commit()
    yield conn
    conn.close()


def _file_id(conn, name):
    return conn.execute(
        "SELECT id FROM lib2_track_files WHERE path LIKE ?", (f"%{name}",)).fetchone()[0]


def _row(track_id, title, artist, album, album_type="album", **extra):
    return {"source": "deezer", "track_id": track_id, "track_title": title,
            "artist_name": artist, "album_name": album, "album_type": album_type,
            "year": "2022", "total_tracks": 10, "image_url": "", "isrc": None, **extra}


def _identify(conn, name, *, lookup, rows, searched=None):
    def search(source, query, title, artist):
        if searched is not None:
            searched.append((query, title, artist))
        return rows
    return identify_file(conn, _file_id(conn, name), lookup=lambda path: lookup,
                         search=search, source="deezer", resolve=lambda p: p)


def test_the_fingerprint_and_the_folder_rank_the_album_first(conn):
    heard = {"status": "ok", "recordings": [
        {"title": "SUZY", "artist": "Kanye West", "score": 0.96, "duration": 200}]}
    searched = []
    result = _identify(conn, "15 - Suzy (1).flac", lookup=heard, searched=searched, rows=[
        _row("s1", "SUZY", "Kanye West", "SUZY", "single"),
        _row("a1", "SUZY", "Kanye West", "Donda 2", track_number=15),
        _row("x1", "Suzy Q", "Someone", "Other"),
    ])
    assert searched == [("SUZY Kanye West", "SUZY", "Kanye West")]
    assert result["status"] == "matched"
    assert result["method"] == "fingerprint"
    best = result["candidates"][0]
    assert best["album_name"] == "Donda 2"
    assert best["confidence"] >= 0.85
    assert "Fingerprint 96%" in best["reasons"]
    assert "Its folder is Donda 2" in best["reasons"]
    assert "Track 15" in best["reasons"]
    # the single is still offered, below
    assert [c["track_id"] for c in result["candidates"][:2]] == ["a1", "s1"]


def test_a_recording_the_library_has_is_a_duplicate(conn):
    heard = {"status": "ok", "recordings": [
        {"title": "Blur", "artist": "MØ", "score": 0.93}]}
    result = _identify(conn, "06 - Tongue.flac", lookup=heard,
                       rows=[_row("b1", "Blur", "MØ", "Forever Neverland")])
    assert result["status"] == "duplicate"
    assert result["already_have"]["album_title"] == "Forever Neverland"
    # filing it anyway stays possible
    assert result["candidates"][0]["track_id"] == "b1"


def test_without_a_fingerprint_it_says_so_and_drops_strangers(conn):
    result = _identify(conn, "06 - Tongue.flac",
                       lookup={"status": "unavailable", "error": "No AcoustID API key configured"},
                       rows=[_row("t1", "Tongue Tied", "Grouplove", "Never Trust a Happy Song")])
    assert result["method"] == "name"
    assert result["status"] == "no_match"
    assert result["candidates"] == []
    assert "Fingerprinting is off" in result["message"]


def test_a_name_match_without_a_fingerprint_is_never_strong(conn):
    result = _identify(conn, "06 - Tongue.flac", lookup={"status": "no_match"},
                       rows=[_row("t1", "Tongue", "MØ", "No Mythologies to Follow")])
    assert result["status"] == "matched"
    assert result["candidates"][0]["confidence"] < 0.85
    assert result["candidates"][0]["reasons"] == ["Title from the file name"]


def test_a_file_that_is_matched_now_is_not_identified(conn):
    blur = _file_id(conn, "Blur.flac")
    assert identify_file(conn, blur, lookup=lambda p: {}, search=lambda *a: [],
                         source="deezer", resolve=lambda p: p) is None


def test_a_missing_file_says_so(conn):
    result = identify_file(conn, _file_id(conn, "06 - Tongue.flac"),
                           lookup=lambda p: pytest.fail("nothing to fingerprint"),
                           search=lambda *a: [], source="deezer", resolve=lambda p: None)
    assert result["status"] == "missing"


def test_the_files_own_length_counts_against_a_shorter_release(conn, monkeypatch):
    # the row has no length; the file does (suzy: 4:26 against a shorter release)
    import core.library2.unmatched_identify as ui
    monkeypatch.setattr(ui, "_file_length", lambda path: 266.0)
    result = _identify(conn, "15 - Suzy (1).flac", lookup={"status": "no_match"}, rows=[
        _row("short", "Suzy", "Kanye West", "Donda 2", duration_ms=161000),
        _row("long", "Suzy", "Kanye West", "Donda 2 (Deluxe)", duration_ms=265000),
    ])
    by_id = {c["track_id"]: c for c in result["candidates"]}
    assert "Same length" in by_id["long"]["reasons"]
    assert "Length differs (4:26 vs 2:41)" in by_id["short"]["reasons"]
    assert by_id["short"]["confidence"] < 0.6

