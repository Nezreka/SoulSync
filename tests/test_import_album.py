import core.import_album as import_album


class _FakeEngine:
    def normalize_string(self, text):
        return str(text or "").strip().lower()

    def similarity_score(self, left, right):
        return 1.0 if left == right else 0.0


def test_resolve_album_artist_context_uses_provider_genres(monkeypatch):
    class _FakeClient:
        def get_artist(self, artist_id):
            assert artist_id == "artist-1"
            return {"genres": ["rock", "indie"]}

    monkeypatch.setattr(import_album, "get_client_for_source", lambda source: _FakeClient() if source == "spotify" else None)

    context = import_album.resolve_album_artist_context(
        {
            "id": "album-1",
            "name": "Album One",
            "artist_id": "artist-1",
            "artists": [{"name": "Artist One", "id": "artist-1"}],
        },
        source="spotify",
    )

    assert context == {
        "id": "artist-1",
        "name": "Artist One",
        "genres": ["rock", "indie"],
        "source": "spotify",
    }


def test_build_album_import_context_is_neutral():
    context = import_album.build_album_import_context(
        {
            "id": "album-1",
            "name": "Album One",
            "artist": "Artist One",
            "artist_id": "artist-1",
            "artists": [{"name": "Artist One", "id": "artist-1"}],
            "release_date": "2024-01-01",
            "total_tracks": 12,
            "album_type": "album",
            "image_url": "https://img.example/album.jpg",
            "source": "deezer",
        },
        {
            "id": "track-1",
            "name": "Song One",
            "track_number": 3,
            "disc_number": 2,
            "duration_ms": 180000,
            "artists": [{"name": "Artist One"}],
            "uri": "deezer:track:track-1",
            "album": "Album One",
            "source": "deezer",
        },
        artist_context={
            "id": "artist-1",
            "name": "Artist One",
            "genres": ["rock"],
            "source": "deezer",
        },
        total_discs=2,
        source="deezer",
    )

    assert context["artist"]["name"] == "Artist One"
    assert context["artist"]["genres"] == ["rock"]
    assert context["album"]["name"] == "Album One"
    assert context["album"]["total_discs"] == 2
    assert context["track_info"]["name"] == "Song One"
    assert context["track_info"]["track_number"] == 3
    assert context["original_search_result"]["clean_title"] == "Song One"
    assert context["source"] == "deezer"
    assert "spotify_artist" not in context
    assert "spotify_album" not in context


def test_build_album_import_match_payload_uses_generic_track_keys(monkeypatch, tmp_path):
    staging_root = tmp_path / "Staging"
    staging_root.mkdir()
    (staging_root / "Song One.flac").write_text("fake")

    monkeypatch.setattr(import_album, "get_staging_path", lambda: str(staging_root))
    monkeypatch.setattr(import_album, "_get_matching_engine", lambda: _FakeEngine())
    monkeypatch.setattr(
        import_album,
        "read_staging_file_metadata",
        lambda file_path, filename=None: {
            "title": "Song One",
            "artist": "Artist One",
            "albumartist": "Artist One",
            "album": "Album One",
            "track_number": 1,
            "disc_number": 1,
        },
    )
    monkeypatch.setattr(
        import_album,
        "get_artist_album_tracks",
        lambda album_id, artist_name="", album_name="", source=None: {
            "success": True,
            "album": {
                "id": album_id,
                "name": "Album One",
                "artist": "Artist One",
                "artist_name": "Artist One",
                "artist_id": "artist-1",
                "artists": [{"name": "Artist One", "id": "artist-1"}],
                "release_date": "2024-01-01",
                "total_tracks": 1,
                "total_discs": 1,
                "album_type": "album",
                "image_url": "https://img.example/album.jpg",
                "images": [{"url": "https://img.example/album.jpg"}],
                "source": "spotify",
            },
            "tracks": [
                {
                    "id": "track-1",
                    "name": "Song One",
                    "track_number": 1,
                    "disc_number": 1,
                    "duration_ms": 180000,
                    "artists": [{"name": "Artist One"}],
                    "uri": "spotify:track:track-1",
                    "album": {
                        "id": album_id,
                        "name": "Album One",
                        "artist": "Artist One",
                    },
                    "source": "spotify",
                }
            ],
            "source": "spotify",
            "source_priority": ["spotify"],
            "resolved_album_id": album_id,
        },
    )

    result = import_album.build_album_import_match_payload(
        "album-1",
        album_name="Album One",
        album_artist="Artist One",
        source="spotify",
    )

    assert result["success"] is True
    assert result["album"]["artist"] == "Artist One"
    assert result["source"] == "spotify"
    assert result["matches"] == [
        {
            "track": {
                "id": "track-1",
                "name": "Song One",
                "track_number": 1,
                "disc_number": 1,
                "duration_ms": 180000,
                "artists": [{"name": "Artist One"}],
                "uri": "spotify:track:track-1",
                "album": {
                    "id": "album-1",
                    "name": "Album One",
                    "artist": "Artist One",
                },
                "source": "spotify",
            },
            "staging_file": {
                "filename": "Song One.flac",
                "full_path": str(staging_root / "Song One.flac"),
                "title": "Song One",
                "artist": "Artist One",
                "album": "Album One",
                "albumartist": "Artist One",
                "track_number": 1,
                "disc_number": 1,
            },
            "confidence": 1.0,
        }
    ]
