"""Proves the StreamSession is a drop-in for the old stream_state dict.

web_server.py was swapped from a module-global ``dict`` + ``threading.Lock``
to ``StreamStateStore().get()`` (the default session) + that session's lock.
This exercises every access pattern the real call sites use, against the same
object web_server now binds, so the swap is verified without booting Flask.

The patterns mirrored here come verbatim from web_server.py:
  - /api/library/play:  with lock: state.update({... "is_library": True})
  - /api/stream/start:  with lock: state.update({"status": "loading", ...})
  - /api/stream/status: with lock: read state["status"], state["progress"], ...
  - /stream/audio:      with lock: if state["status"] != "ready" or not state["file_path"]
  - /api/stream/stop:   with lock: state.get("is_library", False); state.update({... reset})
  - prepare.py:         state.update(...) and state["status"] = "queued"
"""

from __future__ import annotations

from core.streaming.state import StreamStateStore


def _server_like_state():
    """Reproduce exactly what web_server.py now binds."""
    store = StreamStateStore()
    state = store.get()          # DEFAULT_SESSION
    lock = state.lock
    return state, lock


def test_library_play_pattern():
    state, lock = _server_like_state()
    with lock:
        state.update({
            "status": "ready",
            "progress": 100,
            "track_info": {"title": "T", "artist": "A", "album": "Al"},
            "file_path": "/Stream/x.flac",
            "is_library": True,
        })
    assert state["status"] == "ready"
    assert state["file_path"] == "/Stream/x.flac"
    assert state.get("is_library") is True


def test_stream_start_pattern():
    state, lock = _server_like_state()
    with lock:
        state.update({
            "status": "loading",
            "progress": 0,
            "track_info": {"title": "Song"},
            "file_path": None,
            "error_message": None,
        })
    assert state["status"] == "loading"


def test_stream_status_read_pattern():
    state, lock = _server_like_state()
    state.update({"status": "queued", "progress": 42,
                  "track_info": {"title": "Q"}, "error_message": None})
    with lock:
        payload = {
            "status": state["status"],
            "progress": state["progress"],
            "track_info": state["track_info"],
            "error_message": state["error_message"],
        }
    assert payload == {"status": "queued", "progress": 42,
                       "track_info": {"title": "Q"}, "error_message": None}


def test_stream_audio_guard_pattern():
    state, lock = _server_like_state()
    # Not ready → guard trips (would 404 in the route).
    with lock:
        not_ready = state["status"] != "ready" or not state["file_path"]
    assert not_ready is True

    state.update({"status": "ready", "file_path": "/Stream/y.flac"})
    with lock:
        not_ready = state["status"] != "ready" or not state["file_path"]
        path = state["file_path"]
    assert not_ready is False
    assert path == "/Stream/y.flac"


def test_stream_stop_pattern():
    state, lock = _server_like_state()
    state.update({"status": "ready", "file_path": "/x", "is_library": True})
    with lock:
        is_library = state.get("is_library", False)
    assert is_library is True
    with lock:
        state.update({
            "status": "stopped", "progress": 0, "track_info": None,
            "file_path": None, "error_message": None, "is_library": False,
        })
    assert state["status"] == "stopped"
    assert state.get("is_library") is False


def test_prepare_worker_inplace_mutation_pattern():
    state, _ = _server_like_state()
    # prepare.py mutates in place via both update() and [k]=
    state.update({"status": "loading", "progress": 0})
    state["status"] = "queued"
    state["progress"] = 10
    state["status"] = "loading"
    state["progress"] = 55
    assert state["status"] == "loading"
    assert state["progress"] == 55


def test_set_stream_state_replace_keeps_same_session_object():
    """web_server._set_stream_state now routes a reassignment through
    replace() so the store's default session stays the live object. Verify a
    'reassign' is reflected in the SAME session the store hands out."""
    store = StreamStateStore()
    state = store.get()
    # simulate _set_stream_state(value): state.replace(dict(value))
    state.replace({"status": "error", "error_message": "boom"})
    # The store still hands back the same object, now carrying the new values.
    assert store.get() is state
    assert store.get()["status"] == "error"
    assert store.get()["error_message"] == "boom"
