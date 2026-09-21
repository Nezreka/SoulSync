"""Unit tests for compilation handling in Library Reorganize.

Covers:
- Manually set album `record_type = 'compilation'` overriding provider's `album` classification.
- Multi-artist auto-detection for compilations.
- Template resolution separating $albumartist from $artist on compilations.
- Persistence of detected compilation type in DB on reorganize / rename-only.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch
import pytest

from core.library_reorganize import (
    is_multi_artist_compilation,
    plan_album_reorganize,
    preview_album_reorganize,
    reorganize_album_rename_only,
    _build_post_process_context,
    _build_album_info,
)
from core.imports.paths import build_final_path_for_track


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


def test_plan_album_reorganize_respects_manual_compilation():
    album_data = {
        "id": "alb-1",
        "title": "MOVELT JUKE JAM 4",
        "artist_name": "Moveltraxx Presents",
        "record_type": "compilation",
    }
    tracks = [
        {"id": "t1", "title": "Burnin", "track_number": 1, "duration": 180000},
    ]

    api_album = {
        "id": "deezer-123",
        "title": "MOVELT JUKE JAM 4",
        "album_type": "album",  # Provider misclassifies as bare album!
        "total_tracks": 1,
    }
    api_tracks = [
        {
            "id": "d-t1",
            "title": "Burnin",
            "track_number": 1,
            "disc_number": 1,
            "artists": [{"name": "DJ Earl"}],
        }
    ]

    with patch("core.library_reorganize._resolve_source", return_value=("deezer", api_album, api_tracks)):
        plan = plan_album_reorganize(album_data, tracks, primary_source="deezer")

    assert plan["status"] == "planned"
    assert plan["record_type"] == "compilation"
    assert plan["is_compilation"] is True


def test_plan_album_reorganize_auto_detects_multi_artist():
    album_data = {
        "id": "alb-2",
        "title": "Summer Vibes 2024",
        "artist_name": "Ministry of Sound",
        "record_type": "album",  # Saved as album originally
    }
    tracks = [
        {"id": f"t{i}", "title": f"Song {i}", "track_number": i, "duration": 180000}
        for i in range(1, 5)
    ]

    api_album = {
        "id": "deezer-456",
        "title": "Summer Vibes 2024",
        "album_type": "album",
    }
    api_tracks = [
        {
            "id": f"d-t{i}",
            "title": f"Song {i}",
            "track_number": i,
            "disc_number": 1,
            "artists": [{"name": f"Producer {i}"}],
        }
        for i in range(1, 5)
    ]

    with patch("core.library_reorganize._resolve_source", return_value=("deezer", api_album, api_tracks)):
        plan = plan_album_reorganize(album_data, tracks, primary_source="deezer")

    assert plan["status"] == "planned"
    assert plan["record_type"] == "compilation"
    assert plan["is_compilation"] is True


def test_preview_album_reorganize_compilation_destination(tmp_path):
    transfer_dir = str(tmp_path / "music")
    os.makedirs(transfer_dir, exist_ok=True)

    album_data = {
        "id": "alb-1",
        "title": "MOVELT JUKE JAM 4",
        "artist_name": "Moveltraxx Presents",
        "record_type": "compilation",
        "year": "2023",
    }
    file_path = os.path.join(transfer_dir, "old_folder", "01. Burnin.flac")
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "wb") as f:
        f.write(b"mock audio")

    tracks = [
        {
            "id": "t1",
            "title": "Burnin",
            "track_number": 1,
            "file_path": file_path,
            "duration": 180000,
        },
    ]

    api_album = {
        "id": "deezer-123",
        "title": "MOVELT JUKE JAM 4",
        "album_type": "album",
        "release_date": "2023-05-12",
        "total_tracks": 1,
    }
    api_tracks = [
        {
            "id": "d-t1",
            "title": "Burnin",
            "track_number": 1,
            "disc_number": 1,
            "artists": [{"name": "DJ Earl"}],
        }
    ]

    # Configure custom compilation_path
    config = {
        "soulseek.transfer_path": transfer_dir,
        "file_organization.templates": {
            "album_path": "$artist/$album/$track - $title",
            "compilation_path": "Various Artists/$albumartist - $album/$track - $artist - $title",
        },
        "file_organization.detect_multi_artist_compilations": True,
    }

    class MockDB:
        def _get_connection(self):
            conn = MagicMock()
            cursor = MagicMock()
            cursor.fetchone.return_value = {**album_data, "artist_name": album_data["artist_name"]}
            cursor.fetchall.return_value = [{**t, "artist_name": "DJ Earl"} for t in tracks]
            conn.cursor.return_value = cursor
            return conn

    with patch("core.library_reorganize._resolve_source", return_value=("deezer", api_album, api_tracks)), \
         patch("core.imports.paths._get_config_manager") as mock_cfg:
        mock_cfg.return_value.get.side_effect = lambda k, default=None: config.get(k, default)

        preview = preview_album_reorganize(
            album_id="alb-1",
            db=MockDB(),
            transfer_dir=transfer_dir,
            resolve_file_path_fn=lambda p: p,
            build_final_path_fn=lambda ctx, sa, ai, ext, create_dirs=False: build_final_path_for_track(
                ctx, sa, ai, ext, create_dirs=create_dirs
            ),
            primary_source="deezer",
        )

    assert preview["success"] is True
    assert preview["record_type"] == "compilation"
    assert preview["is_compilation"] is True
    assert len(preview["tracks"]) == 1
    t0 = preview["tracks"][0]
    assert t0["matched"] is True

    # Check that the path used compilation_path and separated $albumartist from $artist
    norm_new = t0["new_path"].replace("\\", "/")
    assert norm_new.startswith("Various Artists/Moveltraxx Presents - MOVELT JUKE JAM 4/")
    assert "01 - DJ Earl - Burnin" in norm_new


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


def test_plan_album_reorganize_auto_detect_disabled():
    album_data = {
        "id": "alb-2",
        "title": "Summer Vibes 2024",
        "artist_name": "Ministry of Sound",
        "record_type": "album",
    }
    tracks = [
        {"id": f"t{i}", "title": f"Song {i}", "track_number": i, "duration": 180000}
        for i in range(1, 5)
    ]

    api_album = {
        "id": "deezer-456",
        "title": "Summer Vibes 2024",
        "album_type": "album",
    }
    api_tracks = [
        {
            "id": f"d-t{i}",
            "title": f"Song {i}",
            "track_number": i,
            "disc_number": 1,
            "artists": [{"name": f"Producer {i}"}],
        }
        for i in range(1, 5)
    ]

    with patch("core.library_reorganize._resolve_source", return_value=("deezer", api_album, api_tracks)), \
         patch("core.library_reorganize._detect_compilation_enabled", return_value=False):
        plan = plan_album_reorganize(album_data, tracks, primary_source="deezer")

    assert plan["status"] == "planned"
    assert plan["record_type"] == "album"
    assert plan["is_compilation"] is False


def test_reorganize_album_persists_detected_compilation(tmp_path):
    from core.library_reorganize import reorganize_album

    transfer_dir = str(tmp_path / "music")
    staging_root = str(tmp_path / "staging")
    os.makedirs(transfer_dir, exist_ok=True)
    os.makedirs(staging_root, exist_ok=True)

    for i in range(1, 5):
        fpath = os.path.join(transfer_dir, f"0{i}.flac")
        with open(fpath, "wb") as f:
            f.write(b"content")

    album_data = {
        "id": "alb-55",
        "title": "Summer Comp",
        "artist_name": "Ministry of Sound",
        "record_type": "album",  # currently 'album'
    }
    tracks = [
        {
            "id": f"t{i}",
            "title": f"Song {i}",
            "track_number": i,
            "duration": 180000,
            "file_path": os.path.join(transfer_dir, f"0{i}.flac"),
        }
        for i in range(1, 5)
    ]

    api_album = {
        "id": "deezer-999",
        "title": "Summer Comp",
        "album_type": "album",
        "total_tracks": 4,
    }
    api_tracks = [
        {
            "id": f"d-t{i}",
            "title": f"Song {i}",
            "track_number": i,
            "disc_number": 1,
            "artists": [{"name": f"Producer {i}"}],
        }
        for i in range(1, 5)
    ]

    mock_db = MagicMock()
    mock_db.update_album_fields = MagicMock()
    mock_db._get_connection.return_value.cursor.return_value.fetchone.return_value = {
        **album_data,
        "artist_name": album_data["artist_name"],
    }
    mock_db._get_connection.return_value.cursor.return_value.fetchall.return_value = [
        {**t, "artist_name": f"Producer {i+1}"} for i, t in enumerate(tracks)
    ]

    def mock_post_process(ctx_key, context, staging_file):
        out = os.path.join(transfer_dir, "done", os.path.basename(staging_file))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(b"processed")
        context["_final_processed_path"] = out

    with patch("core.library_reorganize._resolve_source", return_value=("deezer", api_album, api_tracks)):
        summary = reorganize_album(
            album_id="alb-55",
            db=mock_db,
            staging_root=staging_root,
            resolve_file_path_fn=lambda p: p,
            post_process_fn=mock_post_process,
            update_track_path_fn=MagicMock(),
            transfer_dir=transfer_dir,
        )

    assert summary["status"] == "completed"
    assert summary["moved"] == 4
    mock_db.update_album_fields.assert_called_once_with("alb-55", {"record_type": "compilation"})

