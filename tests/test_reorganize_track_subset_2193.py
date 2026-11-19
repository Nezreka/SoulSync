"""#2193: a track moved into a missing slot is filed into its album's folder
by the reorganize queue, limited to that track. The rest of the album stays
where it is, and the album is not marked placed by a run that only moved one
of its files."""

from core.library_reorganize import reorganize_album_rename_only
from core.reorganize_queue import ReorganizeQueue
from core.reorganize_runner import _only_tracks


def _plan(tracks):
    def _preview(**_kw):
        return {"success": True, "status": "planned", "source": None, "tracks": tracks}
    return _preview


def _track(tmp_path, track_id, name):
    src = tmp_path / "old" / f"{name}.mp3"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(name.encode())
    new = tmp_path / "new" / f"{name} renamed.mp3"
    return src, new, {"track_id": track_id, "title": name, "matched": True, "unchanged": False,
                      "collision": False, "file_exists": True,
                      "current_path_abs": str(src), "new_path_abs": str(new)}


def test_only_the_named_track_moves(tmp_path, monkeypatch):
    moved_src, moved_new, moved = _track(tmp_path, 7, "Punkrocker")
    other_src, other_new, other = _track(tmp_path, 8, "Walking on Air")
    placed = []
    monkeypatch.setattr("core.library_reorganize.mark_album_placed",
                        lambda db, album_id, fingerprint=None: placed.append(album_id))
    updates = []
    summary = reorganize_album_rename_only(
        album_id="5", db=None, transfer_dir=str(tmp_path),
        resolve_file_path_fn=lambda p: p, build_final_path_fn=lambda *a, **k: (None, True),
        update_track_path_fn=lambda tid, path, **_k: updates.append((tid, path)),
        preview_fn=_only_tracks(_plan([moved, other]), [7]),
    )
    assert summary["moved"] == 1
    # moved, not copied
    assert moved_new.exists() and not moved_src.exists()
    # the rest of the album nobody asked about stays put
    assert other_src.exists() and not other_new.exists()
    assert updates == [(7, str(moved_new))]
    # one track moved says nothing about the album as a whole
    assert placed == []


def test_whole_album_run_still_marks_placed(tmp_path, monkeypatch):
    _src, _new, track = _track(tmp_path, 7, "Punkrocker")
    placed = []
    monkeypatch.setattr("core.library_reorganize.mark_album_placed",
                        lambda db, album_id, fingerprint=None: placed.append(album_id))
    reorganize_album_rename_only(
        album_id="5", db=None, transfer_dir=str(tmp_path),
        resolve_file_path_fn=lambda p: p, build_final_path_fn=lambda *a, **k: (None, True),
        update_track_path_fn=lambda *a, **k: None,
        preview_fn=_only_tracks(_plan([track]), None),
    )
    assert placed == ["5"]


def _queue():
    return ReorganizeQueue(runner=lambda item: {"status": "completed"})


def _enqueue(queue, **kw):
    return queue.enqueue(album_id="5", album_title="OST", artist_id="1", artist_name="A", **kw)


def test_queue_keeps_the_track_ids(monkeypatch):
    monkeypatch.setattr("core.reorganize_queue._library_for_new_item", lambda: None)
    queue = ReorganizeQueue()   # no runner: items stay queued
    result = _enqueue(queue, track_ids=[9, 7])
    assert result["queued"] is True
    item = queue._items[-1]
    assert item.track_ids == [7, 9]
    assert item.to_snapshot()["track_ids"] == [7, 9]


def test_a_queued_whole_album_covers_a_track(monkeypatch):
    monkeypatch.setattr("core.reorganize_queue._library_for_new_item", lambda: None)
    queue = ReorganizeQueue()
    assert _enqueue(queue)["queued"] is True
    assert _enqueue(queue, track_ids=[7])["reason"] == "already_queued"


def test_a_queued_track_does_not_cover_the_whole_album(monkeypatch):
    monkeypatch.setattr("core.reorganize_queue._library_for_new_item", lambda: None)
    queue = ReorganizeQueue()
    assert _enqueue(queue, track_ids=[7])["queued"] is True
    assert _enqueue(queue, track_ids=[8])["queued"] is True
    assert _enqueue(queue, track_ids=[7])["reason"] == "already_queued"
    assert _enqueue(queue)["queued"] is True


def test_restore_keeps_the_track_ids(monkeypatch):
    class _Store:
        def load_pending(self):
            return [{"queue_id": "q1", "album_id": "5", "album_title": "OST", "artist_id": "1",
                     "artist_name": "A", "track_ids": [7]}]

    queue = ReorganizeQueue(store=_Store())
    monkeypatch.setattr(queue, "_ensure_worker", lambda: None)
    assert queue.restore() == 1
    assert queue._items[0].track_ids == [7]
