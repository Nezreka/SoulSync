"""#1064 (QT3496) — singles/EPs in a discography wishlist all filed as Albums.

Root cause: SpotipyFree — the no-auth Spotify metadata fallback most installs
ride — emits NO album_type anywhere (verified against the installed package),
and the converters turned that absence into a CONFIDENT 'album' default. Every
discography single/EP then stored album_type='album' → the wishlist's Singles
category showed 0 from the start, and files were named [Album].

2026-09-24 recurrence (CAL, Yellowcard discography): the #1064 derivation was
defeated by a NEW shape of the same lie — current SpotipyFree hardcodes
``album["album_type"] = "album"`` in formatAlbum() (verified against the
published spotipyFree package), so the filler now arrives AS an explicit type
key and _has_explicit_type_signal trusted it. Fix: a bare 'album' is never an
explicit signal — only distinguishing types ('single'/'ep'/'compilation') are
trusted outright; 'album' is verified against the real track count.

Fixes under test:
  * _build_album_tracks_payload derives the type from the REAL track count
    when the source raw carries no *distinguishing* type signal
  * Album.from_spotify_dict does the same instead of trusting bare 'album'
  * get_album_type_display no longer collapses an unknown-count 'ep' to
    [Single] (the individual-EP mislabel from the same report)
  * end-to-end: a filler-'album' 2-track release stored via the discography
    payload classifies as 'singles' in the wishlist
"""

from __future__ import annotations

import pytest

from core.imports.paths import get_album_type_display
from core.metadata.album_tracks import (
    _build_album_tracks_payload,
    _has_explicit_type_signal,
    derive_album_type_from_count,
)
from core.metadata.types import Album


def _free_album_raw(n_tracks, **extra):
    """A SpotipyFree-shaped album: formatAlbum() hardcodes album_type='album'
    on every release (verified against the published spotipyFree package) and
    sets total_tracks from the returned items."""
    raw = {
        'id': 'alb1', 'name': 'Cool Release',
        'uri': 'spotify:album:alb1',
        'artists': [{'id': 'ar1', 'name': 'QT Artist'}],
        'release_date': '2024-05-01',
        'album_type': 'album',  # the filler — NOT a real classification
        'total_tracks': n_tracks,
        'tracks': {'items': [
            {'id': f't{i}', 'name': f'Track {i}', 'track_number': i,
             'artists': [{'name': 'QT Artist'}]}
            for i in range(1, n_tracks + 1)]},
    }
    raw.update(extra)
    return raw


# ── the derivation helpers ───────────────────────────────────────────────────

def test_derive_from_count_bands():
    assert derive_album_type_from_count(1) == 'single'
    assert derive_album_type_from_count(3) == 'single'
    assert derive_album_type_from_count(4) == 'ep'
    assert derive_album_type_from_count(6) == 'ep'
    assert derive_album_type_from_count(7) == 'album'
    assert derive_album_type_from_count(0) == 'album'
    assert derive_album_type_from_count(None) == 'album'


def test_type_signal_detection():
    # distinguishing types are trusted outright …
    assert _has_explicit_type_signal({'album_type': 'single'}) is True
    assert _has_explicit_type_signal({'album_type': 'EP'}) is True
    assert _has_explicit_type_signal({'record_type': 'ep'}) is True
    assert _has_explicit_type_signal({'record_type': 'compile'}) is True
    # … but a bare 'album' is the universal filler, not a signal
    assert _has_explicit_type_signal({'album_type': 'album'}) is False
    assert _has_explicit_type_signal({'collectionType': 'Album'}) is False
    assert _has_explicit_type_signal({'name': 'X', 'album_type': ''}) is False
    assert _has_explicit_type_signal({'name': 'X'}) is False
    assert _has_explicit_type_signal(None) is False


# ── the payload builder chokepoint ───────────────────────────────────────────

def test_signalless_single_gets_derived_type():
    payload = _build_album_tracks_payload(_free_album_raw(2), None, 'spotify', 'alb1')
    assert payload['album']['album_type'] == 'single'
    assert payload['album']['total_tracks'] == 2


def test_signalless_ep_gets_derived_type():
    payload = _build_album_tracks_payload(_free_album_raw(5), None, 'spotify', 'alb1')
    assert payload['album']['album_type'] == 'ep'


def test_signalless_full_album_stays_album():
    payload = _build_album_tracks_payload(_free_album_raw(11), None, 'spotify', 'alb1')
    assert payload['album']['album_type'] == 'album'


def test_explicit_type_is_never_overridden():
    # distinguishing types are never reclassified, whatever the count says
    raw = _free_album_raw(8, album_type='single')     # source's word wins
    payload = _build_album_tracks_payload(raw, None, 'spotify', 'alb1')
    assert payload['album']['album_type'] == 'single'
    raw = _free_album_raw(2, album_type='ep')
    payload = _build_album_tracks_payload(raw, None, 'spotify', 'alb1')
    assert payload['album']['album_type'] == 'ep'
    raw = _free_album_raw(9, record_type='compile')
    payload = _build_album_tracks_payload(raw, None, 'deezer', 'alb1')
    assert payload['album']['album_type'] == 'compilation'


def test_bare_album_filler_is_verified_by_count():
    # SpotipyFree hardcodes album_type='album' on everything — a 2-track
    # "album" is a single, a 5-track one an EP, an 11-track one an album.
    payload = _build_album_tracks_payload(_free_album_raw(2), None, 'spotify', 'alb1')
    assert payload['album']['album_type'] == 'single'
    payload = _build_album_tracks_payload(_free_album_raw(5), None, 'spotify', 'alb1')
    assert payload['album']['album_type'] == 'ep'
    payload = _build_album_tracks_payload(_free_album_raw(11), None, 'spotify', 'alb1')
    assert payload['album']['album_type'] == 'album'


# ── converter backstop ───────────────────────────────────────────────────────

def test_spotify_converter_infers_when_absent():
    album = Album.from_spotify_dict({'id': 'a', 'name': 'N', 'artists': [],
                                     'total_tracks': 2})
    assert album.album_type == 'single'
    album = Album.from_spotify_dict({'id': 'a', 'name': 'N', 'artists': [],
                                     'total_tracks': 5})
    assert album.album_type == 'ep'
    # explicit distinguishing value untouched …
    album = Album.from_spotify_dict({'id': 'a', 'name': 'N', 'artists': [],
                                     'album_type': 'single', 'total_tracks': 8})
    assert album.album_type == 'single'
    # … but the SpotipyFree 'album' filler is verified against the count
    album = Album.from_spotify_dict({'id': 'a', 'name': 'N', 'artists': [],
                                     'album_type': 'album', 'total_tracks': 2})
    assert album.album_type == 'single'
    album = Album.from_spotify_dict({'id': 'a', 'name': 'N', 'artists': [],
                                     'album_type': 'album', 'total_tracks': 11})
    assert album.album_type == 'album'
    # unknown count stays album
    album = Album.from_spotify_dict({'id': 'a', 'name': 'N', 'artists': []})
    assert album.album_type == 'album'


# ── the display/naming fix (individual EP labeled [Single]) ──────────────────

def test_unknown_count_ep_stays_ep():
    assert get_album_type_display('ep', 0) == 'EP'
    assert get_album_type_display('ep', None) == 'EP'
    assert get_album_type_display('single', 0) == 'Single'
    # known counts keep the long-standing bands
    assert get_album_type_display('single', 5) == 'EP'
    assert get_album_type_display('ep', 2) == 'Single'
    assert get_album_type_display('ep', 9) == 'Album'
    assert get_album_type_display('album', 0) == 'Album'


# ── end-to-end: discography add → wishlist classification ────────────────────

def test_filler_album_single_path_end_to_end():
    # DB-free version of the CAL regression: filler-'album' 2-track single →
    # stored 'single' → wishlist 'singles' bucket → 'Single' display folder.
    # (The DB round-trip variant below covers the full add_to_wishlist path
    # where the cryptography dependency is installed.)
    from core.imports.paths import get_album_type_display
    from core.wishlist.classification import classify_wishlist_track

    payload = _build_album_tracks_payload(_free_album_raw(2), None, 'spotify', 'alb1')
    album = payload['album']
    assert album['album_type'] == 'single'
    row = {'spotify_data': {
        'id': 't1', 'name': 'Track 1', 'artists': [{'name': 'QT Artist'}],
        'album': {'id': album['id'], 'name': album['name'],
                  'album_type': album['album_type'],
                  'total_tracks': album['total_tracks']}}}
    assert classify_wishlist_track(row) == 'singles'
    assert get_album_type_display(album['album_type'], album['total_tracks']) == 'Single'


def test_filler_album_single_classifies_as_singles(tmp_path):
    # CAL 2026-09-24: a SpotipyFree-shaped 2-track single (hardcoded
    # album_type='album' filler) stored via the discography flow must land in
    # the wishlist's singles bucket, not albums.
    from database.music_database import MusicDatabase
    from core.wishlist.classification import classify_wishlist_track

    payload = _build_album_tracks_payload(_free_album_raw(2), None, 'spotify', 'alb1')
    album = payload['album']
    assert album['album_type'] == 'single'
    # the same track payload shape the discography endpoint stores
    track_data = {
        'id': 't1', 'name': 'Track 1', 'artists': [{'name': 'QT Artist'}],
        'album': {'id': album['id'], 'name': album['name'], 'artists': album['artists'],
                  'images': album.get('images') or [], 'album_type': album['album_type'],
                  'release_date': album['release_date'],
                  'total_tracks': album['total_tracks']},
        'duration_ms': 1000, 'track_number': 1, 'disc_number': 1,
    }
    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    assert db.add_to_wishlist(spotify_track_data=track_data,
                              failure_reason='Added via Download Discography',
                              source_type='discography', source_info='{}',
                              profile_id=1)
    rows = db.get_wishlist_tracks()
    assert classify_wishlist_track(rows[0]) == 'singles'
