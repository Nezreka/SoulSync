"""the Albums view's provider filter, for a provider albums never carry.

the Artists view can filter by Genius (an artist and track provider). 3.6.1
left such a filter out on the album browse; v2 applied it and every album
"had no Genius id", so carrying the filter over emptied the grid.
"""

import json

from core.library2 import queries as Q
from core.library2.schema import ensure_library_v2_schema
from tests import lib2_seed


def _two_albums(tmp_path):
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    ensure_library_v2_schema(conn)
    with_id = lib2_seed.album(conn, "Drake", "Views")
    lib2_seed.album(conn, "Drake", "Scorpion")
    conn.execute("UPDATE lib2_albums SET external_ids=? WHERE id=?",
                 (json.dumps({"deezer": "dz-views"}), with_id))
    conn.commit()
    return conn


def _titles(conn, source):
    albums, total = Q.list_albums(conn, source_filter=source)
    return sorted(a["title"] for a in albums), total


def test_an_artist_only_provider_never_empties_the_album_grid(tmp_path):
    conn = _two_albums(tmp_path)
    assert _titles(conn, "genius") == (["Scorpion", "Views"], 2)
    assert _titles(conn, "!genius") == (["Scorpion", "Views"], 2)


def test_an_album_provider_still_filters(tmp_path):
    conn = _two_albums(tmp_path)
    assert _titles(conn, "deezer") == (["Views"], 1)
    assert _titles(conn, "!deezer") == (["Scorpion"], 1)
