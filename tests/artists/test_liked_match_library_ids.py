"""The liked-artist harvest takes every id the library already has.

review 5 R5-C-3: the catalogue row comes back through ARTIST_IDS_SQL, which
names deezer, discogs and musicbrainz deezer_id / discogs_id / musicbrainz_id.
the harvest looked for *_artist_id, so those three were never taken and each
liked artist paid up to three fuzzy api searches instead.
"""
import sqlite3
from unittest.mock import MagicMock

import core.artists.liked_match as lm
from core.library2.importer import normalize_name
from core.library2.schema import ensure_library_v2_schema


def _no_client():
    raise RuntimeError("no client in this test")


def test_library_artist_ids_are_all_harvested(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.execute(
        "INSERT INTO lib2_artists(id,name,name_key,spotify_id,musicbrainz_id,external_ids) "
        "VALUES (1,'Björk',?,'sp-1','mb-1',?)",
        (normalize_name('Björk'), '{"deezer":"630","discogs":"31","itunes":"it-1"}'))
    conn.commit()
    db = MagicMock()
    db._get_connection.return_value = conn
    db.get_liked_artists_pending_match.return_value = [{'id': 9, 'artist_name': 'Björk'}]
    monkeypatch.setattr(lm, 'spotify_client', None)
    for name in ('_get_itunes_client', '_get_deezer_client', '_get_discogs_client',
                 '_get_musicbrainz_client'):
        monkeypatch.setattr(lm, name, _no_client, raising=False)
    monkeypatch.setattr(lm, '_backfill_liked_artist_images', lambda *a, **k: None)

    lm._match_liked_artists_to_all_sources(db, 1)

    all_ids = db.update_liked_artist_match.call_args.kwargs['all_ids']
    assert all_ids.get('spotify_artist_id') == 'sp-1'
    assert all_ids.get('itunes_artist_id') == 'it-1'
    assert all_ids.get('deezer_artist_id') == '630'
    assert all_ids.get('discogs_artist_id') == '31'
    assert all_ids.get('musicbrainz_artist_id') == 'mb-1'
