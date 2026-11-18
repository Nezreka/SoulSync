"""Video download source-config — pure normalize for mode + hybrid chain
(soulseek/torrent/usenet only), isolated from music."""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

from core.video.download_config import (
    MODES,
    SOURCES,
    load,
    normalize_hybrid_order,
    normalize_mode,
    save,
)


def test_modes_are_video_only():
    """No streaming sources — those are music-only. EXT.to joined the list in
    861936c7a so it can be named explicitly in a hybrid chain; it is still a
    torrent underneath (the grab files it as one), it is just discoverable as
    its own lane."""
    assert SOURCES == ("soulseek", "torrent", "usenet", "extto")
    assert MODES == ("soulseek", "torrent", "usenet", "extto", "hybrid")
    for music_only in ("spotify", "tidal", "qobuz", "deezer", "youtube"):
        assert music_only not in SOURCES


def test_normalize_mode():
    assert normalize_mode("torrent") == "torrent"
    assert normalize_mode("HYBRID") == "hybrid"
    assert normalize_mode("spotify") == "soulseek"   # music sources rejected
    assert normalize_mode(None) == "soulseek"
    assert normalize_mode("") == "soulseek"


def test_normalize_hybrid_order_filters_dedupes_defaults():
    assert normalize_hybrid_order(["torrent", "usenet"]) == ["torrent", "usenet"]
    assert normalize_hybrid_order(["torrent", "torrent", "spotify"]) == ["torrent"]
    assert normalize_hybrid_order([]) == ["soulseek"]        # never empty
    assert normalize_hybrid_order("garbage") == ["soulseek"]
    # Accepts a JSON string (as stored in the KV table).
    assert normalize_hybrid_order(json.dumps(["usenet", "soulseek"])) == ["usenet", "soulseek"]


class _FakeDB:
    def __init__(self):
        self._kv = {}

    def get_setting(self, key, default=None):
        return self._kv.get(key, default)

    def set_setting(self, key, value):
        self._kv[key] = value


# seeding lifecycle keys (arr-parity P5) ride the same config payload
_SEED_DEFAULTS = {"seed_ratio_goal": 0.0, "seed_time_goal_hours": 0, "seed_remove_data": True,
                  "seed_mode": "soulsync",
                  # per indexer rules, empty until someone sets one
                  "seed_overrides": {},
                  # Remove Completed (usenet) is opt-in: off until someone picks it
                  "usenet_remove_completed": "off",
                  # a release the client failed is replaced by the next one: on
                  "redownload_failed": True,
                  # Specials are monitored unless someone turns this off
                  "monitor_specials": True}


def test_load_defaults():
    assert load(_FakeDB()) == {"download_mode": "soulseek", "hybrid_order": ["soulseek"],
                               **_SEED_DEFAULTS}


def test_save_validates_and_roundtrips():
    db = _FakeDB()
    out = save(db, {"download_mode": "hybrid", "hybrid_order": ["torrent", "bogus", "torrent", "usenet"]})
    assert out == {"download_mode": "hybrid", "hybrid_order": ["torrent", "usenet"],
                   **_SEED_DEFAULTS}
    assert load(db) == out                                  # persisted + reloads identically


def test_save_ignores_absent_keys():
    db = _FakeDB()
    save(db, {"download_mode": "usenet"})
    assert load(db)["download_mode"] == "usenet"
    save(db, {"hybrid_order": ["soulseek", "torrent"]})     # mode key absent → unchanged
    assert load(db)["download_mode"] == "usenet"
    assert load(db)["hybrid_order"] == ["soulseek", "torrent"]


def test_the_config_module_stays_cheap_to_import():
    """It is read on the sidebar service-status path. An earlier version pulled
    seed_rules from core.torrent_clients, whose __init__ imports every client
    adapter + the config manager — 479 modules to normalise a dict."""
    import subprocess
    import sys
    code = ("import sys; before=len(sys.modules);"
            "import core.video.download_config;"
            "print(len(sys.modules)-before)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(_ROOT))
    pulled = int((out.stdout or "0").strip().splitlines()[-1])
    assert pulled < 120, "download_config now drags in %d modules" % pulled


from tests.support.settings_page import index_with_settings_page
_WEBUI = Path(__file__).resolve().parents[1] / "webui"


def test_saving_seed_settings_cannot_clobber_the_download_chain():
    """A real data-loss path, not a tidy-up.

    video-settings.js keeps _videoMode/_videoHybrid, refreshed only when that
    file loads. saveDownloads() used to post them on EVERY save - and it fires
    when you edit a folder path or any seeding field. The shared chain widget
    writes the chain straight to the same endpoint, so the sequence

        set the chain in the widget  ->  change a seed ratio

    re-posted the stale chain and silently undid the change. The endpoint only
    persists keys that are present (core/video/download_config.save), so the fix
    is for this saver to stop sending them and let the widget own them.
    """
    # the saver is React (video-downloads.ts downloadsBody, SETTINGS_PORT.md video pass V2a)
    js = (_WEBUI / "src" / "features" / "settings" / "tabs" / "video-downloads.ts").read_text(encoding="utf-8")
    body = js.split("export function downloadsBody(", 1)[1].split("\n}\n", 1)[0]
    assert "download_mode" not in body, "the saver still posts a possibly-stale download_mode"
    assert "hybrid_order" not in body, "the saver still posts a possibly-stale hybrid_order"
    # it must still save what it is actually for
    assert "seed_ratio_goal" in body and "download_path" in body


def test_the_retired_video_source_picker_is_gone():
    """Two editors for the video chain lived on one tab. The old select and its
    arrow list stayed in the DOM, hidden, while video-settings.js read them on
    load; nothing reads them now, so they went (SETTINGS_PORT.md video pass V3c)
    and the chain widget is the one editor. They must not come back.
    """
    index = (_WEBUI / "index.html").read_text(encoding="utf-8")
    assert "data-video-legacy-source" not in index, "the retired source picker is back"
    assert 'id="video-download-mode"' not in index and 'id="video-hybrid-rows"' not in index
    css = (_WEBUI / "static" / "style.css").read_text(encoding="utf-8")
    assert "[data-video-legacy-source]" not in css


def test_the_video_settings_live_with_their_subject_not_on_one_dumping_tab():
    """The video Downloads tab had become a dumping ground: download folders,
    the source picker, torrent seeding, import lists and notifications all under
    one heading, while the music side keeps each of those with its subject.

    Each moved to where its subject already lives:
      * download folders -> Library, because music's download-path and
        transfer-path are on Library
      * torrent seeding  -> Sources, inside the torrent client panel, because it
        configures what happens to a torrent after it finishes
      * import lists     -> Advanced, with the other background sync machinery

    A move is only safe while every id survives it - video-settings.js reads and
    writes these by id, and moving markup on this page has silently wiped real
    settings before. So this pins the tab each one landed on AND that the saver
    can still find every field it touches.
    """
    index = (_WEBUI / "index.html").read_text(encoding="utf-8")
    tabs = _WEBUI / "src" / "features" / "settings" / "tabs"

    # the download folders and the seeding are React (SETTINGS_PORT.md video
    # pass V2a): the folders in the Folders card's Video panel, on Library
    folders = (tabs / "library-video-folders.tsx").read_text(encoding="utf-8")
    assert "const TAB = 'library';" in folders and 'id="video-download-path"' in folders
    # what to hunt is a library choice, not a usenet one (Video Preferences,
    # React: SETTINGS_PORT.md video pass V2c)
    prefs = (tabs / "library-video-prefs.tsx").read_text(encoding="utf-8")
    assert "const TAB = 'library';" in prefs and 'id="video-monitor-specials"' in prefs
    # #1822: import lists add to what you discover (React, video pass V3c)
    lists = (tabs / "video-row-lists.tsx").read_text(encoding="utf-8")
    section = lists.split("export function ImportListsSection(", 1)[1]
    assert 'tab="discovery"' in section.split("</Section>", 1)[0]
    assert 'id="video-import-lists"' in section

    # seeding has to be INSIDE the torrent panel, not merely on the same tab
    panels = (tabs / "source-panels-more.tsx").read_text(encoding="utf-8")
    torrent = panels.split("export function TorrentPanel(", 1)[1].split("\nexport function", 1)[0]
    assert "<VideoSeedingGoals />" in torrent
    assert 'id="video-seeding-goals"' in (tabs / "video-source-blocks.tsx").read_text(encoding="utf-8")

    # the vanilla video saver that read fields by id is gone (SETTINGS_PORT.md
    # step V4c): each React section reads its own state, no id can be orphaned
    assert not (_WEBUI / "static" / "video" / "video-settings.js").exists()


def test_the_video_slskd_form_is_gone():
    """Nine fields on the video Downloads tab wrote the SAME nine config keys as
    the music Soulseek panel - soulseek.slskd_url, api_key, search_timeout and
    the rest - via /api/video/downloads/slskd. One slskd instance, two forms, on
    two different tabs, and the clobber this page has been bitten by twice:
    saveSlskd() posted EVERY field whenever any one changed.

    The Sources tab's Soulseek card is the single editor. The duplicate stayed
    hidden while video-settings.js read it; nothing does now, so it went
    (SETTINGS_PORT.md video pass V3c). It must not come back.
    """
    index = (_WEBUI / "index.html").read_text(encoding="utf-8")
    assert 'id="video-slskd-container"' not in index and "video-slskd-" not in index
    assert "data-dupe-of" not in index
    css = (_WEBUI / "static" / "style.css").read_text(encoding="utf-8")
    assert "[data-dupe-of]" not in css
    tabs_dir = _WEBUI / "src" / "features" / "settings" / "tabs"
    vjs = "".join(p.read_text(encoding="utf-8") for p in sorted(tabs_dir.glob("video-*.ts*"))
                  if not p.name.endswith((".test.ts", ".test.tsx")))
    assert "saveSlskd" not in vjs and "/api/video/downloads/slskd" not in vjs
    # the music Soulseek panel (React, SETTINGS_PORT.md step 7f) is the editor
    panel = (_WEBUI / "src" / "features" / "settings" / "tabs" / "source-panels.tsx").read_text(encoding="utf-8")
    for music in ("soulseek-url", "soulseek-api-key", "soulseek-search-timeout"):
        assert f'id="{music}"' in panel, music


def test_the_downloads_tab_holds_only_download_settings():
    """The video Downloads tab had become a dumping ground - folders, the source
    picker, seeding, import lists and notifications all under one heading. Each
    of those went to where its subject already lives, and what is left is four
    expandable cards that are all actually about downloading.

    Notifications in particular: being told when a download finishes is not a
    download setting, it is background machinery, so it sits in Advanced with
    import lists and logging.
    """
    # the Settings page is React (SETTINGS_PORT.md step V4c): read in its place
    index = index_with_settings_page()
    markup = re.sub(r"<!--.*?-->", "", index, flags=re.S)

    titles = []
    for m in re.finditer(r'<div class="settings-section-header[^"]*"[^>]*data-stg="downloads"[^>]*>', markup):
        t = re.search(r"<h3[^>]*>(.*?)</h3>", markup[m.end():m.end() + 900], re.S)
        if t:
            titles.append(re.sub(r"<[^>]+>", "", t.group(1)).strip())
    # Download Behaviour, Retry Logic, Album Publishing and Filtering are React
    # now (SETTINGS_PORT.md step 5): their cards are rendered from the tab, in
    # this order, into slots after Source Settings
    tab = (_WEBUI / "src" / "features" / "settings" / "tabs" / "downloads-tab.tsx").read_text(encoding="utf-8")
    # Source Settings (the download chain) is React too (step 7a), rendered
    # into the first slot, ahead of the others
    chain = (_WEBUI / "src" / "features" / "settings" / "tabs" / "sources-tab.tsx").read_text(encoding="utf-8")
    assert markup.index('data-stg-part="chain"') < markup.index('data-stg-part="behaviour"')
    titles += re.findall(r'settings-section-static" data-stg="downloads">\s*<h3>([^<]+)</h3>', chain)
    titles += re.findall(r'title="([^"]+)"', tab[tab.index("function Behaviour("):])
    # #1822: Filtering (explicit content, genre whitelist) decides what gets
    # downloaded, so it joined the download cards
    assert titles == ["Source Settings", "Download Behaviour", "Retry Logic", "Album Publishing",
                      "Filtering"], titles

    # the two that moved must not have crept back
    # #1822: notifications sit with the other connected services, import lists
    # on Discovery; neither belongs on Downloads
    # (both React: video-row-lists.tsx, SETTINGS_PORT.md video pass V3c)
    lists = (_WEBUI / "src" / "features" / "settings" / "tabs" / "video-row-lists.tsx").read_text(encoding="utf-8")
    for stray, section, home in (("video-notifications", "NotificationsSection", "connections"),
                                 ("video-import-lists", "ImportListsSection", "discovery")):
        body = lists.split(f"export function {section}(", 1)[1].split("\nexport function", 1)[0]
        assert f'id="{stray}"' in body
        assert f'tab="{home}"' in body, f"{stray} is not on the {home} tab"
        assert f'data-stg-react="{home}" data-stg-part=' in markup


def test_the_video_save_button_cannot_write_a_retired_form():
    """A live clobber, and a consequence of making the settings tabs shared.

    On the video side the Save Settings button first runs a CAPTURE-phase
    listener that flushes the video savers. (It used to stop the click there, so
    the music save never ran on the video side; it lets it through now - see
    test_the_video_save_button_saves_the_whole_page below.)

    Once the Sources tab was shared, this sequence lost data:

        1. on the video side, open Sources and change the Soulseek URL
        2. autosave stores it
        3. click Save Settings
        4. saveSlskd posts the RETIRED video slskd form, which still holds the
           value it loaded with, straight over the change from step 2

    The retired form has no business writing anything. Its change-listeners are
    gone for the same reason: hidden fields can only change from code, and any
    write from there races the music panel's own save.
    """
    tabs = _WEBUI / "src" / "features" / "settings" / "tabs"

    # every video section is React (SETTINGS_PORT.md video pass): the save
    # button's capture listener is video-shown.ts, and each section adds its
    # saver there. video-settings.js is gone, and nothing posts slskd
    assert not (_WEBUI / "static" / "video" / "video-settings.js").exists()
    savers = [p for p in tabs.glob("*.ts*") if "onVideoSave(" in p.read_text(encoding="utf-8")
              and not p.name.endswith((".test.tsx", ".test.ts")) and p.name != "video-shown.ts"]
    for p in savers:
        assert "/api/video/downloads/slskd" not in p.read_text(encoding="utf-8"), (
            f"{p.name} posts the retired slskd form from the video save button"
        )
    # the savers that legitimately belong to the video side stay
    server = (tabs / "video-server.ts").read_text(encoding="utf-8")
    assert "onVideoSave(() => Promise.all([videoServer.saveConn(true), videoServer.saveLibs(true)]));" in server
    # the downloads config is React and saves from the same click (video-downloads.ts)
    downloads = (_WEBUI / "src" / "features" / "settings" / "tabs" / "video-downloads.ts").read_text(encoding="utf-8")
    assert "onVideoSave(() => videoDownloads.save());" in downloads
    # Video Quality and YouTube Quality are React and add their own savers to
    # the same capture-phase click (SETTINGS_PORT.md, video pass V1)
    shown = (tabs / "video-shown.ts").read_text(encoding="utf-8")
    assert "if (!(event.target as Element | null)?.closest?.('#save-settings')) return;" in shown
    assert "true,\n  );" in shown  # capture phase
    quality = (tabs / "video-quality.tsx").read_text(encoding="utf-8")
    assert quality.count("useEffect(() => onVideoSave(() => save()), [])") == 2

    # and no video section saves the retired form's endpoint from a field
    sections = "".join(p.read_text(encoding="utf-8") for p in sorted(tabs.glob("video-*.ts*"))
                       if not p.name.endswith((".test.ts", ".test.tsx")))
    assert "saveSlskd" not in sections and "/api/video/downloads/slskd" not in sections, (
        "a retired hidden field can still trigger a save"
    )


def test_the_video_save_button_saves_the_whole_page():
    """The Settings page is shared, so most of what it shows on the video side
    - every music field on Sources, Downloads, Library and Quality, and all of
    System including Security & Access - has no saver but the music one.

    The video side's Save Settings handler used to call preventDefault and
    stopImmediatePropagation, so the music save never ran there, and it toasted
    "Settings saved" anyway. Reported live: tick OIDC only on the video side,
    Save, reload - unticked, and no POST /api/settings in the log.

    The capture listener must flush the video savers and let the click through
    to the button's own handler, and it must not claim a save of its own.
    """
    # the capture listener is video-shown.ts (SETTINGS_PORT.md video pass)
    shown = (_WEBUI / "src" / "features" / "settings" / "tabs" / "video-shown.ts").read_text(encoding="utf-8")
    handler = shown.split("?.closest?.('#save-settings')) return;", 1)[1]
    handler = handler.split("true,\n  );", 1)[0]
    for blocker in ("stopImmediatePropagation", "stopPropagation", "preventDefault"):
        assert blocker not in handler, f"the video save button still calls {blocker}"
    assert "'Settings saved'" not in handler, (
        "the video handler toasts a save it didn't make; the music handler owns the toast"
    )


def test_the_music_autosave_runs_on_the_video_side_for_shared_fields():
    """The other half of the same gap: debouncedAutoSaveSettings returned for
    EVERY edit on the video side, so a shared or System field changed there was
    never auto-saved either. Only a field the video side saves itself (inside a
    data-video-only block or wired by a data-video-* hook) may skip it - that
    one already saves through /api/video/*."""
    # every control is React and saves through the settings store, which has
    # no side in it; the page's own autosave went with settings.js
    # (SETTINGS_PORT.md step V4b). video-side-settings-save.test.ts runs it:
    # a Security and a Sources field save on the video side, and no video
    # section saves through it (each posts to /api/video/* itself)
    store = (_WEBUI / "src" / "features" / "settings" / "settings-store.ts").read_text(encoding="utf-8")
    assert "data-side" not in store, "the store stands down on one side"
    shell = (_WEBUI / "src" / "features" / "settings" / "shell.ts").read_text(encoding="utf-8")
    assert "debouncedAutoSaveSettings" not in shell


def test_the_ytdlp_card_loads_where_it_actually_lives():
    """The yt-dlp tile moved into the YouTube panel on the SOURCES tab, but its
    loader stayed wired to the Advanced tab - so the card sat on "Installed:
    Loading..." forever unless you happened to open Advanced first, which nobody
    does to read a version number.

    Predates this session; found while checking nothing else had come unhooked.

    The tile is React now, inside the YouTube panel (SETTINGS_PORT.md step 7g):
    the settings.js tab hook and the modal's call are gone, and the panel loads
    the version itself on arriving at Sources and on opening YouTube. Advanced
    loads nothing.
    """
    panels = (_WEBUI / "src" / "features" / "settings" / "tabs" / "source-panels-more.tsx").read_text(encoding="utf-8")
    youtube = panels.split("export function YoutubePanel(", 1)[1].split("\nexport function", 1)[0]
    assert "if (active && opened) void loadYtdlp();" in youtube, "the loader is no longer wired to the Sources tab"
    # and opening the YouTube card refreshes it
    assert "useOnOpen(model, 'youtube', () => void loadYtdlp());" in youtube
    js = (_WEBUI / "src" / "features" / "settings" / "shell.ts").read_text(encoding="utf-8")
    assert "loadYtdlpStatus" not in js, "settings.js still wires a loader for a tile it no longer has"



def test_redownload_failed_can_be_switched_off():
    db = _FakeDB()
    assert save(db, {"redownload_failed": False})["redownload_failed"] is False
    assert load(db)["redownload_failed"] is False
    assert save(db, {"redownload_failed": True})["redownload_failed"] is True


class _PolicyDB(_FakeDB):
    def __init__(self):
        super().__init__()
        self.applied = 0

    def apply_specials_policy(self, show_id=None):
        self.applied += 1
        return 0


def test_monitor_specials_saves_and_applies_only_on_a_change():
    db = _PolicyDB()
    assert load(db)["monitor_specials"] is True
    save(db, {"monitor_specials": True})          # unchanged: nothing re-applied
    assert db.applied == 0
    save(db, {"monitor_specials": False})
    assert load(db)["monitor_specials"] is False and db.applied == 1
    save(db, {"monitor_specials": False})         # every Downloads save re-posts it
    assert db.applied == 1
    save(db, {"monitor_specials": True})
    assert load(db)["monitor_specials"] is True and db.applied == 2
