"""Tests for the spotify_public cross-source alias in the resolve endpoint.

Playlists saved via SoulSync's link-paste use the no-auth "spotify_public"
source, keyed on the md5 of the canonical playlist URL. The Companion
extension checks with the raw Spotify ID under source="spotify", so the
resolve endpoint translates via _resolve_spotify_public_alias.
"""
import hashlib

from database.music_database import MusicDatabase
from api.mirrored_playlists import _resolve_spotify_public_alias
from core.playlists.sources.base import SOURCE_SPOTIFY_PUBLIC

SPOTIFY_ID = "37i9dQZF1EIUZzBgsXXF8b"
CANONICAL = f"https://open.spotify.com/playlist/{SPOTIFY_ID}"
URL_HASH = hashlib.md5(CANONICAL.encode()).hexdigest()[:12]


def _mirror_public(db, profile_id=1):
    return db.mirror_playlist(
        source=SOURCE_SPOTIFY_PUBLIC,
        source_playlist_id=URL_HASH,
        name="The Notorious B.I.G. Mix",
        tracks=[{"track_name": "Song", "artist_name": "Artist"}],
        profile_id=profile_id,
    )


def test_alias_finds_link_pasted_playlist(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    mirror_id = _mirror_public(db)
    assert mirror_id is not None

    found = _resolve_spotify_public_alias(db, SPOTIFY_ID, 1)
    assert found is not None
    assert int(found["id"]) == int(mirror_id)
    assert found["source"] == SOURCE_SPOTIFY_PUBLIC


def test_alias_is_profile_scoped(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    _mirror_public(db, profile_id=1)
    # Profile 2 must not see profile 1's mirror.
    assert _resolve_spotify_public_alias(db, SPOTIFY_ID, 2) is None


def test_alias_ignores_non_id_refs(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    _mirror_public(db)
    assert _resolve_spotify_public_alias(db, "not-a-spotify-id", 1) is None
    assert _resolve_spotify_public_alias(db, "", 1) is None
    assert _resolve_spotify_public_alias(db, None, 1) is None
    # Right length, wrong alphabet (has dashes/underscores).
    assert _resolve_spotify_public_alias(db, "37i9dQZF1EIUZzBgsXXF8-", 1) is None


def test_alias_misses_when_no_public_mirror(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    # A mirror under source="spotify" with the raw ID is NOT the alias's job
    # (the primary resolve handles it) — the alias only looks at spotify_public.
    db.mirror_playlist(
        source="spotify",
        source_playlist_id=SPOTIFY_ID,
        name="Direct",
        tracks=[{"track_name": "Song", "artist_name": "Artist"}],
        profile_id=1,
    )
    assert _resolve_spotify_public_alias(db, SPOTIFY_ID, 1) is None
