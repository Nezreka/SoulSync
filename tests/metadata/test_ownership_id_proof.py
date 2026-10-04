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
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('AR1', 'The Cure', 'test')")
        conn.execute(
            "INSERT INTO albums (id, artist_id, title, year, track_count, server_source, deezer_id) "
            "VALUES ('AL1', 'AR1', 'Disintegration', 2014, 12, 'test', 'DZ-9')")
        for i in range(12):
            conn.execute(
                "INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path, server_source) "
                "VALUES (?, 'AL1', 'AR1', ?, ?, ?, 'test')",
                (f'T{i}', f'Track {i}', i + 1, f'/m/t{i}.flac'))
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
    """#1289 tighter ID-conflict rule: the library candidate carries stored
    deezer_id 'DZ-9' but the Deezer card is 'DZ-2019-DELUXE' — hard proof of
    different releases (owned standard vs deluxe card), so the reissue-date
    exemption does not fire and the year gate still vetoes (1989 vs 2014).
    Id proof can't rescue either: the card's id matches no stored id."""
    db, candidates = library
    r = check_album_completion(db, _card(id='DZ-2019-DELUXE', year=1989),
                               'The Cure', source_override='deezer',
                               candidate_albums=candidates)
    assert r['status'] == 'missing'


def test_deezer_card_with_matching_id_exempt_no_conflict(library):
    """#1289 tighter rule, no-conflict branch: the card's Deezer id equals the
    candidate's stored id, so there is no ID conflict and the single-candidate
    exemption fires — owned. (Id proof would rescue this too; this pins the
    helper's equal-id branch.)"""
    db, candidates = library
    r = check_album_completion(db, _card(id='DZ-9', year=1989),
                               'The Cure', source_override='deezer',
                               candidate_albums=candidates)
    assert r['status'] == 'completed'


def test_single_completion_deezer_exemption_fires(library):
    """#1289 consistency: the singles path threads metadata_source too, so a
    Deezer single card with one exact-title candidate and a year conflict is
    owned. The card carries no id, so id proof cannot rescue — the exemption
    is the only path to owned, which pins the threading."""
    db, candidates = library
    single = {'name': 'Disintegration', 'total_tracks': 12,
              'album_type': 'single', 'year': 1989}
    r = check_single_completion(db, single, 'The Cure',
                                source_override='deezer',
                                candidate_albums=candidates)
    assert r['status'] == 'completed'


def test_single_completion_spotify_gate_holds(library):
    """#1289 consistency: a spotify single card keeps the strict gate — the
    same year conflict still reads missing."""
    db, candidates = library
    single = {'id': 'SP-123', 'name': 'Disintegration', 'total_tracks': 12,
              'album_type': 'single', 'year': 1989}
    r = check_single_completion(db, single, 'The Cure',
                                source_override='spotify',
                                candidate_albums=candidates)
    assert r['status'] == 'missing'


def test_single_completion_deezer_conflicting_id_still_missing(library):
    """#1289 tighter rule on the singles path: a Deezer card whose id
    conflicts with the candidate's stored id is a different release — the
    exemption does not fire, the gate vetoes."""
    db, candidates = library
    single = {'id': 'DZ-2019-DELUXE', 'name': 'Disintegration',
              'total_tracks': 12, 'album_type': 'single', 'year': 1989}
    r = check_single_completion(db, single, 'The Cure',
                                source_override='deezer',
                                candidate_albums=candidates)
    assert r['status'] == 'missing'


def test_year_gate_unchanged_without_enrichment_id(library):
    """The documented residual: no stored id for the viewing source AND a
    year conflict → still missing (canonical-pin territory).
    (#1289's exemption is Deezer-scoped; this is a spotify card, so the
    gate holds exactly as before.)"""
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
    proof — columns are consulted per the card's own source only.
    (#1289's exemption is Deezer-scoped; this is a spotify card, so the
    gate holds exactly as before.)"""
    db, candidates = library
    r = check_album_completion(db, _card(id='DZ-9', year=1989), 'The Cure',
                               source_override='spotify',
                               candidate_albums=candidates)
    assert r['status'] == 'missing'


def test_ep_branch_gets_the_same_rescue(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('AR1', 'Muse', 'test')")
        conn.execute(
            "INSERT INTO albums (id, artist_id, title, year, track_count, server_source, itunes_album_id) "
            "VALUES ('AL2', 'AR1', 'Hullabaloo EP', 2012, 4, 'test', 'IT-55')")
        for i in range(4):
            conn.execute(
                "INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path, server_source) "
                "VALUES (?, 'AL2', 'AR1', ?, ?, ?, 'test')",
                (f'E{i}', f'Cut {i}', i + 1, f'/m/e{i}.flac'))
        conn.commit()
    candidates = db.get_candidate_albums_for_artist('Muse', server_source='test')
    ep = {'id': 'IT-55', 'name': 'Hullabaloo EP', 'total_tracks': 4,
          'album_type': 'ep', 'year': 2002}
    r = check_single_completion(db, ep, 'Muse', source_override='itunes',
                                candidate_albums=candidates)
    assert r['status'] == 'completed'


def test_get_album_source_ids_shape(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('AR1', 'X', 'test')")
        conn.execute(
            "INSERT INTO albums (id, artist_id, title, server_source, deezer_id, spotify_album_id) "
            "VALUES ('AL1', 'AR1', 'A', 'test', 'D1', 'S1')")
        conn.execute(
            "INSERT INTO albums (id, artist_id, title, server_source) VALUES ('AL2', 'AR1', 'B', 'test')")
        conn.commit()
    m = db.get_album_source_ids(['AL1', 'AL2'])
    assert m['AL1']['deezer_id'] == 'D1' and m['AL1']['spotify_album_id'] == 'S1'
    assert 'AL2' not in m                 # no enrichment ids → omitted
    assert db.get_album_source_ids([]) == {}


# ── endpoint + frontend contract pins ───────────────────────────────────────

def test_library_stream_honors_per_item_source():
    import web_server
    src = inspect.getsource(web_server.library_completion_stream)
    assert "item.get('source')" in src
    assert "source_override=item_source" in src


def test_gap_cards_ride_the_ownership_stream():
    """The frontend half of the per-item source contract above.

    #1071: a gap card's id is only meaningful on the source that listed it, so
    the completion-stream payload has to carry a source PER ITEM rather than one
    source for the request. The endpoint honours item['source'] (asserted
    above); this pins that the client actually sends it.

    Reads the React page -- artist detail moved off library.js, and the vanilla
    _streamGapOwnership this used to read was deleted with it.
    """
    react = _ROOT / "webui" / "src" / "routes" / "artist-detail"
    payload = (react / "-artist-detail.gap-fill.ts").read_text(encoding="utf-8")
    body = payload[payload.index("export function gapStreamPayload"):]
    body = body[:body.index("\n}\n") + 3]
    assert "source: gap._gap_source || null" in body     # per-item source travels
    assert "source: null" in body                        # request-level source does NOT

    stream = (react / "-artist-detail.use-gap-fill.ts").read_text(encoding="utf-8")
    assert "'/api/library/completion-stream'" in stream  # same endpoint as base cards
    assert "gapStreamPayload(artistName, gaps)" in stream
    # The vanilla guarded stale responses with a request sequence number; React
    # aborts the request itself when the artist changes.
    assert "controller.abort()" in stream
