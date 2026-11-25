"""the player's genre and label pages read the album browse with ?genre= and
?label=. a release matches on its own genres, else its artist's; a label
matches the release's label, case aside."""

import json

from core.library2 import queries as Q
from core.library2.schema import ensure_library_v2_schema
from tests import lib2_seed


def _library(tmp_path):
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    ensure_library_v2_schema(conn)
    views = lib2_seed.album(conn, "Drake", "Views")
    lib2_seed.album(conn, "Drake", "Scorpion")
    blue = lib2_seed.album(conn, "Miles Davis", "Kind of Blue")
    conn.execute("UPDATE lib2_albums SET genres=?, label=? WHERE id=?",
                 (json.dumps(["Hip-Hop"]), "OVO Sound", views))
    conn.execute("UPDATE lib2_albums SET label=? WHERE id=?", ("Columbia", blue))
    miles = conn.execute("SELECT primary_artist_id FROM lib2_albums WHERE id=?", (blue,)).fetchone()[0]
    conn.execute("UPDATE lib2_artists SET genres=? WHERE id=?", (json.dumps(["Jazz", "Bebop"]), miles))
    conn.commit()
    return conn


def _titles(conn, **kw):
    albums, total = Q.list_albums(conn, **kw)
    return sorted(a["title"] for a in albums), total


def test_genre_matches_the_release_then_its_artist(tmp_path):
    conn = _library(tmp_path)
    assert _titles(conn, genre="hip-hop") == (["Views"], 1)
    # Kind of Blue has no genres of its own; Miles Davis does
    assert _titles(conn, genre="JAZZ") == (["Kind of Blue"], 1)
    assert _titles(conn, genre="polka") == ([], 0)


def test_label_matches_case_aside(tmp_path):
    conn = _library(tmp_path)
    assert _titles(conn, label="columbia") == (["Kind of Blue"], 1)
    assert _titles(conn, label="OVO SOUND") == (["Views"], 1)


def test_no_filter_is_everything(tmp_path):
    conn = _library(tmp_path)
    assert _titles(conn) == (["Kind of Blue", "Scorpion", "Views"], 3)
