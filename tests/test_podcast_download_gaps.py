"""two silent gaps in podcast episode downloads.

1. every queued episode got its own thread the moment it was queued. the
   batch's max_concurrent was a number nobody read, so one watchlist scan
   across a few shows with a backlog opened every download at once.
2. the episode was written to downloaded_podcast_episodes at QUEUE time and
   any row counted as downloaded, so a failed or cancelled auto-download (or
   a restart mid-download) was "done" forever: the scan never retried it
   and nothing said so. the title-only fallback also matched an "Episode 1"
   from any other show.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

import api.podcasts as podcasts_api
from api.podcasts import create_podcasts_blueprint
from core.runtime_state import download_tasks
from database.music_database import MusicDatabase


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(create_podcasts_blueprint())
    return app.test_client()


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "podcasts.db"))


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch):
    # each test gets its own semaphore, and 2 slots so a third has to wait
    monkeypatch.setattr(podcasts_api, "_download_slots", None)
    monkeypatch.setattr(podcasts_api, "_download_slots_size", 0)
    monkeypatch.setattr(podcasts_api, "_max_concurrent_downloads", lambda: 2)
    yield
    monkeypatch.setattr(podcasts_api, "_download_slots", None)


def _wait(task_id, states, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if download_tasks.get(task_id, {}).get("status") in states:
            return download_tasks[task_id]
        time.sleep(0.02)
    return download_tasks.get(task_id, {})


# ── 1. concurrency ───────────────────────────────────────────────────────────

def test_only_so_many_episodes_download_at_once(client):
    running = {"now": 0, "peak": 0}
    gate = threading.Event()
    lock = threading.Lock()

    def slow_download(ep, progress_callback=None, show_title=None, author=None,
                      is_cancelled=None, show_metadata=None):
        with lock:
            running["now"] += 1
            running["peak"] = max(running["peak"], running["now"])
        gate.wait(3.0)
        with lock:
            running["now"] -= 1
        return f"/downloads/{ep.guid}.mp3"

    mock_dl = MagicMock()
    mock_dl.download_episode.side_effect = slow_download
    with patch("api.podcasts._get_download_client", return_value=mock_dl), \
         patch("api.podcasts._db", return_value=None):
        ids = []
        for i in range(4):
            res = client.post("/api/podcasts/download", json={
                "enclosure_url": f"https://example.com/ep{i}.mp3", "guid": f"g{i}",
                "title": f"Episode {i}", "show_title": "Show",
            })
            ids.append(res.get_json()["task_id"])
        time.sleep(0.4)
        with lock:
            assert running["now"] == 2, "two slots: two downloading, two queued"
        queued = [download_tasks[t]["status"] for t in ids]
        assert sorted(queued) == ["downloading", "downloading", "queued", "queued"]
        gate.set()
        for t in ids:
            assert _wait(t, ("completed",))["status"] == "completed"
        assert running["peak"] == 2


def test_a_cancel_while_waiting_for_a_slot_never_starts(client):
    gate = threading.Event()

    def slow_download(ep, **_):
        gate.wait(3.0)
        return f"/downloads/{ep.guid}.mp3"

    mock_dl = MagicMock()
    mock_dl.download_episode.side_effect = slow_download
    forgotten = []
    fake_db = MagicMock()
    fake_db.forget_podcast_episode_attempt.side_effect = lambda **kw: forgotten.append(kw) or True
    with patch("api.podcasts._get_download_client", return_value=mock_dl), \
         patch("api.podcasts._db", return_value=fake_db):
        ids = []
        for i in range(3):
            res = client.post("/api/podcasts/download", json={
                "enclosure_url": f"https://example.com/w{i}.mp3", "guid": f"w{i}",
                "title": f"Episode {i}", "show_title": "Show", "feed_url": "https://example.com/feed.xml",
            })
            ids.append(res.get_json()["task_id"])
        time.sleep(0.3)
        waiting = [t for t in ids if download_tasks[t]["status"] == "queued"]
        assert len(waiting) == 1
        download_tasks[waiting[0]]["cancel_requested"] = True
        task = _wait(waiting[0], ("cancelled",))
        assert task["status"] == "cancelled"
        gate.set()
        for t in ids:
            _wait(t, ("completed", "cancelled"))
        # the cancelled one never reached the client, and its placeholder went
        assert mock_dl.download_episode.call_count == 2
        assert len(forgotten) == 1
        assert forgotten[0]["feed_url"] == "https://example.com/feed.xml"


# ── 2. a failed download is not "downloaded" ─────────────────────────────────

def test_a_queued_but_unfinished_episode_does_not_count_as_downloaded(db):
    feed, enc = "https://example.com/feed.xml", "https://example.com/ep1.mp3"
    assert db.record_downloaded_podcast_episode(feed_url=feed, enclosure_url=enc, guid="g1", title="Ep 1")
    # what queue_podcast_download writes: a row with no file yet
    assert db.is_podcast_episode_downloaded(feed, enclosure_url=enc, guid="g1", title="Ep 1") is False
    # the download lands: now it counts
    assert db.record_downloaded_podcast_episode(feed_url=feed, enclosure_url=enc, guid="g1",
                                                title="Ep 1", file_path="/pod/ep1.mp3")
    assert db.is_podcast_episode_downloaded(feed, enclosure_url=enc, guid="g1", title="Ep 1") is True


def test_a_pruned_episode_still_counts_as_downloaded(db):
    feed, enc = "https://example.com/feed.xml", "https://example.com/ep1.mp3"
    db.record_downloaded_podcast_episode(feed_url=feed, enclosure_url=enc, guid="g1",
                                         title="Ep 1", file_path="/pod/ep1.mp3")
    rows = db.get_downloaded_podcast_episodes(feed_url=feed)
    assert db.mark_podcast_episode_pruned(rows[0]["id"])
    # retention removed the file; that is not an invitation to fetch it again
    assert db.is_podcast_episode_downloaded(feed, enclosure_url=enc, guid="g1") is True


def test_forgetting_an_attempt_only_drops_a_placeholder(db):
    feed = "https://example.com/feed.xml"
    db.record_downloaded_podcast_episode(feed_url=feed, enclosure_url="https://example.com/a.mp3", guid="a")
    db.record_downloaded_podcast_episode(feed_url=feed, enclosure_url="https://example.com/b.mp3", guid="b",
                                         file_path="/pod/b.mp3")
    assert db.forget_podcast_episode_attempt(feed, "https://example.com/a.mp3") is True
    assert db.forget_podcast_episode_attempt(feed, "https://example.com/b.mp3") is False
    assert [r["guid"] for r in db.get_downloaded_podcast_episodes(feed_url=feed)] == ["b"]


def test_a_failed_download_leaves_the_episode_wanted_again(client, db):
    feed, enc = "https://example.com/feed.xml", "https://example.com/fail.mp3"
    mock_dl = MagicMock()
    mock_dl.download_episode.side_effect = RuntimeError("connection reset")
    with patch("api.podcasts._get_download_client", return_value=mock_dl), \
         patch("api.podcasts._db", return_value=db):
        res = client.post("/api/podcasts/download", json={
            "enclosure_url": enc, "guid": "fail-1", "title": "Ep", "show_title": "Show", "feed_url": feed,
        })
        task = _wait(res.get_json()["task_id"], ("failed",))
    assert task["status"] == "failed"
    # the next scan may try again: nothing claims it was downloaded
    assert db.is_podcast_episode_downloaded(feed, enclosure_url=enc, guid="fail-1") is False
    assert db.get_downloaded_podcast_episodes(feed_url=feed) == []


def test_the_title_fallback_is_scoped_to_the_show(db):
    db.add_library_history_entry(
        event_type="podcast", title="Episode 1", artist_name="Host A", album_name="Show A",
        quality="audio/mpeg", file_path="/pod/a1.mp3", download_source="Podcast",
        source_track_id="a-1", origin="podcast", origin_context="Show A",
    )
    # same episode title on another show is not this episode
    assert db.is_podcast_episode_downloaded("https://b/feed", title="Episode 1", show_title="Show B") is False
    assert db.is_podcast_episode_downloaded("https://a/feed", title="Episode 1", show_title="Show A") is True
    # a caller that cannot name the show keeps the old, looser answer
    assert db.is_podcast_episode_downloaded("https://b/feed", title="Episode 1") is True


# ── the page knows what is on disk after a restart ───────────────────────────

def test_the_show_says_which_episodes_are_on_disk(client, db):
    from core.podcast_client import PodcastEpisode, PodcastShow
    feed = "https://example.com/feed.xml"
    show = PodcastShow(
        title="Show", author="Host", description="", artwork_url="", feed_url=feed,
        itunes_id=1, website="", language="en", explicit=False, categories=[], episode_count=3,
        episodes=[
            PodcastEpisode(guid="g1", title="One", enclosure_url="https://example.com/1.mp3",
                           enclosure_type="audio/mpeg", enclosure_length=1, pub_date=None,
                           duration_seconds=1, description="", show_notes="", season=None,
                           episode_number=None, episode_type="full", artwork_url=None,
                           chapter_url=None, transcript_url=None),
            PodcastEpisode(guid="g2", title="Two", enclosure_url="https://example.com/2.mp3",
                           enclosure_type="audio/mpeg", enclosure_length=1, pub_date=None,
                           duration_seconds=1, description="", show_notes="", season=None,
                           episode_number=None, episode_type="full", artwork_url=None,
                           chapter_url=None, transcript_url=None),
            PodcastEpisode(guid="g3", title="Three", enclosure_url="https://example.com/3.mp3",
                           enclosure_type="audio/mpeg", enclosure_length=1, pub_date=None,
                           duration_seconds=1, description="", show_notes="", season=None,
                           episode_number=None, episode_type="full", artwork_url=None,
                           chapter_url=None, transcript_url=None),
        ],
    )
    # one landed, one was only queued (a restart mid-download), one pruned by retention
    db.record_downloaded_podcast_episode(feed_url=feed, enclosure_url="https://example.com/1.mp3",
                                         guid="g1", file_path="/pod/1.mp3")
    db.record_downloaded_podcast_episode(feed_url=feed, enclosure_url="https://example.com/2.mp3", guid="g2")
    db.record_downloaded_podcast_episode(feed_url=feed, enclosure_url="https://example.com/3.mp3",
                                         guid="g3", file_path="/pod/3.mp3")
    pruned = next(r for r in db.get_downloaded_podcast_episodes(feed_url=feed) if r["guid"] == "g3")
    db.mark_podcast_episode_pruned(pruned["id"])

    fake_client = MagicMock()
    fake_client.fetch_feed.return_value = show
    with patch("api.podcasts.get_podcast_client", return_value=fake_client), \
         patch("api.podcasts._db", return_value=db), \
         patch("api.podcasts._show_cache", {}):
        body = client.get("/api/podcasts/show", query_string={"url": feed}).get_json()
        assert body["success"]
        flags = {ep["guid"]: (ep.get("downloaded"), ep.get("file_path")) for ep in body["show"]["episodes"]}
        assert flags["g1"] == (True, "/pod/1.mp3")
        assert flags["g2"] == (None, None), "queued-only is not on disk"
        assert flags["g3"] == (None, None), "pruned is not on disk"

        # the cached copy of the show carries no flags of its own: a download
        # that lands later shows up on the next request without waiting out
        # the feed cache
        db.record_downloaded_podcast_episode(feed_url=feed, enclosure_url="https://example.com/2.mp3",
                                             guid="g2", file_path="/pod/2.mp3")
        body = client.get("/api/podcasts/show", query_string={"url": feed}).get_json()
        assert next(ep for ep in body["show"]["episodes"] if ep["guid"] == "g2")["downloaded"] is True
        assert fake_client.fetch_feed.call_count == 1
