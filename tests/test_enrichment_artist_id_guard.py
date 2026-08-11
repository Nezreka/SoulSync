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
# Shared: a MISSING result name means no verification is possible, so no
# correction happens — fail closed, the Deezer #988 semantics. The previous
# pin here ("trust the search" when the name is absent) was the corruption
# vector itself: the Tidal client builds album/track artist stubs with an id
# and NO name at all, so under the old rule every collaboration/compilation
# unconditionally rewrote the parent artist's tidal_id.
# --------------------------------------------------------------------------

def test_qobuz_missing_result_name_never_corrects():
    w = _stub(QobuzWorker, '_correct_artist_qobuz_id')
    item = _item('Kendrick Lamar', '111', 'artist_qobuz_id')
    w._verify_artist_id(item, '999', None)
    assert w._corrections == []


def test_tidal_missing_result_name_never_corrects():
    # Not an edge case for Tidal — it's the ONLY case its client produces.
    w = _stub(TidalWorker, '_correct_artist_tidal_id')
    item = _item('Kendrick Lamar', '111', 'artist_tidal_id')
    w._verify_artist_id(item, '999', None)
    assert w._corrections == []


def test_audiodb_missing_result_name_never_corrects():
    w = _stub(AudioDBWorker, '_correct_artist_audiodb_id')
    item = _item('Kendrick Lamar', '111', 'artist_audiodb_id')
    w._verify_artist_id(item, {'idArtist': '999', 'strArtist': ''})
    assert w._corrections == []
