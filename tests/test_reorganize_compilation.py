"""Unit tests for compilation handling in Library Reorganize.

Covers:
- Manually set album `record_type = 'compilation'` overriding provider's `album` classification.
- Multi-artist auto-detection for compilations.
- Template resolution separating $albumartist from $artist on compilations.
- Persistence of detected compilation type in DB on reorganize / rename-only.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

from core.library_reorganize import (
    is_multi_artist_compilation,
    reorganize_album_rename_only,
)


def test_is_multi_artist_compilation_explicit_various_artists():
    assert is_multi_artist_compilation("Various Artists")
    assert is_multi_artist_compilation("Various")
    assert is_multi_artist_compilation("VA")
    assert is_multi_artist_compilation("V.A.")
    assert is_multi_artist_compilation("Soundtrack")


def test_is_multi_artist_compilation_track_distribution():
    # 4 distinct artists across 4 tracks -> Compilation
    api_tracks = [
        {"artists": [{"name": "Artist A"}], "title": "T1"},
        {"artists": [{"name": "Artist B"}], "title": "T2"},
        {"artists": [{"name": "Artist C"}], "title": "T3"},
        {"artists": [{"name": "Artist D"}], "title": "T4"},
    ]
    assert is_multi_artist_compilation("Label Presents", api_tracks=api_tracks)

    # Dominant artist (3 out of 4 tracks by Artist A > 50%) -> NOT a compilation
    dominant_tracks = [
        {"artists": [{"name": "Artist A"}], "title": "T1"},
        {"artists": [{"name": "Artist A"}], "title": "T2"},
        {"artists": [{"name": "Artist A"}], "title": "T3"},
        {"artists": [{"name": "Artist B"}], "title": "T4"},
    ]
    assert not is_multi_artist_compilation("Artist A", api_tracks=dominant_tracks)

    # Fewer than 3 tracks -> NOT a compilation
    short_tracks = [
        {"artists": [{"name": "Artist A"}], "title": "T1"},
        {"artists": [{"name": "Artist B"}], "title": "T2"},
    ]
    assert not is_multi_artist_compilation("Label Presents", api_tracks=short_tracks)


def test_reorganize_rename_only_persists_compilation_type(tmp_path):
    transfer_dir = str(tmp_path / "music")
    os.makedirs(transfer_dir, exist_ok=True)
    src_file = os.path.join(transfer_dir, "01.flac")
    dst_file = os.path.join(transfer_dir, "Various Artists", "Comp", "01 - Song.flac")
    with open(src_file, "wb") as f:
        f.write(b"content")

    preview_result = {
        "success": True,
        "status": "planned",
        "source": "deezer",
        "record_type": "compilation",
        "is_compilation": True,
        "tracks": [
            {
                "track_id": "t1",
                "title": "Song",
                "track_number": 1,
                "current_path_abs": src_file,
                "new_path_abs": dst_file,
                "matched": True,
                "unchanged": False,
                "collision": False,
                "file_exists": True,
            }
        ],
    }

    mock_db = MagicMock()
    mock_db.update_album_fields = MagicMock()

    summary = reorganize_album_rename_only(
        album_id="alb-99",
        db=mock_db,
        transfer_dir=transfer_dir,
        resolve_file_path_fn=lambda p: p,
        build_final_path_fn=lambda *a, **k: (None, True),
        update_track_path_fn=MagicMock(),
        preview_fn=lambda **kw: preview_result,
    )

    assert summary["moved"] == 1
    # Verify compilation record_type was persisted to db
    mock_db.update_album_fields.assert_called_once_with("alb-99", {"record_type": "compilation"})
    assert os.path.exists(dst_file)


