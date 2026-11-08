"""an import on the soulsync media server looks its album up once per track,
inside the import transaction. (server_source, server_id), spotify_id and
the musicbrainz id had no usable index on lib2_albums, so each was a full
scan of a 72k-album table per downloaded track."""

from __future__ import annotations

import pytest

from core.library2.schema import ensure_library_v2_schema
from tests.lib2_seed import row_conn


@pytest.fixture
def conn(tmp_path):
    c = row_conn(str(tmp_path / "lib2.db"))
    ensure_library_v2_schema(c)
    yield c
    c.close()


def _plan(conn, sql, params):
    return " | ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))


@pytest.mark.parametrize("sql", [
    "SELECT id FROM lib2_albums WHERE server_source = 'soulsync' AND server_id = ?",
    "SELECT id FROM lib2_albums WHERE spotify_id = ?",
    "SELECT id FROM lib2_albums WHERE LOWER(musicbrainz_id) = LOWER(?)",
])
def test_the_import_album_lookups_use_an_index(conn, sql):
    plan = _plan(conn, sql, ("x",))
    assert "SCAN lib2_albums" not in plan, plan
