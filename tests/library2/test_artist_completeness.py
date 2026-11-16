"""Each artist's collection completeness on the library list (#2106, #2107).

The table already showed "owned / total"; the cards, the sorts and the
filters now read the same numbers, and an artist is never called complete
when nothing is expected or a release's size was never looked up.
"""

import pytest

from core.library2 import artist_completeness as AC
from core.library2 import queries
from core.library2.schema import ensure_library_v2_schema
from core.library2.wanted_views import list_missing
from tests.lib2_seed import artist, row_conn, track


@pytest.fixture
def lib(tmp_path):
    path = str(tmp_path / "complete.db")
    conn = row_conn(path)
    ensure_library_v2_schema(conn)
    conn.execute("CREATE TABLE watchlist_artists(spotify_artist_id TEXT, itunes_artist_id TEXT,"
                 " artist_name TEXT, profile_id INTEGER)")
    AC.forget()
    ids = {}

    def song(name, album, title, owned=True, size=None, credit_also=None):
        tid = track(conn, name, album, title, owned=owned, credit=name,
                    monitored=0 if owned else 1,
                    album_cols={"expected_track_count": size} if size is not None else None)
        if not owned:
            conn.execute("INSERT INTO lib2_wanted_tracks(profile_id, track_id, wanted, reason,"
                         " projection_version) VALUES (1, ?, 1, 'track', 1)", (tid,))
        if credit_also:
            conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id) VALUES (?, ?)",
                         (tid, artist(conn, credit_also)))
        return tid

    # every track here, every release sized
    song("Done", "Finished", "d1", size=2)
    song("Done", "Finished", "d2", size=2, credit_also="Guest")
    # one of three is missing
    song("Gaps", "Holes", "g1", size=3)
    song("Gaps", "Holes", "g2", size=3)
    ids["gap_missing"] = song("Gaps", "Holes", "g3", owned=False, size=3)
    # wanted, nothing here yet
    song("Empty", "Wanted", "e1", owned=False, size=1)
    # all here, but nobody ever looked up how big the release is
    conn.execute("UPDATE lib2_albums SET expected_track_count=NULL WHERE title='Unsized'")
    song("Unsized", "Unsized", "u1")
    conn.execute("UPDATE lib2_albums SET expected_track_count=NULL WHERE title='Unsized'")
    # on the Watchlist, nothing expected at all
    ids["watched"] = artist(conn, "Watched", monitored=1)
    conn.commit()
    for name in ("Done", "Gaps", "Empty", "Unsized", "Guest"):
        ids[name] = artist(conn, name)
    yield conn, ids
    AC.forget()
    conn.close()


def _names(conn, **kw):
    rows, total = queries.list_artists(conn, **kw)
    assert total == len(rows)
    return [r["name"] for r in rows]


def test_each_row_says_how_complete_the_artist_is(lib):
    """#2106: the cards get the table's numbers, and an artist with nothing
    expected or an unsized release is never 'complete'."""
    conn, _ids = lib
    rows = {r["name"]: r for r in queries.list_artists(conn)[0]}
    assert rows["Done"]["completion"] == "complete"
    assert (rows["Gaps"]["tracks_present"], rows["Gaps"]["track_count"]) == (2, 3)
    assert rows["Gaps"]["completion"] == "incomplete"
    assert rows["Empty"]["completion"] == "incomplete"
    assert rows["Unsized"]["completion"] == "unchecked"
    assert rows["Unsized"]["unverified_albums"] == 1
    assert rows["Watched"]["completion"] == "unknown"


def test_completion_states_never_call_nothing_complete():
    assert AC.completion_state(0, 0) == "unknown"
    assert AC.completion_state(5, 5, unverified_albums=1) == "unchecked"
    assert AC.completion_state(5, 4) == "incomplete"
    assert AC.completion_state(5, 5) == "complete"
    assert AC.Completeness(total=0).percent is None


def test_completion_filters(lib):
    """#2107: complete, missing, nothing owned, catalogue not checked."""
    conn, _ids = lib

    def by(**kw):
        return _names(conn, completeness=AC.completeness_filters(**kw))

    assert by(completion="complete") == ["Done"]
    assert by(completion="incomplete") == ["Empty", "Gaps"]
    assert by(completion="none_owned") == ["Empty"]
    assert by(completion="unchecked") == ["Unsized", "Watched"]
    assert by(missing_min=1) == ["Empty", "Gaps"]
    assert by(missing_min=1, missing_max=0) == []
    # 2 of 3 is 66%
    assert by(pct_min=50, pct_max=99) == ["Gaps"]
    assert by(pct_max=0) == ["Empty"]
    # an open range filters nothing; junk is ignored
    assert AC.completeness_filters(pct_min=0, pct_max=None) == {}
    assert AC.completeness_filters(completion="bogus", missing_min="x") == {}


def test_completeness_sorts_both_ways(lib):
    """#2106/#2107: by completion and by missing, either way round; the
    unmeasured stay last."""
    conn, _ids = lib
    assert _names(conn, sort="completion") == ["Empty", "Gaps", "Done", "Unsized", "Watched"]
    assert _names(conn, sort="completion", direction="desc") == [
        "Done", "Unsized", "Gaps", "Empty", "Watched"]
    assert _names(conn, sort="missing")[:2] == ["Empty", "Gaps"]
    assert _names(conn, sort="missing", direction="asc")[:2] == ["Done", "Unsized"]
    assert _names(conn, sort="incomplete_albums")[:2] == ["Empty", "Gaps"]


def test_the_sort_pages_through_the_kept_order(lib):
    conn, _ids = lib
    first, total = queries.list_artists(conn, sort="completion", limit=2, page=1)
    second, _ = queries.list_artists(conn, sort="completion", limit=2, page=2)
    assert total == 5
    assert [r["name"] for r in first + second] == ["Empty", "Gaps", "Done", "Unsized"]
    # the projection keeps the numbers live
    assert {r["name"]: r["tracks_present"] for r in first} == {"Empty": 0, "Gaps": 2}


def test_any_sort_turns_around(lib):
    """#2107: every sort takes a direction."""
    conn, _ids = lib
    names = _names(conn, sort="name")
    assert _names(conn, sort="name", direction="desc") == list(reversed(names))
    assert _names(conn, sort="name", direction="asc") == names
    assert _names(conn, sort="tracks")[0] == "Gaps"
    assert _names(conn, sort="tracks", direction="asc")[-1] == "Gaps"


def test_featured_only_artists(lib):
    """#2107: release artists by default, the guests on their own on ask."""
    conn, _ids = lib
    assert "Guest" not in _names(conn)
    assert _names(conn, credit="featured") == ["Guest"]
    assert "Guest" in _names(conn, credit="all") and "Done" in _names(conn, credit="all")


def test_missing_view_takes_one_artist(lib):
    """#2106: the missing count opens that artist's missing tracks."""
    conn, ids = lib
    rows, total = list_missing(conn, artist_id=ids["Gaps"])
    assert total == 1 and [r["track_id"] for r in rows] == [ids["gap_missing"]]
    _rows, everyone = list_missing(conn)
    assert everyone == 2


def test_overview_counts(lib):
    from core.library2.library_overview import library_overview
    conn, _ids = lib
    conn.execute("INSERT INTO watchlist_artists(artist_name, profile_id) VALUES ('Done', 1)")
    out = library_overview(conn, profile_id=1)
    assert out["artists"] == 5
    assert out["owned_tracks"] == 5
    assert out["missing_tracks"] == 2
    assert (out["complete_artists"], out["incomplete_artists"], out["unchecked_artists"]) == (1, 2, 2)
    assert out["watchlist_artists"] == 1


def test_kept_answer_moves_with_the_library(lib, monkeypatch):
    """the kept answer is served until a write moves the member counter, then
    worked out again behind the page."""
    conn, ids = lib
    before = AC.artist_completeness(conn)
    assert before[ids["Gaps"]].present == 2
    calls = []
    monkeypatch.setattr(AC, "compute", lambda c, parts=None: calls.append(1) or AC.LibraryFacts({}, 0, 0))
    assert AC.artist_completeness(conn) is before
    assert calls == []
    conn.execute("INSERT INTO lib2_track_files(track_id, path, is_primary) VALUES (?, '/x.flac', 1)",
                 (ids["gap_missing"],))
    conn.commit()
    ran = []
    monkeypatch.setattr(AC, "_spawn", lambda target: ran.append(target))
    # the old answer still answers this request; a refresh is on its way
    assert AC.artist_completeness(conn) is before
    assert len(ran) == 1


def test_the_library_pass_stays_on_indexes(lib):
    """one pass over the tracks is the point; every join from it is a seek."""
    conn, _ids = lib
    parts = AC._scope_parts()
    for sql in (AC._TRACKS_SQL.format(**parts), AC._OWN_RELEASES_SQL.format(**parts)):
        plan = " | ".join(str(r[3]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql))
        for table in ("tf", "w", "m"):
            assert f"SCAN {table} " not in plan + " ", plan
    from core.library2.wanted_views import _ARTIST_TRACKS
    plan = " | ".join(str(r[3]) for r in conn.execute(
        "EXPLAIN QUERY PLAN " + _ARTIST_TRACKS, {"artist_id": 1}))
    assert "SCAN" not in plan, plan


def test_select_all_ids_follow_the_filters(lib):
    """#2109: "select all" takes every artist the list's filters match."""
    from core.library2.artist_reader import read_artist_page
    conn, ids = lib
    rows, total = read_artist_page(conn, ids_only=True,
                                   completeness=AC.completeness_filters(completion="incomplete"))
    assert [r["id"] for r in rows] == [ids["Empty"], ids["Gaps"]] and total == 2
    everyone, total = read_artist_page(conn, ids_only=True, sort="name", direction="desc")
    assert total == 5 and everyone[0]["id"] == ids["watched"]
