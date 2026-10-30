"""what the review of Unknown Artist imports guesses about each file"""

from core.library2.unmatched_review import list_unmatched, parse_file_name
from tests import lib2_seed


def test_the_file_name_gives_the_number_title_and_copy():
    assert parse_file_name(r"H:\Music\DONDA\DONDA - DONDA 2\15 - SUZY (1).flac") == {
        "folder": r"H:\Music\DONDA\DONDA - DONDA 2",
        "folder_name": "DONDA - DONDA 2",
        "track_number": 15,
        "title": "SUZY",
        "copy": True,
    }
    assert parse_file_name("/music/x/1-03 Track - Copy.flac")["title"] == "Track"
    assert parse_file_name("/music/x/1-03 Track - Copy.flac")["track_number"] == 3
    assert parse_file_name("/music/Kid A.flac")["track_number"] is None


def _seed(conn):
    for n, title in ((1, "True Love"), (2, "Broken Road"), (15, "Suzy")):
        lib2_seed.track(conn, "Kanye West", "Donda 2", title, track_number=n,
                        path=f"/music/Kanye West/Donda 2/{n:02d} - {title}.flac")
    lib2_seed.track(conn, "Unknown Artist", "Unknown Album", "Suzy (1)",
                    path="/music/Kanye West/Donda 2/15 - Suzy (1).flac")
    lib2_seed.track(conn, "Unknown Artist", "Unknown Album", "Tongue",
                    path="/music/06 - Tongue.flac")
    lib2_seed.track(conn, "Someone", "Elsewhere", "Other", path="/music/Other.flac")
    lib2_seed.track(conn, "Someone Else", "Another", "Thing", path="/music/Thing.flac")
    conn.commit()


def test_a_copy_beside_its_matched_original_is_a_duplicate(tmp_path):
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)
    _seed(conn)

    rows = {r["file_name"]: r for r in list_unmatched(conn)}

    suzy = rows["15 - Suzy (1).flac"]
    assert suzy["duplicate_of"]["title"] == "Suzy"
    assert suzy["duplicate_of"]["album_title"] == "Donda 2"
    assert (suzy["guess"]["album"], suzy["guess"]["artist"]) == ("Donda 2", "Kanye West")
    assert suzy["guess"]["query"] == "Suzy Kanye West Donda 2"


def test_a_mixed_folder_guesses_no_album(tmp_path):
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)
    _seed(conn)

    tongue = next(r for r in list_unmatched(conn) if r["file_name"] == "06 - Tongue.flac")

    assert tongue["duplicate_of"] is None
    assert tongue["guess"]["album"] is None
    assert tongue["guess"]["query"] == "Tongue"
