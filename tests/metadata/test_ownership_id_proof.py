"""#1071 (QT3496) — cross-source ownership via enrichment id proof.

The re-release year gate (5BILLION's fix) hard-rejects a name match when the
card's year and the library album's year differ by >1 — but different sources
DATE THE SAME ALBUM differently (your file tags carry the edition you bought;
the viewing source may date the original). Ownership then appeared locked to
whichever source dates the album like your copy. The fix: when the card's id
equals the local album's stored enrichment id FOR THAT SOURCE, that's
identity proof — it beats the year gate and title drift, and can never
credit a sibling edition (a re-release card carries a different id).

All hermetic: temp DB, no network (id proof reads only local columns).
"""

from __future__ import annotations

import inspect
import os
import re
import tempfile
from pathlib import Path

import pytest

from core.metadata.completion import check_album_completion, check_single_completion
from database.music_database import MusicDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture()
def library(tmp_path):
    """A library owning the 2014 remaster edition of a 1989 album, enriched
    with the album's Deezer id."""
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        from tests.support.catalogue_seed import seed_album, seed_artist, seed_track

        artist = seed_artist(conn, server_id='AR1', name='The Cure', server_source='test')
        album = seed_album(conn, server_id='AL1', artist_id=artist, server_source='test',
                           title='Disintegration', year=2014, track_count=12)
        conn.execute("UPDATE lib2_albums SET external_ids=? WHERE id=?",
                     ('{"deezer": "DZ-9"}', album))
        for i in range(12):
            seed_track(conn, server_id=f'T{i}', title=f'Track {i}', album_id=album,
                       artist_id=artist, server_source='test', track_number=i + 1,
                       file_path=f'/m/t{i}.flac')
        conn.commit()
    candidates = db.get_candidate_albums_for_artist('The Cure', server_source='test')
    assert len(candidates) == 1
    return db, candidates


def _card(**kw):
    base = {'id': 'DZ-9', 'name': 'Disintegration', 'total_tracks': 12,
            'album_type': 'album', 'year': 1989}
    base.update(kw)
    return base


def test_id_proof_beats_the_year_gate(library):
    """QT's exact case: same album, cross-source edition dating — owned."""
    db, candidates = library
    r = check_album_completion(db, _card(), 'The Cure',
                               source_override='deezer', candidate_albums=candidates)
    assert r['status'] == 'completed'
    assert r['confidence'] == 1.0
    assert r['owned_tracks'] == 12


def test_id_proof_beats_title_drift(library):
    """A source titling the release differently still proves by id."""
    db, candidates = library
    r = check_album_completion(db, _card(name='Disintegration (Remastered 2014)'),
                               'The Cure', source_override='deezer',
                               candidate_albums=candidates)
    assert r['status'] == 'completed'


def test_rerelease_card_with_different_id_still_missing(library):
    """5BILLION's guard holds: a sibling edition has a DIFFERENT id — the
    year gate keeps rejecting it, id proof can never fire."""
    db, candidates = library
    r = check_album_completion(db, _card(id='DZ-2019-DELUXE', year=1989),
                               'The Cure', source_override='deezer',
                               candidate_albums=candidates)
    assert r['status'] == 'missing'
    assert r['confidence'] == 0.0


def test_year_gate_unchanged_without_enrichment_id(library):
    """The documented residual: no stored id for the viewing source AND a
    year conflict → still missing (canonical-pin territory)."""
    db, candidates = library
    r = check_album_completion(db, _card(id='SP-123'), 'The Cure',
                               source_override='spotify',
                               candidate_albums=candidates)
    assert r['status'] == 'missing'


def test_matching_year_never_needed_the_proof(library):
    """Same-year matching stays pure fuzzy — byte-identical to before."""
    db, candidates = library
    r = check_album_completion(db, _card(id='SP-123', year=2014), 'The Cure',
                               source_override='spotify',
                               candidate_albums=candidates)
    assert r['status'] == 'completed'


def test_wrong_source_id_space_never_matches(library):
    """A spotify card whose id happens to equal a DEEZER stored id is not
    proof — columns are consulted per the card's own source only."""
    db, candidates = library
    r = check_album_completion(db, _card(id='DZ-9', year=1989), 'The Cure',
                               source_override='spotify',
                               candidate_albums=candidates)
    assert r['status'] == 'missing'


def test_ep_branch_gets_the_same_rescue(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        from tests.support.catalogue_seed import seed_album, seed_artist, seed_track

        from tests.support.catalogue_seed import seed_album, seed_artist, seed_track

        artist = seed_artist(conn, server_id='AR1', name='Muse', server_source='test')
        album = seed_album(conn, server_id='AL2', artist_id=artist, server_source='test',
                           title='Hullabaloo EP', year=2002, track_count=4)
        conn.execute("UPDATE lib2_albums SET external_ids=? WHERE id=?",
                     ('{"itunes": "IT-55"}', album))
        for i in range(4):
            seed_track(conn, server_id=f'E{i}', title=f'Cut {i}', album_id=album,
                       artist_id=artist, server_source='test', track_number=i + 1,
                       file_path=f'/m/e{i}.flac')
        conn.commit()
    candidates = db.get_candidate_albums_for_artist('Muse', server_source='test')
    ep = {'id': 'IT-55', 'name': 'Hullabaloo EP', 'total_tracks': 4,
          'album_type': 'ep', 'year': 2002}
    r = check_single_completion(db, ep, 'Muse', source_override='itunes',
                                candidate_albums=candidates)
    assert r['status'] == 'completed'


def test_get_album_source_ids_shape(tmp_path):
    """The keys stay the legacy column names — that is the vocabulary the
    ownership proof speaks — while the values come out of the catalogue's
    promoted columns and its `external_ids` payload."""
    from tests.support.catalogue_seed import seed_album, seed_artist

    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        artist = seed_artist(conn, server_id='AR1', name='X', server_source='test')
        enriched = seed_album(conn, server_id='AL1', artist_id=artist, title='A',
                              server_source='test')
        conn.execute(
            "UPDATE lib2_albums SET spotify_id='S1', external_ids=? WHERE id=?",
            ('{"deezer": "D1"}', enriched))
        bare = seed_album(conn, server_id='AL2', artist_id=artist, title='B',
                          server_source='test')
        conn.commit()
    m = db.get_album_source_ids([enriched, bare])
    assert m[enriched]['deezer_id'] == 'D1' and m[enriched]['spotify_album_id'] == 'S1'
    assert bare not in m                  # no enrichment ids → omitted
    assert db.get_album_source_ids([]) == {}


# ── endpoint + frontend contract pins ───────────────────────────────────────

def test_library_stream_honors_per_item_source():
    import web_server
    src = inspect.getsource(web_server.library_completion_stream)
    assert "item.get('source')" in src
    assert "source_override=item_source" in src

