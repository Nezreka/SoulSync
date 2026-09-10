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
                  "seed_overrides": {}}


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
    js = (_WEBUI / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")
    body = js.split("function saveDownloads(", 1)[1].split("})", 1)[0]
    assert "download_mode:" not in body, "saveDownloads still posts a possibly-stale download_mode"
    assert "hybrid_order:" not in body, "saveDownloads still posts a possibly-stale hybrid_order"
    # it must still save what it is actually for
    assert "seed_ratio_goal" in body and "download_path" in body


def test_the_retired_video_source_picker_stays_hidden():
    """Two editors for the video chain lived on one tab. The old select and its
    arrow list stay in the DOM - video-settings.js reads them on load - but they
    must never render, or they drift against the widget again.

    The rule needs !important twice over: updateVideoSourceUI() writes
    style.display on the hybrid container, and .form-group sets display:flex,
    which alone beats the [hidden] attribute.
    """
    index = (_WEBUI / "index.html").read_text(encoding="utf-8")
    assert index.count("data-video-legacy-source") == 3, "the legacy markers moved"
    for m in re.finditer(r"<[^>]*data-video-legacy-source[^>]*>", index):
        assert "hidden" in m.group(0), "a legacy source control lost its hidden attribute"

    css = (_WEBUI / "static" / "style.css").read_text(encoding="utf-8")
    rule = re.search(r"#settings-page \[data-video-legacy-source\]\s*{([^}]*)}", css)
    assert rule, "no rule hiding the retired picker"
    assert "display: none" in rule.group(1) and "!important" in rule.group(1)


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

    for pid, tab in (("video-download-path", "library"),
                     ("video-seeding-goals", "sources"),
                     ("video-import-lists", "advanced")):
        at = index.index(f'id="{pid}"')
        stg = index.rfind('data-stg=', 0, at)
        found = re.search(r'data-stg="([a-z]+)"', index[stg:stg + 30]).group(1)
        assert found == tab, f"{pid} is on the {found} tab, expected {tab}"

    # seeding has to be INSIDE the torrent panel, not merely on the same tab
    assert index.index('id="video-seeding-goals"') > index.index('id="torrent-client-settings-container"')

    # nothing the video saver reads may have been left behind by a move
    js = (_WEBUI / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")
    touched = {a or b for a, b in re.findall(r"getElementById\('([^']+)'\)|val\('([^']+)'\)", js)}
    orphans = [i for i in sorted(touched) if i.startswith(("video-", "vq-")) and f'id="{i}"' not in index]
    assert not orphans, f"the move orphaned these fields: {orphans}"


def test_the_video_slskd_form_is_a_retired_duplicate():
    """Nine fields on the video Downloads tab wrote the SAME nine config keys as
    the music Soulseek panel - soulseek.slskd_url, api_key, search_timeout and
    the rest - via /api/video/downloads/slskd. One slskd instance, two forms, on
    two different tabs, in two different styles.

    It also carried the clobber this page has been bitten by twice now:
    saveSlskd() posts EVERY field whenever any one changes, so changing a search
    timeout on the video side re-posted whatever URL that form happened to be
    holding - undoing a change made from the music side.

    The Sources tab is shared and renders on the video side, so the Soulseek card
    there is the single editor. The fields stay in the DOM (video-settings.js
    reads them) but must never render. This also pins the 1:1 mapping, so the day
    a field exists on one side only, this fails instead of quietly hiding it.
    """
    index = (_WEBUI / "index.html").read_text(encoding="utf-8")
    at = index.index('id="video-slskd-container"')
    tag = index[index.rfind("<div", 0, at):index.index(">", at) + 1]
    assert 'data-dupe-of="soulseek-settings-container"' in tag
    assert "hidden" in tag

    css = (_WEBUI / "static" / "style.css").read_text(encoding="utf-8")
    rule = re.search(r"#settings-page \[data-dupe-of\]\s*{([^}]*)}", css)
    assert rule and "display: none" in rule.group(1) and "!important" in rule.group(1)

    # every video field must still have a music counterpart, or hiding it loses a setting
    vjs = (_WEBUI / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")
    mjs = (_WEBUI / "static" / "settings.js").read_text(encoding="utf-8")
    video = {i for i in re.findall(r"'(video-slskd-[a-z-]+)'", vjs) if not i.endswith("container")}
    alias = {"search-min-delay": "search-min-delay-seconds", "auto-clear": "auto-clear-searches"}
    for vid in sorted(video):
        stem = vid[len("video-slskd-"):]
        music = "soulseek-" + alias.get(stem, stem)
        assert f"'{music}'" in mjs, f"{vid} has no music counterpart ({music}) - hiding it loses that setting"


def test_the_downloads_tab_holds_only_download_settings():
    """The video Downloads tab had become a dumping ground - folders, the source
    picker, seeding, import lists and notifications all under one heading. Each
    of those went to where its subject already lives, and what is left is four
    expandable cards that are all actually about downloading.

    Notifications in particular: being told when a download finishes is not a
    download setting, it is background machinery, so it sits in Advanced with
    import lists and logging.
    """
    index = (_WEBUI / "index.html").read_text(encoding="utf-8")
    markup = re.sub(r"<!--.*?-->", "", index, flags=re.S)

    titles = []
    for m in re.finditer(r'<div class="settings-section-header[^"]*"[^>]*data-stg="downloads"[^>]*>', markup):
        t = re.search(r"<h3[^>]*>(.*?)</h3>", markup[m.end():m.end() + 900], re.S)
        if t:
            titles.append(re.sub(r"<[^>]+>", "", t.group(1)).strip())
    assert titles == ["Source Settings", "Download Behaviour", "Retry Logic", "Album Publishing"], titles

    # the two that moved must not have crept back
    for stray in ("video-notifications", "video-import-lists"):
        at = markup.index(f'id="{stray}"')
        stg = markup.rfind('data-stg="', 0, at)
        tab = re.search(r'data-stg="([a-z]+)"', markup[stg:stg + 32]).group(1)
        assert tab == "advanced", f"{stray} is back on the {tab} tab"


def test_the_video_save_button_cannot_write_a_retired_form():
    """A live clobber, and a consequence of making the settings tabs shared.

    On the video side the Save Settings button is a CAPTURE-phase listener that
    calls stopImmediatePropagation, so only the video savers run - the music
    save, a bubble-phase listener on the button itself, never fires. That was
    harmless while the video side could only show video settings.

    Now the Sources tab is shared, so this sequence lost data:

        1. on the video side, open Sources and change the Soulseek URL
        2. autosave stores it
        3. click Save Settings
        4. saveSlskd posts the RETIRED video slskd form, which still holds the
           value it loaded with, straight over the change from step 2

    The retired form has no business writing anything. Its change-listeners are
    gone for the same reason: hidden fields can only change from code, and any
    write from there races the music panel's own save.
    """
    js = (_WEBUI / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")

    chain = js.split("Promise.all([", 1)[1].split("])", 1)[0]
    assert "saveSlskd" not in chain, (
        "the video save button posts the retired slskd form again"
    )
    # the savers that legitimately belong to the video side stay
    for keep in ("saveConn", "saveDownloads", "saveQuality", "saveYtQuality"):
        assert keep in chain, f"{keep} was dropped from the video save chain"

    wiring = js.split("function wireSlskd", 1)[-1][:1200] if "function wireSlskd" in js else js
    assert "addEventListener('change', function () { saveSlskd(true); })" not in js, (
        "a retired hidden field can still trigger a save"
    )


def test_the_ytdlp_card_loads_where_it_actually_lives():
    """The yt-dlp tile moved into the YouTube panel on the SOURCES tab, but its
    loader stayed wired to the Advanced tab - so the card sat on "Installed:
    Loading..." forever unless you happened to open Advanced first, which nobody
    does to read a version number.

    Predates this session; found while checking nothing else had come unhooked.
    """
    js = (_WEBUI / "static" / "settings.js").read_text(encoding="utf-8")
    trigger = re.search(r"if \(\(?tab === [^)]*\)? && typeof loadYtdlpStatus", js)
    assert trigger, "the yt-dlp loader is no longer wired to a tab"
    assert "'sources'" in trigger.group(0), (
        "the loader still only fires on Advanced, where the tile no longer is"
    )
    # and opening the YouTube card refreshes it
    opener = js.split("function openSourceModal(", 1)[1].split("\n}", 1)[0]
    assert "loadYtdlpStatus" in opener

