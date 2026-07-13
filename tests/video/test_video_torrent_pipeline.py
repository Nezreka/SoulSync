"""Torrent/usenet video download pipeline — the pure logic:
- process_client_download: maps a torrent/usenet client status → the monitor patch shape.
- _default_search (hybrid): tries sources in order, first with an ACCEPTED release wins.
"""

from __future__ import annotations

import core.automation.handlers.video_process_wishlist as w
import core.video.client_download as cd


class _St:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ── process_client_download (pure; all I/O injected) ──────────────────────────
def _proc(dl, status, *, find="/local/movie.mkv", organizer=None):
    return cd.process_client_download(
        dl, get_status=lambda s, r: status, resolve_path=lambda p: "/local",
        find_video=lambda root, name=None: find, organizer=organizer)


def test_in_progress_reports_downloading_percent():
    upd = _proc({"client_ref": "h1", "source": "torrent"}, _St(state="downloading", progress=0.42))
    assert upd == {"status": "downloading", "progress": 42.0}


def test_error_state_fails():
    upd = _proc({"client_ref": "h1", "source": "torrent"}, _St(state="error", error="disk full"))
    assert upd["status"] == "failed" and "disk full" in upd["error"]


def test_no_ref_is_missing():
    upd = cd.process_client_download({"source": "torrent"}, get_status=lambda s, r: None,
                                     resolve_path=lambda p: p, find_video=lambda root, name=None: None)
    assert upd == {"_missing": True}


def test_find_video_is_scoped_to_this_jobs_content(tmp_path):
    """The cross-attribution guard: a shared download folder holds THIS job's small file plus a
    neighbour's much larger one. Scoping by the job name must pick OUR file, never the biggest."""
    shared = tmp_path / "soulsync"
    shared.mkdir()
    # our single-file torrent (small) — named after the torrent
    ours = shared / "Lego Masters AU S08E02 1080p.mkv"
    ours.write_bytes(b"x" * 1000)
    # a neighbour torrent's much larger file sitting in the SAME shared folder
    (shared / "Some Other Show S01E01 2160p.mkv").write_bytes(b"y" * 9000)

    scoped = cd.find_video_file(str(shared), "Lego Masters AU S08E02 1080p.mkv")
    assert scoped == str(ours)                       # ours, not the 9x-larger neighbour

    # a multi-file job: name is the folder; only its contents are searched
    folder = shared / "Lego Masters AU S08E03 1080p"
    folder.mkdir()
    (folder / "episode.mkv").write_bytes(b"z" * 500)
    assert cd.find_video_file(str(shared), "Lego Masters AU S08E03 1080p") == str(folder / "episode.mkv")

    # the job's content isn't on disk → None (never grab a neighbour's file)
    assert cd.find_video_file(str(shared), "Nonexistent Torrent Name") is None

    # no name (per-job usenet folder) → largest-in-root fallback still works
    assert cd.find_video_file(str(folder)) == str(folder / "episode.mkv")


def test_client_forgot_job_is_missing_when_unplaced():
    upd = _proc({"client_ref": "h1", "source": "usenet"}, None, find=None)
    assert upd == {"_missing": True}


def test_client_forgot_but_already_placed_completes():
    upd = _proc({"client_ref": "h1", "source": "usenet", "dest_path": "/lib/x.mkv"}, None, find=None)
    assert upd["status"] == "completed" and upd["dest_path"] == "/lib/x.mkv"


def test_completed_organizes_the_found_video():
    seen = {}

    def organizer(dl, src):
        seen["src"] = src
        return {"status": "completed", "progress": 100.0, "dest_path": "/lib/Movie/Movie.mkv"}

    upd = _proc({"client_ref": "h1", "source": "torrent", "id": 5},
                _St(state="seeding", progress=1.0, save_path="/dl/Movie"), organizer=organizer)
    assert seen["src"] == "/local/movie.mkv"                 # the resolved+found file was imported
    assert upd["dest_path"] == "/lib/Movie/Movie.mkv"


def test_completed_but_file_not_visible_yet_keeps_polling():
    upd = _proc({"client_ref": "h1", "source": "torrent"},
                _St(state="completed", progress=1.0, save_path="/dl/x"), find=None)
    assert upd == {"progress": 100.0}                        # no status → the monitor waits


# ── hybrid ordered-fallback in _default_search ────────────────────────────────
def _hybrid(monkeypatch, mode, order, per_source):
    monkeypatch.setattr("core.video.download_config.load",
                        lambda db: {"download_mode": mode, "hybrid_order": order})
    monkeypatch.setattr("api.video.get_video_db", lambda: object())
    calls = []

    def fake_one(src, item, mt):
        calls.append(src)
        return per_source.get(src, ([], None))

    monkeypatch.setattr(w, "_search_one_source", fake_one)
    return calls


def test_hybrid_first_source_with_an_accepted_release_wins(monkeypatch):
    calls = _hybrid(monkeypatch, "hybrid", ["soulseek", "torrent", "usenet"], {
        "soulseek": ([{"accepted": False, "title": "sd"}], None),   # hits, none good → fall on
        "torrent": ([{"accepted": True, "title": "tor"}], None),    # accepted → stop here
    })
    cands, err = w._default_search({"title": "X"}, "movie")
    assert calls == ["soulseek", "torrent"]                 # usenet never reached
    assert cands[0]["title"] == "tor" and err is None


def test_hybrid_falls_through_a_source_that_couldnt_run(monkeypatch):
    calls = _hybrid(monkeypatch, "hybrid", ["soulseek", "torrent"], {
        "soulseek": (None, "slskd offline"),                        # didn't run → skip
        "torrent": ([{"accepted": True, "title": "tor"}], None),
    })
    cands, err = w._default_search({"title": "X"}, "movie")
    assert calls == ["soulseek", "torrent"] and cands[0]["title"] == "tor"


def test_hybrid_none_accepted_returns_rejected_hits(monkeypatch):
    _hybrid(monkeypatch, "hybrid", ["soulseek", "torrent"], {
        "soulseek": ([{"accepted": False, "title": "a"}], None),
        "torrent": ([{"accepted": False, "title": "b"}], None),
    })
    cands, err = w._default_search({"title": "X"}, "movie")
    assert cands and not any(c["accepted"] for c in cands) and err is None    # → 'rejected'


def test_hybrid_all_sources_failed_to_run_returns_error(monkeypatch):
    _hybrid(monkeypatch, "hybrid", ["soulseek", "torrent"], {
        "soulseek": (None, "slskd offline"),
        "torrent": (None, "prowlarr offline"),
    })
    cands, err = w._default_search({"title": "X"}, "movie")
    assert cands is None and err                              # → 'search didn't run'


def test_single_mode_only_tries_that_source(monkeypatch):
    calls = _hybrid(monkeypatch, "torrent", ["soulseek", "torrent"], {
        "torrent": ([{"accepted": True, "title": "tor"}], None),
    })
    cands, err = w._default_search({"title": "X"}, "movie")
    assert calls == ["torrent"] and cands[0]["title"] == "tor"   # order ignored in single mode
