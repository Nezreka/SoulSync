"""Regression: Deezer enrichment must not overwrite an artist's deezer_id from a
collaboration/compilation track whose primary artist is someone else.

The Kendrick/Jorja bug: a track our library credits to Jorja Smith lives on
Kendrick Lamar's curated "Black Panther" album. The album/track search resolves
to that album, whose Deezer primary artist is Kendrick (id 525046). The old
``_verify_artist_id`` "corrected" Jorja's deezer_id to 525046 with no name
check — stamping one Deezer id across several unrelated artists, which later
broke the artist-detail page (it matched the wrong library artist by id).

The fix gates the correction on a name match between the result's primary
artist and our parent artist.
"""

from __future__ import annotations

from core.deezer_worker import DeezerWorker


def _worker():
    # Bypass __init__ — it wants real clients/db. We only exercise the pure
    # _verify_artist_id / _name_matches / _normalize_name logic.
    w = DeezerWorker.__new__(DeezerWorker)
    w.name_similarity_threshold = 0.80
    w._corrections = []
    w._correct_artist_deezer_id = lambda item, cid: w._corrections.append((item['id'], cid))
    return w


def _item(artist_name, parent_deezer_id):
    return {
        'type': 'track', 'id': 1, 'name': 'Some Track',
        'artist': artist_name, 'artist_deezer_id': parent_deezer_id,
    }


def test_no_correction_when_result_artist_name_differs():
    # Jorja Smith (deezer 999) but the track resolved to Kendrick's album
    # (Deezer artist 525046, 'Kendrick Lamar') → must NOT overwrite.
    w = _worker()
    w._verify_artist_id(_item('Jorja Smith', '999'), '525046', 'Kendrick Lamar')
    assert w._corrections == []


def test_correction_when_names_match():
    # Same artist, stale/wrong stored id → legitimate correction proceeds.
    w = _worker()
    w._verify_artist_id(_item('Kendrick Lamar', '111'), '525046', 'Kendrick Lamar')
    assert w._corrections == [(1, '525046')]


def test_name_match_tolerates_minor_variation():
    # Fuzzy match (feat. suffix / casing) still counts as the same artist.
    w = _worker()
    w._verify_artist_id(_item('Kendrick Lamar', '111'), '525046', 'KENDRICK LAMAR')
    assert w._corrections == [(1, '525046')]


def test_no_correction_when_ids_already_equal():
    w = _worker()
    w._verify_artist_id(_item('Whoever', '525046'), '525046', 'Anyone')
    assert w._corrections == []


def test_no_parent_id_is_noop():
    w = _worker()
    w._verify_artist_id(_item('X', None), '525046', 'Y')
    assert w._corrections == []


def test_missing_result_name_preserves_old_behavior():
    # No artist name on the result → can't name-check; keep the original
    # "trust the more specific album/track search" behavior.
    w = _worker()
    w._verify_artist_id(_item('Kendrick Lamar', '111'), '525046', None)
    assert w._corrections == [(1, '525046')]
