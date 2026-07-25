"""#1064 (QT3496) — singles/EPs in a discography wishlist all filed as Albums.

Root cause: SpotipyFree — the no-auth Spotify metadata fallback most installs
ride — emits NO album_type anywhere (verified against the installed package),
and the converters turned that absence into a CONFIDENT 'album' default. Every
discography single/EP then stored album_type='album' → the wishlist's Singles
category showed 0 from the start, and files were named [Album].

Fixes under test:
  * _build_album_tracks_payload derives the type from the REAL track count
    when (and only when) the source raw carried no type signal at all
  * Album.from_spotify_dict infers from total_tracks instead of fabricating
  * get_album_type_display no longer collapses an unknown-count 'ep' to
    [Single] (the individual-EP mislabel from the same report)
  * end-to-end: a signal-less 2-track release stored via the discography
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
    """A SpotipyFree-shaped album: spotipy-ish, NO album_type key anywhere."""
    return {
        'id': 'alb1', 'name': 'Cool Release',
        'artists': [{'id': 'ar1', 'name': 'QT Artist'}],
        'release_date': '2024-05-01',
        'tracks': {'items': [
            {'id': f't{i}', 'name': f'Track {i}', 'track_number': i,
             'artists': [{'name': 'QT Artist'}]}
            for i in range(1, n_tracks + 1)]},
        **extra,
    }


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
    assert _has_explicit_type_signal({'album_type': 'single'}) is True
    assert _has_explicit_type_signal({'record_type': 'ep'}) is True
    assert _has_explicit_type_signal({'collectionType': 'Album'}) is True
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
    # a REAL 5-track album (source said so) must not be reclassified as EP
    raw = _free_album_raw(5, album_type='album')
    payload = _build_album_tracks_payload(raw, None, 'spotify', 'alb1')
    assert payload['album']['album_type'] == 'album'
    raw = _free_album_raw(8, album_type='single')     # source's word wins too
    payload = _build_album_tracks_payload(raw, None, 'spotify', 'alb1')
    assert payload['album']['album_type'] == 'single'


# ── converter backstop ───────────────────────────────────────────────────────

def test_spotify_converter_infers_when_absent():
    album = Album.from_spotify_dict({'id': 'a', 'name': 'N', 'artists': [],
                                     'total_tracks': 2})
    assert album.album_type == 'single'
    album = Album.from_spotify_dict({'id': 'a', 'name': 'N', 'artists': [],
                                     'total_tracks': 5})
    assert album.album_type == 'ep'
    # explicit value untouched; unknown count stays album
    album = Album.from_spotify_dict({'id': 'a', 'name': 'N', 'artists': [],
                                     'album_type': 'album', 'total_tracks': 2})
    assert album.album_type == 'album'
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

def test_signalless_single_classifies_as_singles(tmp_path):
    from database.music_database import MusicDatabase
    from core.wishlist.classification import classify_wishlist_track

    payload = _build_album_tracks_payload(_free_album_raw(2), None, 'spotify', 'alb1')
    album = payload['album']
    # the same track payload shape the discography endpoint stores (#1064 flow)
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
