"""YouTube download worker — the fulfillment lane for wished YouTube videos. Pure
orchestration (dest planning, yt-dlp opts, completion → archive + unwish, failure →
archive only) tested with the yt-dlp run + all DB writes injected.
"""

from __future__ import annotations

import json
import os

from core.video import youtube_download as ytd
from core.video.youtube_quality import default_profile


# ── organising fields from the queue row ──────────────────────────────────────
def test_fields_prefer_search_ctx_then_fall_back():
    dl = {"title": "Some Channel", "year": "2024-01-01", "media_id": "vid1",
          "search_ctx": json.dumps({"channel": "Veritasium", "video_title": "Electricity",
                                     "published_at": "2024-03-15"})}
    f = ytd.youtube_fields_from_download(dl)
    assert f == {"channel": "Veritasium", "title": "Electricity",
                 "published_at": "2024-03-15", "youtube_id": "vid1",
                 "channel_id": None, "poster_url": None}


def test_fields_fall_back_to_row_when_ctx_absent_or_garbage():
    dl = {"title": "Chan", "year": "2024-02-02", "media_id": "v2", "search_ctx": "{bad"}
    f = ytd.youtube_fields_from_download(dl)
    assert f["channel"] == "Chan" and f["title"] == "Chan"
    assert f["published_at"] == "2024-02-02" and f["youtube_id"] == "v2"


# ── destination planning ──────────────────────────────────────────────────────
def test_plan_destination_uses_the_youtube_template():
    dl = {"target_dir": "/yt", "media_id": "v1",
          "search_ctx": json.dumps({"channel": "Veritasium", "video_title": "How It Works",
                                    "published_at": "2024-03-15"})}
    dest = ytd.plan_destination(dl, {}, "mp4")
    assert dest["path"] == os.path.join("/yt", "Veritasium", "Season 2024",
                                        "Veritasium - s2024e0315 - How It Works.mp4")


# ── yt-dlp opts ───────────────────────────────────────────────────────────────
def test_ydl_opts_carry_format_selection_and_fixed_output():
    opts = ytd.ydl_download_opts(default_profile(), "/yt/dir", "Chan - 2024-03-15 - Title")
    assert opts["format"] == "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
    assert opts["merge_output_format"] == "mp4"
    assert opts["paths"] == {"home": "/yt/dir"}
    assert opts["outtmpl"] == "Chan - 2024-03-15 - Title.%(ext)s"
    assert opts["noplaylist"] is True


def test_ydl_opts_wire_the_postprocess_hook_for_the_merge_phase():
    # the hook flips the row to 'importing' while ffmpeg merges — so it doesn't sit on 100%
    def hook(_d):
        return None
    opts = ytd.ydl_download_opts(default_profile(), "/d", "stem", postprocess_hook=hook)
    assert opts["postprocessor_hooks"] == [hook]
    assert "postprocessor_hooks" not in ytd.ydl_download_opts(default_profile(), "/d", "stem")


# ── download_one with an injected yt-dlp ───────────────────────────────────────
class _FakeYDL:
    def __init__(self, opts):
        self.opts = opts
        _FakeYDL.last = self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        self.urls = [url]
        return {"title": "Real Title", "upload_date": "20240315"}


class _BoomYDL(_FakeYDL):
    def extract_info(self, url, download=True):
        raise RuntimeError("403 blocked")


def test_download_one_success_returns_built_dest_path():
    res = ytd.download_one("vid1", "/yt/Chan/Season 2024", "Chan - 2024-03-15 - T",
                           default_profile(), "mp4", ydl_factory=_FakeYDL)
    assert res["ok"] is True
    assert res["dest_path"] == os.path.join("/yt/Chan/Season 2024", "Chan - 2024-03-15 - T.mp4")
    assert _FakeYDL.last.urls == ["https://www.youtube.com/watch?v=vid1"]
    # The extractor's own metadata rides along (it's authoritative for titles).
    assert res["title"] == "Real Title" and res["published_at"] == "2024-03-15"


def test_authoritative_fields_fill_a_titleless_row_only():
    good = {"id": 1, "title": "T", "search_ctx": json.dumps({"video_title": "T"})}
    assert ytd.authoritative_download_fields(good, {"title": "Real"}) is good   # untouched
    bare = {"id": 2, "title": "Chan", "search_ctx": json.dumps({"channel": "Chan"})}
    fixed = ytd.authoritative_download_fields(bare, {"title": "Real Title",
                                                     "published_at": "2024-03-15"})
    assert fixed is not bare and fixed["title"] == "Real Title"
    ctx = json.loads(fixed["search_ctx"])
    assert ctx["video_title"] == "Real Title" and ctx["published_at"] == "2024-03-15"
    assert ytd.authoritative_download_fields(bare, {}) is bare                  # no title → no-op


def test_download_one_failure_is_captured_not_raised():
    res = ytd.download_one("vid1", "/yt", "stem", default_profile(), "mp4", ydl_factory=_BoomYDL)
    assert res["ok"] is False and "403 blocked" in res["error"]


def test_download_one_no_factory_is_unavailable():
    res = ytd.download_one("vid1", "/yt", "stem", default_profile(), "mp4", ydl_factory=None)
    # yt_dlp may or may not be importable in the test env; either way no real run happens
    if res["ok"] is False:
        assert res["error"]


# ── the orchestration: completion vs failure ──────────────────────────────────
def _recorder():
    calls = {"rows": [], "archive": [], "unwish": []}

    def update_row(dl_id, **kw):
        calls["rows"].append((dl_id, kw))

    def archive(row, upd):
        calls["archive"].append(upd)

    def clear_wishlist(vid):
        calls["unwish"].append(vid)

    return calls, update_row, archive, clear_wishlist


def _dl():
    return {"id": 7, "media_id": "vid1", "target_dir": "/yt", "title": "Chan", "year": "2024-03-15",
            "search_ctx": json.dumps({"channel": "Chan", "video_title": "T", "published_at": "2024-03-15"})}


def test_process_completion_archives_and_unwishes():
    calls, update_row, archive, clear = _recorder()
    res = ytd.process_youtube_download(
        _dl(), profile=default_profile(), settings={},
        download=lambda *a, **k: {"ok": True, "dest_path": "/yt/Chan/Season 2024/Chan - 2024-03-15 - T.mp4"},
        update_row=update_row, archive=archive, clear_wishlist=clear, now=lambda: "2026-06-25T00:00:00+00:00")
    assert res["status"] == "completed"
    # row marked completed, history snapshot 'completed', and the video unwished
    statuses = [kw.get("status") for _, kw in calls["rows"]]
    assert "downloading" in statuses and statuses[-1] == "completed"
    assert calls["archive"][-1]["status"] == "completed"
    assert calls["unwish"] == ["vid1"]


def test_process_replans_titleless_download_with_extractor_title():
    """The $title bug: a wishlist row that lost its video title used to file as
    '$channel - $date - $channel'. The extractor's title now re-plans the path
    and the file is renamed into it — the row learns the real title too."""
    calls, update_row, archive, clear = _recorder()
    moves = []
    titleless = {"id": 7, "media_id": "vid1", "target_dir": "/yt", "title": "Chan",
                 "year": "2024-03-15",
                 "search_ctx": json.dumps({"channel": "Chan", "published_at": "2024-03-15"})}
    res = ytd.process_youtube_download(
        titleless, profile=default_profile(), settings={},
        download=lambda *a, **k: {"ok": True, "title": "Real Title",
                                  "published_at": "2024-03-15",
                                  "dest_path": "/yt/Chan/Season 2024/Chan - 2024-03-15 - Chan.mp4"},
        update_row=update_row, archive=archive, clear_wishlist=clear,
        move=lambda s, d: moves.append((s, d)), now=lambda: "t")
    assert res["status"] == "completed"
    assert res["dest_path"].endswith("Chan - s2024e0315 - Real Title.mp4")
    assert moves == [("/yt/Chan/Season 2024/Chan - 2024-03-15 - Chan.mp4",
                      res["dest_path"])]                    # renamed into the titled path
    assert calls["rows"][-1][1]["title"] == "Real Title"    # the row learns the title too


def test_process_failure_archives_but_keeps_the_wish():
    calls, update_row, archive, clear = _recorder()
    res = ytd.process_youtube_download(
        _dl(), profile=default_profile(), settings={},
        download=lambda *a, **k: {"ok": False, "error": "yt-dlp said no"},
        update_row=update_row, archive=archive, clear_wishlist=clear, now=lambda: "t")
    assert res["status"] == "failed" and "yt-dlp said no" in res["error"]
    assert calls["rows"][-1][1]["status"] == "failed"
    assert calls["archive"][-1]["status"] == "failed"
    assert calls["unwish"] == []                         # wish kept so it can retry later


def test_process_stages_in_download_folder_then_imports_to_library():
    # the consistent pipeline: download → staging folder → 'importing' → move → library
    calls, update_row, archive, clear = _recorder()
    moves = []
    staged = "/downloads/youtube/Chan - 2024-03-15 - T.mp4"
    res = ytd.process_youtube_download(
        _dl(), profile=default_profile(), settings={},
        download=lambda vid, d, *a, **k: ({"ok": True, "dest_path": staged}
                                          if d == "/downloads/youtube" else {"ok": False, "error": "wrong dir"}),
        update_row=update_row, archive=archive, clear_wishlist=clear,
        stage_dir="/downloads/youtube", move=lambda s, d: moves.append((s, d)), now=lambda: "t")
    assert res["status"] == "completed"
    statuses = [kw.get("status") for _, kw in calls["rows"]]
    assert statuses == ["downloading", "importing", "completed"]      # the visible phases
    final = "/yt/Chan/Season 2024/Chan - s2024e0315 - T.mp4"
    assert moves == [(staged, final)]                                 # staged → organised library
    assert res["dest_path"] == final and calls["unwish"] == ["vid1"]


def test_process_import_failure_is_terminal_and_keeps_the_wish():
    calls, update_row, archive, clear = _recorder()

    def boom(_s, _d):
        raise OSError("disk full")

    res = ytd.process_youtube_download(
        _dl(), profile=default_profile(), settings={},
        download=lambda *a, **k: {"ok": True, "dest_path": "/downloads/youtube/x.mp4"},
        update_row=update_row, archive=archive, clear_wishlist=clear,
        stage_dir="/downloads/youtube", move=boom, now=lambda: "t")
    assert res["status"] == "import_failed"
    statuses = [kw.get("status") for _, kw in calls["rows"]]
    assert statuses == ["downloading", "importing", "import_failed"]
    assert calls["archive"][-1]["status"] == "import_failed"
    assert calls["unwish"] == []                                      # not unwished → can retry


def test_build_episode_nfo():
    from core.video.youtube_download import build_episode_nfo
    nfo = build_episode_nfo({"title": "Cool", "channel": "Chan", "published_at": "2026-06-22",
                             "youtube_id": "abc"}, description="Hi & <there>", runtime=754)
    assert "<title>Cool</title>" in nfo and "<season>2026</season>" in nfo
    assert "<episode>622</episode>" in nfo and "<aired>2026-06-22</aired>" in nfo
    assert "<studio>Chan</studio>" in nfo and '<uniqueid type="youtube"' in nfo
    assert "Hi &amp; &lt;there&gt;" in nfo                     # xml-escaped
    assert "<runtime>13</runtime>" in nfo                      # 754s ≈ 13 min


def test_default_sidecars_writes_thumb_and_nfo_when_on(tmp_path):
    stage, lib = tmp_path / "stage", tmp_path / "lib"
    stage.mkdir(); lib.mkdir()
    base = "Chan - 2026-06-22 - Vid"
    (stage / (base + ".mp4")).write_text("v")
    (stage / (base + ".jpg")).write_text("img")
    (stage / (base + ".info.json")).write_text('{"description": "Desc", "duration": 754}')
    ytd._default_sidecars(str(stage / (base + ".mp4")), str(lib / (base + ".mp4")),
                          {"title": "Vid", "channel": "Chan", "published_at": "2026-06-22", "youtube_id": "v9"},
                          {"save_artwork": True, "write_nfo": True})
    assert (lib / (base + "-thumb.jpg")).exists()              # episode art
    nfo = (lib / (base + ".nfo")).read_text()
    assert "<title>Vid</title>" in nfo and "<aired>2026-06-22</aired>" in nfo and "Desc" in nfo
    assert not (stage / (base + ".info.json")).exists()        # mined + dropped
    assert not (stage / (base + ".jpg")).exists()              # moved out of staging


def test_default_sidecars_off_discards_staged_extras(tmp_path):
    stage, lib = tmp_path / "s", tmp_path / "l"
    stage.mkdir(); lib.mkdir()
    (stage / "V.jpg").write_text("i")
    (stage / "V.info.json").write_text("{}")
    ytd._default_sidecars(str(stage / "V.mp4"), str(lib / "V.mp4"), {"title": "V"}, {})
    assert not (stage / "V.jpg").exists() and not (stage / "V.info.json").exists()   # cleaned up
    assert not (lib / "V-thumb.jpg").exists() and not (lib / "V.nfo").exists()        # nothing written


def test_process_passes_settings_to_sidecars():
    calls, update_row, archive, clear = _recorder()
    got = {}

    def sc(staged, final, fields, settings):
        got["settings"], got["fields"] = settings, fields

    ytd.process_youtube_download(
        _dl(), profile=default_profile(), settings={"write_nfo": True, "save_artwork": False},
        download=lambda *a, **k: {"ok": True, "dest_path": "/yt/Chan/Season 2024/x.mp4"},
        update_row=update_row, archive=archive, clear_wishlist=clear, sidecars=sc, now=lambda: "t")
    assert got["settings"] == {"write_nfo": True, "save_artwork": False}
    assert got["fields"]["youtube_id"] == "vid1"


def test_requeue_orphaned_youtube_recovers_only_dead_downloads():
    """After a restart no worker threads survive, so any 'downloading' YouTube row is an
    orphan → back to 'queued'. A row whose worker is still alive (in _active_worker_ids) and
    non-youtube / non-downloading rows are left alone."""
    updates = []

    class _DB:
        def get_active_video_downloads(self):
            return [
                {"id": 1, "source": "youtube", "status": "downloading"},   # orphan → requeue
                {"id": 2, "source": "youtube", "status": "downloading"},   # live worker → keep
                {"id": 3, "source": "youtube", "status": "queued"},        # not downloading → keep
                {"id": 4, "source": "soulseek", "status": "downloading"},  # not youtube → keep
            ]

        def update_video_download(self, dl_id, **kw):
            updates.append((dl_id, kw))

    ytd._active_worker_ids.clear()
    ytd._active_worker_ids.add(2)                      # id 2 has a live worker
    try:
        n = ytd.requeue_orphaned_youtube(lambda: _DB())
    finally:
        ytd._active_worker_ids.clear()
    assert n == 1
    assert updates == [(1, {"status": "queued", "progress": 0})]   # only the orphan


def test_process_passes_the_organised_dir_to_the_downloader():
    seen = {}

    def fake_download(video_id, dest_dir, stem, profile, container, **kw):
        seen.update(video_id=video_id, dest_dir=dest_dir, stem=stem, container=container)
        return {"ok": True, "dest_path": "/x"}

    calls, update_row, archive, clear = _recorder()
    ytd.process_youtube_download(_dl(), profile=default_profile(), settings={},
                                 download=fake_download, update_row=update_row,
                                 archive=archive, clear_wishlist=clear, now=lambda: "t")
    assert seen["video_id"] == "vid1"
    assert seen["dest_dir"] == os.path.join("/yt", "Chan", "Season 2024")
    assert seen["stem"] == "Chan - s2024e0315 - T" and seen["container"] == "mp4"


def test_process_never_clobbers_target_dir_so_reruns_dont_nest():
    """The row's target_dir is the youtube ROOT; plan_destination derives the channel/season
    folders under it. The worker must NOT write the organised dir back to target_dir, or a
    re-run (e.g. the orphan reaper re-queues an interrupted download) would organise AGAIN →
    Channel/Season/Channel/Season. Re-processing the same row must be idempotent."""
    seen = []

    def fake_download(video_id, dest_dir, stem, profile, container, **kw):
        seen.append(dest_dir)
        return {"ok": True, "dest_path": "/x"}

    calls, update_row, archive, clear = _recorder()
    dl = _dl()                                          # target_dir = "/yt" (the root)
    for _ in range(2):                                  # simulate the interrupted-then-requeued re-run
        ytd.process_youtube_download(dl, profile=default_profile(), settings={},
                                     download=fake_download, update_row=update_row,
                                     archive=archive, clear_wishlist=clear, now=lambda: "t")
    # no update_row call writes target_dir (that's what caused the nesting)
    assert all("target_dir" not in kw for _, kw in calls["rows"])
    # both runs target the SAME organised dir — not a doubly-nested one
    assert seen[0] == seen[1] == os.path.join("/yt", "Chan", "Season 2024")


# ── ytdl-sub parity: dual-convention episode art + channel show assets ────────

def test_thumb_lands_in_both_server_conventions(tmp_path):
    """Plex Local Media Assets reads the SAME-STEM jpg; Jellyfin/Kodi read -thumb.
    Two cheap copies, both servers happy."""
    stage, lib = tmp_path / "stage", tmp_path / "lib"
    stage.mkdir(); lib.mkdir()
    base = "Chan - s2026e0622 - Vid"
    (stage / (base + ".mp4")).write_text("v")
    (stage / (base + ".jpg")).write_text("img")
    ytd._default_sidecars(str(stage / (base + ".mp4")), str(lib / (base + ".mp4")),
                          {"title": "Vid", "channel": "Chan"},
                          {"save_artwork": True})
    assert (lib / (base + ".jpg")).exists()          # Plex convention
    assert (lib / (base + "-thumb.jpg")).exists()    # Jellyfin/Kodi convention
    assert not (stage / (base + ".jpg")).exists()    # staging cleaned


class _FakeFS:
    """Records save_url instead of hitting the network; real text/dir ops."""
    def __init__(self):
        self.saved = []
    def list_dir(self, path):
        import os
        try: return os.listdir(path)
        except OSError: return []
    def makedirs(self, path):
        import os
        os.makedirs(path, exist_ok=True)
    def write_text(self, path, content):
        with open(path, "w", encoding="utf-8") as f: f.write(content)
    def save_url(self, url, dst):
        self.saved.append((url, dst))
        with open(dst, "wb") as f: f.write(b"art")


def test_channel_folder_gets_show_assets_once(tmp_path, monkeypatch):
    """The channel dir (found template-agnostically) is seeded with poster.jpg
    (channel AVATAR via the remembered channel meta — the download row's
    poster_url is the VIDEO thumbnail and must never become the show poster),
    fanart.jpg (banner) and tvshow.nfo; the season folder gets a poster too.
    All idempotent — a second episode refetches nothing."""
    import core.video.importer as imp
    fs = _FakeFS()
    monkeypatch.setattr(imp, "real_fs", lambda: fs)

    lib = tmp_path / "yt" / "Veritasium" / "Season 2026"
    lib.mkdir(parents=True)
    final = lib / "Veritasium - s2026e0711 - Vid.mp4"
    final.write_text("v")
    fields = {"title": "Vid", "channel": "Veritasium", "channel_id": "UC123",
              "poster_url": "http://a/VIDEO-THUMB.jpg",   # video thumb — not the poster
              "published_at": "2026-07-11", "youtube_id": "v1"}
    lookup_calls = []
    def lookup(cid):
        lookup_calls.append(cid)
        return {"avatar_url": "http://a/avatar.jpg", "banner_url": "http://a/banner.jpg",
                "description": "Science videos."}

    ytd._ensure_channel_assets(str(final), fields, {"save_artwork": True, "write_nfo": True}, lookup)

    chan = tmp_path / "yt" / "Veritasium"
    assert (chan / "poster.jpg").exists() and (chan / "fanart.jpg").exists()
    assert lookup_calls == ["UC123"]
    nfo = (chan / "tvshow.nfo").read_text()
    assert "<title>Veritasium</title>" in nfo and "Science videos." in nfo
    assert ("http://a/avatar.jpg", str(chan / "poster.jpg")) in fs.saved
    assert not any(u == "http://a/VIDEO-THUMB.jpg" for u, _ in fs.saved)
    # the year folder gets a poster as well (bare 'Season 2026' cards look broken)
    assert (lib / "poster.jpg").exists()

    # second episode: everything already present → zero refetches
    before = list(fs.saved)
    ytd._ensure_channel_assets(str(final), fields, {"save_artwork": True, "write_nfo": True}, lookup)
    assert fs.saved == before


def test_channel_assets_skip_relative_proxied_urls(tmp_path, monkeypatch):
    """A proxied /api/... avatar url must never reach the fetcher (it was why a
    fresh channel got no poster at all — urlopen can't open a relative url)."""
    import core.video.importer as imp
    fs = _FakeFS()
    monkeypatch.setattr(imp, "real_fs", lambda: fs)
    lib = tmp_path / "yt" / "Chan" / "Season 2026"
    lib.mkdir(parents=True)
    final = lib / "Chan - s2026e0711 - Vid.mp4"
    final.write_text("v")
    ytd._ensure_channel_assets(str(final), {"title": "Vid", "channel": "Chan", "channel_id": "UC1"},
                               {"save_artwork": True, "write_nfo": True},
                               lambda cid: {"avatar_url": "/api/video/youtube/img?u=x"})
    assert fs.saved == []                                  # nothing fetched
    assert (tmp_path / "yt" / "Chan" / "tvshow.nfo").exists()   # nfo still lands


def test_channel_assets_skipped_for_flat_templates(tmp_path, monkeypatch):
    """A custom template without a channel folder gets no show assets (nowhere
    correct to put them) — and nothing blows up."""
    import core.video.importer as imp
    monkeypatch.setattr(imp, "real_fs", lambda: _FakeFS())
    final = tmp_path / "Chan - s2026e0711 - Vid.mp4"
    final.write_text("v")
    ytd._ensure_channel_assets(str(final), {"title": "Vid", "channel": "Chan"},
                               {"save_artwork": True, "write_nfo": True}, None)
    assert not (tmp_path / "tvshow.nfo").exists()


def test_channel_art_lands_BEFORE_the_video_moves_in(tmp_path, monkeypatch):
    """Plex reads show-level art at SHOW CREATION, and its folder watch ingests
    the mp4 the instant it appears — so the poster must already be on disk when
    the video arrives (art written after the move is invisible until a manual
    refresh; ytdl-sub works because its prepared output moves in art-first)."""
    import core.video.importer as imp
    fs = _FakeFS()
    monkeypatch.setattr(imp, "real_fs", lambda: fs)

    stage = tmp_path / "downloads" / "youtube"
    stage.mkdir(parents=True)
    lib = tmp_path / "yt"
    lib.mkdir()
    dl = {"id": 1, "media_id": "v1", "target_dir": str(lib),
          "search_ctx": json.dumps({"channel": "Chan", "channel_id": "UC1",
                                    "video_title": "T", "published_at": "2026-07-11"})}

    def fake_download(vid, dest_dir, stem, profile, cont, **_kw):
        pathlib_p = os.path.join(dest_dir, stem + "." + cont)
        with open(pathlib_p, "w") as f:
            f.write("v")
        return {"ok": True, "dest_path": pathlib_p}

    seen = {}
    def move(src, dst):
        # the assertion that matters: poster is there before the video is
        seen["poster_at_move"] = (lib / "Chan" / "poster.jpg").exists()
        seen["season_at_move"] = (lib / "Chan" / "Season 2026" / "poster.jpg").exists()
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(src, dst)

    from core.video.youtube_download import _ensure_channel_assets as eca
    res = ytd.process_youtube_download(
        dl, profile=default_profile(),
        settings={"save_artwork": True, "write_nfo": True},
        download=fake_download,
        update_row=lambda *a, **k: None,
        archive=lambda *a, **k: None,
        clear_wishlist=lambda *a: None,
        stage_dir=str(stage), move=move,
        sidecars=lambda *a, **k: None,
        channel_assets=lambda fin, flds, stg: eca(
            fin, flds, stg, lambda cid: {"avatar_url": "http://a/av.jpg"}),
        now=lambda: "t")
    assert res["status"] == "completed"
    assert seen["poster_at_move"] is True
    assert seen["season_at_move"] is True
