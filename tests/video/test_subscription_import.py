"""Import ytdl-sub / Kometa subscription files → SoulSync YouTube follows.

Parser is a tolerant line scanner (no PyYAML dep): pulls url / tv_show_name /
active presets per block, skips what it can't read, never raises on a messy
file. Importer resolves + follows each (channel or playlist), applies the show
name as the channel custom name and best_video_quality as a quality override —
all seams injected so the flow tests without yt-dlp.
"""

from __future__ import annotations

from core.video.subscriptions import (
    import_subscriptions,
    parse_subscriptions,
    wants_best_quality,
)

# A slice of a real ytdl-sub file covering every URL/edge shape.
_SAMPLE = """
music_videos:
  preset:
    - "plex_tv_show_by_date"
    - "best_video_quality"
    #- "only_recent_videos"
  overrides:
    tv_show_name: "Official Music Videos"
    tv_show_directory: "./Downloads"
    url: "https://youtube.com/playlist?list=PL4fhv_z5F-JA50VbTZaE9SjLP9yB1U-uW&si=NpXn6yvnPBJpU8ur"

phonicsman:
  preset:
    - "plex_tv_show_by_date"
    #- "best_video_quality"
    - "only_recent_videos"
  overrides:
    tv_show_name: "Phonicsman"
    tv_show_directory: "./Downloads"
    url: "https://www.youtube.com/@PhonicsMan/videos"

nick crowley:
  preset:
    - "plex_tv_show_by_date"
  overrides:
    tv_show_name: "Nick Crowley"
    url: "https://www.youtube.com/@NickCrowley"

mr_ballen_extra:
  preset:
    - "plex_tv_show_by_date"
  overrides:
    tv_show_name: "Mr. Ballen"
    url: "https://www.youtube.com/watch?v=r4bUv4We_BU&list=PL4fhv_z5F-JDSXpFyVZb9NWrAk01dekLF"

tasting_history:
  overrides:
    tv_show_name: "TASTING HISTORY with Max Miller"
    url: "https://www.youtube.com/channel/UCsaGKqPZnGp_7N80hcHySGQ"

broken_block:
  preset:
    - "plex_tv_show_by_date"
  overrides:
    tv_show_name: "No URL here"
"""


def _by_name(subs):
    return {s["name"]: s for s in subs}


def test_parses_every_url_shape_and_show_name():
    subs = parse_subscriptions(_SAMPLE)
    d = _by_name(subs)
    # the URL-less block is dropped, everything else kept
    assert set(d) == {"music_videos", "phonicsman", "nick crowley",
                      "mr_ballen_extra", "tasting_history"}
    assert d["music_videos"]["url"].startswith("https://youtube.com/playlist?list=PL4fhv")
    assert d["phonicsman"]["url"] == "https://www.youtube.com/@PhonicsMan/videos"
    assert d["nick crowley"]["show_name"] == "Nick Crowley"          # space-in-key survives
    assert d["tasting_history"]["url"].endswith("/UCsaGKqPZnGp_7N80hcHySGQ")
    assert "watch?v=r4bUv4We_BU" in d["mr_ballen_extra"]["url"]


def test_commented_presets_are_not_active():
    d = _by_name(parse_subscriptions(_SAMPLE))
    assert "best_video_quality" in d["music_videos"]["presets"]       # uncommented
    assert "best_video_quality" not in d["phonicsman"]["presets"]     # #- commented
    assert wants_best_quality(d["music_videos"]["presets"]) is True
    assert wants_best_quality(d["phonicsman"]["presets"]) is False


def test_directory_override_is_ignored_not_mistaken_for_url():
    d = _by_name(parse_subscriptions(_SAMPLE))
    for s in d.values():
        assert "Downloads" not in (s["url"] or "")


def test_empty_and_garbage_never_raise():
    assert parse_subscriptions("") == []
    assert parse_subscriptions("just a line\nno structure") == []
    assert parse_subscriptions(None) == []           # type: ignore[arg-type]


# ── the import runner ────────────────────────────────────────────────────────
def _seams(followed=(), existing_channels=()):
    calls = {"followed": [], "settings": {}}

    def resolve_channel(url):
        if "playlist" in url or "list=" in url:
            return None
        return {"youtube_id": "UC-" + url[-6:], "title": "Chan " + url[-4:]}

    def resolve_playlist(url):
        return {"playlist_id": "PL-" + url[-6:], "title": "List " + url[-4:]}

    def is_playlist(url):
        return "list=" in url or "/playlist" in url

    def follow_channel(ch):
        calls["followed"].append(ch["youtube_id"])
        return ch["youtube_id"] not in existing_channels    # False = already following

    def follow_playlist(pl):
        calls["followed"].append(pl["playlist_id"])
        return True

    def apply_settings(cid, cs):
        calls["settings"][cid] = cs

    return calls, dict(resolve_channel=resolve_channel, resolve_playlist=resolve_playlist,
                       is_playlist=is_playlist, follow_channel=follow_channel,
                       follow_playlist=follow_playlist, apply_channel_settings=apply_settings)


def test_import_follows_channels_and_playlists_and_applies_names():
    subs = parse_subscriptions(_SAMPLE)
    calls, seams = _seams()
    res = import_subscriptions(subs, **seams)
    assert res["total"] == 5
    assert res["followed"] == 5 and res["failed"] == 0
    # music_videos + mr_ballen_extra are playlists; the rest channels
    kinds = {i["name"]: i["kind"] for i in res["items"]}
    assert kinds["music_videos"] == "playlist" and kinds["mr_ballen_extra"] == "playlist"
    assert kinds["phonicsman"] == "channel"
    # show name applied as custom_name only when it differs from the channel title
    ph = next(i for i in res["items"] if i["name"] == "phonicsman")
    assert calls["settings"][ph["youtube_id"]]["custom_name"] == "Phonicsman"
    # best_video_quality → a quality override (music_videos is a playlist, so its
    # quality intent doesn't apply; use a channel one)
    assert all("quality" not in cs or cs["quality"]["max_resolution"] == "best"
               for cs in calls["settings"].values())


def test_already_following_is_left_untouched_not_reconfigured():
    """An import is additive: a channel you already follow is counted skipped and
    its settings are NOT overwritten with the file's show name/quality (you may
    have set a custom name by hand)."""
    subs = parse_subscriptions(
        "x:\n  preset:\n    - best_video_quality\n  overrides:\n"
        "    tv_show_name: Renamed\n    url: https://youtube.com/@x")
    cid = "UC-" + "https://youtube.com/@x"[-6:]
    calls, seams = _seams(existing_channels={cid})
    res = import_subscriptions(subs, **seams)
    assert res["followed"] == 0 and res["skipped"] == 1
    assert calls["settings"] == {}          # existing config left exactly as-is


def test_one_bad_subscription_never_aborts_the_batch():
    subs = parse_subscriptions(_SAMPLE)
    calls, seams = _seams()

    def boom(url):
        if "PhonicsMan" in url:
            raise RuntimeError("network")
        return {"youtube_id": "UC" + url[-4:], "title": "ok"}
    seams["resolve_channel"] = boom
    res = import_subscriptions(subs, **seams)
    assert res["failed"] >= 1 and res["total"] == 5      # batch completed anyway


def test_progress_and_stop_are_honored():
    subs = parse_subscriptions(_SAMPLE)
    calls, seams = _seams()
    seen = []
    res = import_subscriptions(subs, on_progress=lambda n, item: seen.append(n),
                               should_stop=lambda: len(seen) >= 2, **seams)
    assert seen == [1, 2] and res["total"] == 5          # stopped early after 2


# ── API endpoints ────────────────────────────────────────────────────────────
import time  # noqa: E402

import pytest  # noqa: E402
from flask import Flask  # noqa: E402

from database.video_database import VideoDatabase  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.subscriptions as subs
    import core.video.youtube as yt
    subs._reset_for_tests()
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    # fake yt-dlp resolvers — instant, no network
    monkeypatch.setattr(yt, "resolve_channel",
                        lambda u, **k: {"youtube_id": "UC" + u[-5:], "title": "T " + u[-3:], "videos": []})
    monkeypatch.setattr(yt, "resolve_playlist",
                        lambda u, **k: {"playlist_id": "PL" + u[-5:], "title": "P " + u[-3:], "videos": []})
    monkeypatch.setattr(yt, "parse_playlist_id", lambda u: "PLx" if "list=" in u else None)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client(), videoapi._video_db
    finally:
        subs._reset_for_tests()
        videoapi._video_db = None


def test_preview_parses_without_resolving(client):
    c, _db = client
    r = c.post("/api/video/youtube/subscriptions/preview", json={"text": _SAMPLE})
    d = r.get_json()
    assert d["success"] and d["count"] == 5
    assert {s["name"] for s in d["subscriptions"]} >= {"phonicsman", "music_videos"}


def test_import_runs_in_background_and_follows(client):
    c, db = client
    r = c.post("/api/video/youtube/subscriptions/import", json={"text": _SAMPLE})
    assert r.get_json()["started"] is True and r.get_json()["total"] == 5

    for _ in range(200):     # poll to completion (fakes finish in ms)
        st = c.get("/api/video/youtube/subscriptions/import/status").get_json()
        if st["finished"]:
            break
        time.sleep(0.02)
    assert st["finished"] and st["running"] is False
    assert st["followed"] == 5 and st["failed"] == 0
    # the follows actually landed in the DB
    assert len(db.list_watchlist_channels()) == 3       # phonicsman, nick crowley, tasting_history
    assert len(db.list_watchlist_playlists()) == 2      # music_videos, mr_ballen_extra
    # the show name was applied as a custom_name where it differs
    ph = next(i for i in st["items"] if i["name"] == "phonicsman")
    assert db.get_channel_settings(ph["youtube_id"]).get("custom_name") == "Phonicsman"


def test_import_rejects_an_empty_file(client):
    c, _db = client
    r = c.post("/api/video/youtube/subscriptions/import", json={"text": "nothing here"})
    assert r.status_code == 400


def test_overlap_with_a_manually_added_channel_is_left_alone(client):
    """End-to-end: a channel already followed with a hand-set custom name is
    skipped by the import and its config survives (not clobbered by the file)."""
    c, db = client
    cid = "UC" + "https://youtube.com/@gn"[-5:]     # what the fake resolver will produce
    db.add_channel_to_watchlist({"youtube_id": cid, "title": "Gamers Nexus"})
    db.set_channel_settings(cid, {"custom_name": "My GN Name"})

    c.post("/api/video/youtube/subscriptions/import", json={
        "text": "gn:\n  preset:\n    - best_video_quality\n  overrides:\n"
                "    tv_show_name: Imported Name\n    url: https://youtube.com/@gn"})
    for _ in range(200):
        st = c.get("/api/video/youtube/subscriptions/import/status").get_json()
        if st["finished"]:
            break
        time.sleep(0.02)
    assert st["skipped"] == 1 and st["followed"] == 0
    assert db.get_channel_settings(cid).get("custom_name") == "My GN Name"   # untouched
    assert len(db.list_watchlist_channels()) == 1                            # no duplicate


# ── frontend wiring ──────────────────────────────────────────────────────────
def test_ui_is_wired():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    js = (root / "webui" / "static" / "video" / "video-subscriptions.js").read_text(encoding="utf-8")
    idx = (root / "webui" / "index.html").read_text(encoding="utf-8")
    wl = (root / "webui" / "static" / "video" / "video-watchlist.js").read_text(encoding="utf-8")
    # the toolbar button + script include
    assert "data-vwlp-import" in idx and "video-subscriptions.js" in idx
    # button is channel-tab-only
    assert "imp.hidden = tab !== 'channel'" in wl
    # modal flow: preview → import → status poll
    assert "/subscriptions/preview" in js and "/subscriptions/import" in js
    assert "/subscriptions/import/status" in js
    assert "function preview" in js and "function startImport" in js and "function tick" in js
    assert "readAsText" in js               # file upload
    assert "soulsync:video-wishlist-changed" in js   # refreshes the watchlist on finish
    # the button is truly hidden off the Channels tab (display:inline-flex would
    # otherwise beat the [hidden] attribute — the health-strip trap)
    css = (root / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")
    assert ".vwlp-import-btn[hidden] { display: none; }" in css
