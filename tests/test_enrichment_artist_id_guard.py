"""Regression: the album/track-driven 'artist id correction' in every
enrichment worker that has it must NOT overwrite an artist's source id unless
the result's artist name actually matches.

This is the same Kendrick/Jorja bug fixed in the Deezer worker (see
tests/test_deezer_worker_artist_id_guard.py), proven here to be closed in the
three other workers that copy-pasted the pattern: AudioDB, Qobuz, Tidal.

A track our library credits to Jorja Smith that lives on Kendrick's curated
'Black Panther' album resolves to that album, whose primary artist is Kendrick.
Without a name check, each worker would 'correct' Jorja's source id to
Kendrick's — corrupting it (and sharing one id across unrelated artists).
"""

from __future__ import annotations

from core.audiodb_worker import AudioDBWorker
from core.qobuz_worker import QobuzWorker
from core.tidal_worker import TidalWorker


def _stub(cls, correct_attr):
    """Build a bare worker instance (no __init__/clients) wired to record
    corrections instead of writing to a db."""
    w = cls.__new__(cls)
    w.name_similarity_threshold = 0.80
    w._corrections = []
    setattr(w, correct_attr, lambda item, cid: w._corrections.append((item['id'], cid)))
    return w


def _item(artist_name, parent_id, id_key):
    return {'type': 'track', 'id': 1, 'name': 'Some Track',
            'artist': artist_name, id_key: parent_id}


# --------------------------------------------------------------------------
# AudioDB — _verify_artist_id(item, result_dict); name is result['strArtist'].
# --------------------------------------------------------------------------

def test_audiodb_skips_correction_on_name_mismatch():
    w = _stub(AudioDBWorker, '_correct_artist_audiodb_id')
    item = _item('Jorja Smith', '111', 'artist_audiodb_id')
    w._verify_artist_id(item, {'idArtist': '999', 'strArtist': 'Kendrick Lamar'})
    assert w._corrections == []


def test_audiodb_corrects_on_name_match():
    w = _stub(AudioDBWorker, '_correct_artist_audiodb_id')
    item = _item('Kendrick Lamar', '111', 'artist_audiodb_id')
    w._verify_artist_id(item, {'idArtist': '999', 'strArtist': 'Kendrick Lamar'})
    assert w._corrections == [(1, '999')]


# --------------------------------------------------------------------------
# Qobuz — _verify_artist_id(item, result_artist_id, result_artist_name).
# --------------------------------------------------------------------------

def test_qobuz_skips_correction_on_name_mismatch():
    w = _stub(QobuzWorker, '_correct_artist_qobuz_id')
    item = _item('Jorja Smith', '111', 'artist_qobuz_id')
    w._verify_artist_id(item, '999', 'Kendrick Lamar')
    assert w._corrections == []


def test_qobuz_corrects_on_name_match():
    w = _stub(QobuzWorker, '_correct_artist_qobuz_id')
    item = _item('Kendrick Lamar', '111', 'artist_qobuz_id')
    w._verify_artist_id(item, '999', 'Kendrick Lamar')
    assert w._corrections == [(1, '999')]


# --------------------------------------------------------------------------
# Tidal — _verify_artist_id(item, result_artist_id, result_artist_name).
# --------------------------------------------------------------------------

def test_tidal_skips_correction_on_name_mismatch():
    w = _stub(TidalWorker, '_correct_artist_tidal_id')
    item = _item('Jorja Smith', '111', 'artist_tidal_id')
    w._verify_artist_id(item, '999', 'Kendrick Lamar')
    assert w._corrections == []


def test_tidal_corrects_on_name_match():
    w = _stub(TidalWorker, '_correct_artist_tidal_id')
    item = _item('Kendrick Lamar', '111', 'artist_tidal_id')
    w._verify_artist_id(item, '999', 'Kendrick Lamar')
    assert w._corrections == [(1, '999')]


# --------------------------------------------------------------------------
# Shared: a missing result name preserves the old "trust the search" behavior
# (only the workers that pass an id+name — qobuz/tidal — exercise this path).
# --------------------------------------------------------------------------

def test_qobuz_missing_result_name_preserves_old_behavior():
    w = _stub(QobuzWorker, '_correct_artist_qobuz_id')
    item = _item('Kendrick Lamar', '111', 'artist_qobuz_id')
    w._verify_artist_id(item, '999', None)
    assert w._corrections == [(1, '999')]
