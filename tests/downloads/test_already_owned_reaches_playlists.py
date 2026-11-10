"""a track the cross-batch dedup stands down ('already_owned': another
batch fetched this exact recording) is still a track of this playlist. the
playlist folder, its M3U and the post-download sync only looked at
'completed', so with a wishlist batch and an album batch sharing a song the
song never reached the playlist."""

from __future__ import annotations

from types import SimpleNamespace

from core.playlists.materialize_service import collect_batch_real_paths


def test_the_playlist_folder_takes_an_already_owned_track(tmp_path):
    song = tmp_path / "song.flac"
    song.write_bytes(b"audio")
    batch = {"queue": ["t1", "t2"], "analysis_results": []}
    tasks = {
        "t1": {"status": "already_owned", "final_file_path": str(song)},
        "t2": {"status": "failed", "final_file_path": str(tmp_path / "nope.flac")},
    }
    cfg = SimpleNamespace(get=lambda *_a, **_k: None, get_active_media_server=lambda: None)

    assert collect_batch_real_paths(batch, tasks, config_manager=cfg) == [str(song)]
