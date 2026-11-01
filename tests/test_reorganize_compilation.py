"""Compilation handling in Library Reorganize: a rename-only run that moves a
compilation's files records the album's compilation type in the catalogue.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

from core.library_reorganize import (
    reorganize_album_rename_only,
)


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


