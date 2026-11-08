"""a background lookup's match is labelled automatic, and never relabels a
match the user made by hand (third #1062 review P2B-6)."""

from __future__ import annotations

from core.enrichment.match_provenance import record_automatic_match, record_manual_match
from database.music_database import MusicDatabase


def _origin(conn):
    return conn.execute("SELECT origin, external_id FROM metadata_match_provenance").fetchone()


def test_an_automatic_match_is_labelled_so(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        record_automatic_match(conn, entity_type="lib2_album", entity_id=5, service="deezer",
                               external_id="dz-auto")
        assert tuple(_origin(conn)) == ("automatic", "dz-auto")


def test_it_never_relabels_the_users_own_match(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        record_manual_match(conn, entity_type="lib2_album", entity_id=5, service="deezer",
                            external_id="dz-mine")
        record_automatic_match(conn, entity_type="lib2_album", entity_id=5, service="deezer",
                               external_id="dz-mine")
        assert tuple(_origin(conn)) == ("manual", "dz-mine")
        # a different id found later is a new, automatic match
        record_automatic_match(conn, entity_type="lib2_album", entity_id=5, service="deezer",
                               external_id="dz-other")
        assert tuple(_origin(conn)) == ("automatic", "dz-other")
