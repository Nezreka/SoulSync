"""deezer wrote explicit=0 whenever its record lacked explicit_lyrics (a
failed full fetch, a bare search hit). the writes only fill gaps, so that
'clean' kept a later source's 'explicit' out and the file was tagged clean.
3.6.1 wrote the flag only when deezer gave one."""

from __future__ import annotations

from core.deezer_worker import DeezerWorker


def _writes(monkeypatch):
    worker = DeezerWorker.__new__(DeezerWorker)
    seen = []
    monkeypatch.setattr(worker, "_write", lambda *a, **k: seen.append(k.get("backfill")))
    return worker, seen


def test_no_explicit_key_writes_no_explicit(monkeypatch):
    worker, seen = _writes(monkeypatch)
    worker._update_track(1, {"id": 5}, None)
    worker._update_album(2, {"id": 6}, None)
    assert all("explicit" not in backfill for backfill in seen)


def test_deezer_saying_so_still_writes_it(monkeypatch):
    worker, seen = _writes(monkeypatch)
    worker._update_track(1, {"id": 5, "explicit_lyrics": True}, None)
    worker._update_album(2, {"id": 6, "explicit_lyrics": False}, None)
    assert [b["explicit"] for b in seen] == [1, 0]
