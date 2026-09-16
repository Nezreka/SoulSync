"""Downloads that bypass the batch system still belong on the Downloads page.

Two paths ran invisibly. A music video is fetched by its own thread into its own
private dict with its own status endpoint; a basic-search grab is handed to the
orchestrator and the route returns. Both work. Neither ever wrote a row into the
shared ``download_tasks`` registry that /api/downloads/all is built from, so the
page whose entire job is showing downloads showed nothing.

The isolation contract is the one podcasts and audiobooks already use:
``is_music_batch()`` returns False for a batch flagged ``is_music: False`` or
``managed_externally: True``, and the music worker pool, the batch healer and
the wishlist failure processor all consult it. These downloads are already being
run and filed by their own code — the row is for visibility only, and the music
engine must not adopt one and run it a second time.
"""

from pathlib import Path

import pytest

from core import direct_download_state as dds
from core.runtime_state import download_batches, download_tasks

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_state():
    """These are process-global dicts; never leave rows behind for other tests."""
    for d in (download_tasks, download_batches):
        d.clear()
    yield
    for d in (download_tasks, download_batches):
        d.clear()


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


def test_a_registered_download_appears_as_a_task():
    assert dds.register(dds.MUSIC_VIDEO_BATCH, "vid1", title="Freak On A Leash",
                        artist="Korn") is True
    assert "vid1" in download_tasks
    assert download_tasks["vid1"]["track_info"]["title"] == "Freak On A Leash"
    assert download_tasks["vid1"]["status"] == "downloading"


def test_the_batch_is_flagged_so_the_music_engine_skips_it():
    """The whole isolation contract. Without these the music worker pool would
    adopt a music video and try to run it as a music download."""
    dds.register(dds.MUSIC_VIDEO_BATCH, "vid1", title="A")
    batch = download_batches[dds.MUSIC_VIDEO_BATCH]
    assert batch["is_music"] is False
    assert batch["managed_externally"] is True


def test_the_real_guard_agrees():
    """Not a re-implementation of is_music_batch here — the actual function."""
    from core.downloads.lifecycle import is_music_batch

    dds.register(dds.QUICK_BATCH, "q1", title="A")
    # it takes the batch ID; the dict is looked up (or passed) from there
    assert is_music_batch(dds.QUICK_BATCH) is False
    assert is_music_batch(dds.MUSIC_VIDEO_BATCH,
                          download_batches.get(dds.QUICK_BATCH)) is False


def test_the_flags_are_restamped_on_every_register():
    """A batch that lost them would be picked up on the music pool's next pass."""
    dds.register(dds.QUICK_BATCH, "q1", title="A")
    download_batches[dds.QUICK_BATCH]["is_music"] = True      # something clobbers it
    dds.register(dds.QUICK_BATCH, "q2", title="B")
    assert download_batches[dds.QUICK_BATCH]["is_music"] is False


def test_progress_moves_the_card():
    dds.register(dds.QUICK_BATCH, "q1", title="A")
    dds.update_progress("q1", percent=42.5, bytes_done=100, bytes_total=200)
    assert download_tasks["q1"]["progress"] == 42.5
    assert download_tasks["q1"]["bytes_transferred"] == 100
    assert download_tasks["q1"]["size"] == 200


def test_progress_is_clamped():
    # A plugin reporting 0-1 and one reporting 0-100 both flow through here.
    dds.register(dds.QUICK_BATCH, "q1", title="A")
    dds.update_progress("q1", percent=140)
    assert download_tasks["q1"]["progress"] == 100.0
    dds.update_progress("q1", percent=-5)
    assert download_tasks["q1"]["progress"] == 0.0


def test_completion_pins_the_bar_to_full():
    dds.register(dds.QUICK_BATCH, "q1", title="A")
    dds.mark_status("q1", "completed", file_path="/music/a.mp3")
    assert download_tasks["q1"]["status"] == "completed"
    assert download_tasks["q1"]["progress"] == 100.0
    assert download_tasks["q1"]["final_file_path"] == "/music/a.mp3"


def test_updates_to_an_unknown_id_are_ignored():
    # A poller outliving its card must not resurrect a row.
    dds.update_progress("gone", percent=50)
    dds.mark_status("gone", "completed")
    assert "gone" not in download_tasks


def test_cancelling_is_visible_to_the_poller():
    dds.register(dds.QUICK_BATCH, "q1", title="A")
    assert dds.is_cancelled("q1") is False
    download_tasks["q1"]["cancel_requested"] = True
    assert dds.is_cancelled("q1") is True


def test_the_batch_goes_when_its_last_card_does():
    """Nothing else will empty it: the music batch healer skips this batch by
    design, so an emptied one would sit on the page forever with nothing in it."""
    dds.register(dds.QUICK_BATCH, "q1", title="A")
    dds.register(dds.QUICK_BATCH, "q2", title="B")
    dds.forget("q1")
    assert dds.QUICK_BATCH in download_batches
    dds.forget("q2")
    assert dds.QUICK_BATCH not in download_batches


def test_a_blank_title_or_id_is_refused():
    assert dds.register(dds.QUICK_BATCH, "", title="A") is False
    assert dds.register(dds.QUICK_BATCH, "q1", title="") is False
    assert not download_tasks


def test_the_two_batches_stay_separate():
    dds.register(dds.MUSIC_VIDEO_BATCH, "v1", title="A")
    dds.register(dds.QUICK_BATCH, "q1", title="B")
    assert download_batches[dds.MUSIC_VIDEO_BATCH]["queue"] == ["v1"]
    assert download_batches[dds.QUICK_BATCH]["queue"] == ["q1"]


# ---------------------------------------------------------------------------
# The call sites
# ---------------------------------------------------------------------------

def test_the_music_video_path_registers_and_reports():
    src = _read("web_server.py")
    fn = src.split("def download_music_video(", 1)[1].split("\n@app.route", 1)[0]
    assert "direct_download_state.register(" in fn
    assert "update_progress(" in fn
    assert fn.count("mark_status(") >= 3, "a terminal state is unreported somewhere"


def test_a_dead_music_video_thread_does_not_leave_a_running_card():
    """The card would say 'downloading' forever."""
    src = _read("web_server.py")
    fn = src.split("def download_music_video(", 1)[1].split("\n@app.route", 1)[0]
    handler = fn.split("except Exception as e:", 1)[1]
    assert "mark_status" in handler


def test_the_basic_search_path_registers_both_branches():
    src = _read("web_server.py")
    fn = src.split("def start_download(", 1)[1].split("\n@app.route", 1)[0]
    assert fn.count("_track_quick_download(") == 2, "album and single-track both need one"


def test_the_quick_poller_always_ends_the_card():
    """Register without a progress source and the card sits at 'downloading'
    forever, which is worse than no card. Every exit has to mark it."""
    src = _read("web_server.py")
    fn = src.split("def _track_quick_download(", 1)[1].split("\n@app.route", 1)[0]
    assert "cancelled" in fn
    assert "stopped reporting" in fn      # the client forgot it
    assert "Timed out" in fn              # the loop ran out
    assert "'completed'" in fn and "'failed'" in fn


def test_the_poller_prefers_bytes_over_the_progress_field():
    """progress is 0-1 on some plugins and 0-100 on others; bytes are not
    ambiguous."""
    src = _read("web_server.py")
    fn = src.split("def _track_quick_download(", 1)[1].split("\n@app.route", 1)[0]
    assert "(done / size) * 100.0" in fn


def test_the_poller_bounds_its_status_call():
    """An unbounded wait on an external client inside a polling thread is how a
    subsystem freezes with no error anywhere."""
    src = _read("web_server.py")
    fn = src.split("def _track_quick_download(", 1)[1].split("\n@app.route", 1)[0]
    assert "timeout=" in fn
