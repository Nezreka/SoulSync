"""The playlist-explorer build-tree route must not leak a foreign profile's mirror.

Review-round-2 finding R2-02: every other request-facing mirror lookup was
routed through ``_owned_mirrored_playlist``, but ``playlist_explorer_build_tree``
still resolved the mirror by bare primary key. A POST carrying another profile's
pk returned that profile's playlist name and its complete track list.
"""

from __future__ import annotations

import json

import pytest

from core.playlists.explorer import PlaylistExplorerDeps, playlist_explorer_build_tree
from database.music_database import MusicDatabase


class _Request:
    def __init__(self, payload):
        self._payload = payload

    def get_json(self):
        return self._payload


def _jsonify(payload):
    return payload


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _deps(db, payload, *, acting_profile_id):
    return PlaylistExplorerDeps(
        request=_Request(payload),
        flask_response=lambda *a, **k: None,
        flask_jsonify=_jsonify,
        spotify_client=None,
        get_database=lambda: db,
        get_active_discovery_source=lambda: "spotify",
        get_metadata_fallback_client=lambda: None,
        get_metadata_fallback_source=lambda: "itunes",
        get_metadata_cache=lambda: None,
        get_current_profile_id=lambda: acting_profile_id,
    )


def test_foreign_mirror_is_reported_as_missing(db):
    pk = db.mirror_playlist(
        source="spotify",
        source_playlist_id="secret",
        name="Profile 2 Private Mix",
        tracks=[{"track_name": "Secret Song", "artist_name": "Secret Artist"}],
        profile_id=2,
    )

    body, status = playlist_explorer_build_tree(
        _deps(db, {"playlist_id": pk}, acting_profile_id=1)
    )

    assert status == 404
    assert body["success"] is False
    assert "Secret" not in json.dumps(body)


def test_own_mirror_still_resolves(db):
    pk = db.mirror_playlist(
        source="spotify",
        source_playlist_id="mine",
        name="My Mix",
        tracks=[{"track_name": "Song", "artist_name": "Artist"}],
        profile_id=2,
    )

    result = playlist_explorer_build_tree(
        _deps(db, {"playlist_id": pk}, acting_profile_id=2)
    )

    # Past the ownership gate the route goes on to build the stream; the only
    # thing this test asserts is that it did NOT short-circuit with a 404.
    assert not (isinstance(result, tuple) and result[1] == 404)
