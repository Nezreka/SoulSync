"""Guards that the audiobook subsystem cannot reach into music or video.

Podcasts had to be pulled back out of the music worker pool and the music
wishlist after the fact (cb6f1aeeb) because podcast batches were being picked up
by the music download engine. Audiobooks are born isolated instead, and these
tests are what keeps them that way as the subsystem grows: they read the
modules' real imports, calls and routes rather than trusting a comment.

The rules are not identical for every module, because the isolation that matters
is not "touch nothing shared".

  * Catalogue modules talk to Audible, which is metadata. They may not import an
    indexer client, a download client, or a throttle at all.
  * Acquisition modules talk to indexers and download clients, and they SHARE
    those with music and video on purpose — it is one Prowlarr in front of one
    set of indexers, and one qBittorrent. What they may never touch is the music
    side's own state: its batches, its worker pool, its wishlist, its database.

Every module in the subsystem is held to the second rule; the catalogue ones are
additionally held to the first.
"""

import ast
import os
from pathlib import Path

import pytest
from flask import Flask

from api.audiobooks import create_audiobooks_blueprint

_ROOT = Path(__file__).resolve().parents[1]

# Every module in the subsystem.
_ALL_MODULES = (
    "core/audiobook_client.py",
    "core/audiobook_database.py",
    "core/audiobook_download_monitor.py",
    "core/audiobook_grab.py",
    "core/audiobook_organizer.py",
    "core/audiobook_release_search.py",
    "core/audiobook_soulseek.py",
    "core/audiobook_wishlist_worker.py",
    "core/audiobook_completeness.py",
    "core/audiobook_post_processor.py",
    "core/audiobook_library_scan.py",
    "core/audiobook_watchlist.py",
    "api/audiobooks.py",
)

# The ones that must never touch an indexer or a download client at all.
_CATALOGUE_MODULES = ("core/audiobook_client.py",)

# Music-side state. Reaching any of this is how an isolated feature stops being
# isolated, and it is what actually broke podcasts.
_FORBIDDEN_STATE = (
    "database",                     # the music library database package
    "core.downloads",
    "core.download_engine",
    "core.download_orchestrator",
    "core.wishlist",
    "core.runtime_state",
    "core.matching_engine",
    "core.spotify_client",
    "core.plex_client",
    "core.jellyfin_client",
    "core.navidrome_client",
    "core.video",
    "core.podcast_download_client",
    "core.podcast_automation",
    "web_server",
)

# Shared acquisition infrastructure the catalogue side must stay away from.
_ACQUISITION_ONLY = (
    "core.prowlarr_client",
    "core.prowlarr_throttle",
    "core.slskd_throttle",
    "core.soulseek_client",
    "core.torrent_clients",
    "core.usenet_clients",
)


def _imports(relative_path):
    """Every module name imported anywhere in a file, including inside functions."""
    tree = ast.parse((_ROOT / relative_path).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _calls(relative_path):
    """Every called name in a file, by attribute or bare identifier."""
    tree = ast.parse((_ROOT / relative_path).read_text(encoding="utf-8"))
    return {
        getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }


def _blueprint_app():
    app = Flask(__name__)
    app.register_blueprint(create_audiobooks_blueprint())
    return app


# ---------------------------------------------------------------------------
# Nothing in the subsystem may touch music-side state
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("relative_path", _ALL_MODULES)
def test_no_module_imports_music_or_video_state(relative_path):
    for name in _imports(relative_path):
        for forbidden in _FORBIDDEN_STATE:
            assert not (name == forbidden or name.startswith(forbidden + ".")), (
                f"{relative_path} imports {name}; audiobooks must stay out of it"
            )


@pytest.mark.parametrize("relative_path", _ALL_MODULES)
def test_no_module_opens_the_music_or_video_database(relative_path):
    calls = _calls(relative_path)
    assert "get_database" not in calls
    assert "get_video_db" not in calls
    source = (_ROOT / relative_path).read_text(encoding="utf-8")
    assert "MusicDatabase" not in source


def test_the_subsystem_uses_its_own_database_file():
    from core.audiobook_database import DEFAULT_DB_PATH

    assert "audiobooks" in DEFAULT_DB_PATH
    assert "music_library" not in DEFAULT_DB_PATH
    assert "video_library" not in DEFAULT_DB_PATH


# ---------------------------------------------------------------------------
# The catalogue side may not reach acquisition at all
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("relative_path", _CATALOGUE_MODULES)
def test_the_catalogue_client_touches_no_indexer_or_downloader(relative_path):
    # Audible is a metadata service. Spending an indexer slot on a browse
    # request would slow real downloads for nothing.
    for name in _imports(relative_path):
        for forbidden in _ACQUISITION_ONLY:
            assert not name.startswith(forbidden), f"{relative_path} imports {name}"


@pytest.mark.parametrize("relative_path", _CATALOGUE_MODULES)
def test_the_catalogue_client_spends_no_shared_search_slot(relative_path):
    calls = _calls(relative_path)
    for reserved in ("reserve_search_slot", "wait_for_slot", "note_rate_limited"):
        assert reserved not in calls, f"{relative_path} calls {reserved}"


# ---------------------------------------------------------------------------
# Acquisition shares the indexer budget on purpose
# ---------------------------------------------------------------------------

def test_release_search_goes_through_the_shared_prowlarr_client():
    # Not its own HTTP client: the shared one is what carries the throttle, and
    # an audiobook wishlist drain must not be able to out-shout a music one.
    assert "core.prowlarr_client" in _imports("core/audiobook_release_search.py")


def test_grabs_go_through_the_shared_download_clients():
    imported = _imports("core/audiobook_grab.py")
    assert "core.torrent_clients" in imported
    assert "core.usenet_clients" in imported


def test_release_search_asks_only_for_the_audiobook_category():
    from core.audiobook_release_search import AUDIOBOOK_CATEGORY
    from core.prowlarr_client import MUSIC_CATEGORY_AUDIOBOOK

    # 3030 is the category core/prowlarr_client.py already keeps OUT of music
    # searches, so using it here costs the music side nothing.
    assert AUDIOBOOK_CATEGORY == MUSIC_CATEGORY_AUDIOBOOK


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def test_every_route_lives_under_the_audiobooks_prefix():
    rules = [r for r in _blueprint_app().url_map.iter_rules() if r.endpoint != "static"]
    assert rules, "expected the blueprint to register routes"
    for rule in rules:
        assert str(rule).startswith("/api/audiobooks/"), f"{rule} escapes the prefix"


def test_only_the_acquisition_routes_write_anything():
    # A write appearing anywhere else means state landed somewhere that is
    # supposed to be read-only.
    allowed_writers = {"/api/audiobooks/wishlist", "/api/audiobooks/wishlist/<asin>",
                       "/api/audiobooks/wishlist/search", "/api/audiobooks/grab",
                       # Following an author is acquisition too: it is what
                       # feeds the wishlist without the user asking again.
                       "/api/audiobooks/watchlist", "/api/audiobooks/watchlist/<path:name>",
                       "/api/audiobooks/watchlist/scan",
                       # A search job is a POST that persists NOTHING: it lives
                       # in process memory for a few minutes so a modal can show
                       # results as they land. Allowed here, and pinned below to
                       # stay that way.
                       "/api/audiobooks/releases/<asin>/start",
                       "/api/audiobooks/releases/poll",
                       # POST only because it carries a release payload too big
                       # for a query string. It reads a .torrent or NZB into
                       # memory and returns the file list; nothing is enqueued
                       # and nothing is stored. Pinned below.
                       "/api/audiobooks/releases/contents",
                       # Blocking a release is acquisition: it is how the
                       # wishlist is told to stop fetching one.
                       "/api/audiobooks/blocklist",
                       "/api/audiobooks/blocklist/<path:key>",
                       # Deleting a book and putting one back are the two
                       # library writes. Both move files, neither touches a
                       # music path.
                       "/api/audiobooks/library/<asin>", "/api/audiobooks/library/<asin>/match",
                       "/api/audiobooks/library/recycle/<path:name>",
                       "/api/audiobooks/library/recycle"}
    for rule in _blueprint_app().url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        writes = rule.methods - {"GET", "HEAD", "OPTIONS"}
        if writes:
            assert str(rule) in allowed_writers, f"{rule} exposes {sorted(writes)}"


def test_the_catalogue_routes_are_still_read_only():
    read_only = ("/api/audiobooks/search", "/api/audiobooks/home",
                 "/api/audiobooks/categories", "/api/audiobooks/person")
    for rule in _blueprint_app().url_map.iter_rules():
        if str(rule) in read_only:
            assert rule.methods - {"GET", "HEAD", "OPTIONS"} == set()


def test_registering_audiobooks_does_not_disturb_podcasts():
    from api.podcasts import create_podcasts_blueprint

    app = Flask(__name__)
    app.register_blueprint(create_podcasts_blueprint())
    app.register_blueprint(create_audiobooks_blueprint())

    prefixes = {}
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        prefixes.setdefault(rule.endpoint.split(".")[0], set()).add(str(rule).split("/")[2])
    assert prefixes["podcasts_api"] == {"podcasts"}
    assert prefixes["audiobooks_api"] == {"audiobooks"}


def test_web_server_registers_the_blueprint():
    # A blueprint nobody registers is a feature that silently does not exist.
    source = (_ROOT / "web_server.py").read_text(encoding="utf-8", errors="ignore")
    assert "create_audiobooks_blueprint" in source
    assert "app.register_blueprint(_create_audiobooks_blueprint())" in source


# ---------------------------------------------------------------------------
# The background worker
# ---------------------------------------------------------------------------

def test_the_wishlist_worker_holds_no_lock_the_music_side_takes():
    # An async callback taking tasks_lock is what wedged every download once
    # before; the audiobook worker must never be able to reach that lock.
    source = (_ROOT / "core/audiobook_wishlist_worker.py").read_text(encoding="utf-8")
    assert "tasks_lock" not in source
    assert "download_batches" not in source
    assert "download_tasks" not in source


# ---------------------------------------------------------------------------
# An install that never uses audiobooks must be untouched by them
# ---------------------------------------------------------------------------

def test_the_database_reports_whether_the_subsystem_has_been_used(tmp_path):
    from core.audiobook_database import AudiobookDatabase, subsystem_in_use

    path = str(tmp_path / "audiobooks.db")
    assert subsystem_in_use(path) is False
    AudiobookDatabase(path)
    assert subsystem_in_use(path) is True


def test_the_wishlist_has_no_thread_of_its_own():
    """Draining the wishlist is a system automation, not a private timer.

    An automation is visible on the Automations page, can be paused,
    rescheduled or run by hand, and obeys the master switch. The first cut of
    this shipped a daemon thread instead, which was none of those.
    """
    import core.audiobook_wishlist_worker as module

    assert not hasattr(module, "ensure_started")
    assert not hasattr(module, "AudiobookWishlistWorker")
    assert "threading.Thread" not in (_ROOT / "core/audiobook_wishlist_worker.py").read_text(
        encoding="utf-8",
    )


def test_the_wishlist_automation_is_seeded_and_handled():
    # A seeded automation with no handler fires and does nothing forever; a
    # handler with no seeded automation never fires at all.
    from core.automation.handlers import auto_process_audiobook_wishlist
    from core.audiobook_wishlist_worker import AUTOMATION_ACTION
    from core.automation_engine import SYSTEM_AUTOMATIONS

    assert AUTOMATION_ACTION in {spec["action_type"] for spec in SYSTEM_AUTOMATIONS}
    assert callable(auto_process_audiobook_wishlist)


def test_the_wishlist_automation_is_not_hidden_from_the_music_page():
    # The podcast scan carries no owned_by either: audio-side automations belong
    # on the same page as music, and tagging this one would need new filtering
    # logic on a page audiobooks should not be touching.
    from core.automation_engine import SYSTEM_AUTOMATIONS

    spec = next(s for s in SYSTEM_AUTOMATIONS
                if s["action_type"] == "audiobook_process_wishlist")
    assert "owned_by" not in spec


def test_the_download_monitor_stays_asleep_on_an_unused_install():
    from unittest.mock import patch

    from core.audiobook_download_monitor import ensure_started, get_monitor

    get_monitor().stop(timeout=2)
    with patch("core.audiobook_database.subsystem_in_use", return_value=False):
        assert ensure_started() is False
    assert get_monitor().running is False


def test_wanting_a_book_starts_no_thread():
    """Adding to the wishlist writes a row and nothing else.

    It used to force-start a background searcher. The automation engine owns
    that now, so an HTTP handler starting threads would be both redundant and a
    way to spawn work no one can see or stop.
    """
    source = (_ROOT / "api/audiobooks.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "wishlist_add":
            body = ast.dump(node)
            assert "audiobook_wishlist_worker" not in body
            assert "ensure_started" not in body
            return
    raise AssertionError("wishlist_add route not found")


def test_grabbing_wakes_the_download_monitor():
    source = (_ROOT / "api/audiobooks.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "grab":
            body = ast.dump(node)
            assert "audiobook_download_monitor" in body
            assert "ensure_started" in body
            return
    raise AssertionError("grab route not found")


def test_boot_starts_only_the_download_monitor():
    """The download monitor is a poller, so it is a thread — matching the video
    side's own download monitor. The wishlist is a scheduled job, so it is an
    automation. Boot starts the first and not the second.
    """
    source = (_ROOT / "web_server.py").read_text(encoding="utf-8", errors="ignore")
    assert "_ensure_audiobook_downloads()" in source
    assert "_ensure_audiobook_wishlist" not in source
    assert "_ensure_audiobook_downloads(force=True)" not in source


# ---------------------------------------------------------------------------
# Soulseek, shared with music
# ---------------------------------------------------------------------------

def test_the_orphan_reaper_cannot_reach_a_downloading_book(tmp_path):
    """A book downloading from a peer lands in slskd's per-share subfolder.

    The music side's orphan reaper sweeps the SAME download root for audio
    nothing claims, and an audiobook transfer is claimed by no music engine
    record. It survives only because the reaper is root-level and a peer's
    files arrive in a folder. If that ever became recursive, every in-flight
    book would be deleted an hour after it started, so it is pinned here.
    """
    from core.downloads.cleanup import sweep_orphaned_download_audio

    book = tmp_path / "Project Hail Mary [Ray Porter]"
    book.mkdir()
    chapter = book / "01 - Chapter.mp3"
    chapter.write_bytes(b"x" * 1000)
    os.utime(chapter, (0, 0))

    loose = tmp_path / "orphan.mp3"
    loose.write_bytes(b"x" * 1000)
    os.utime(loose, (0, 0))

    removed = sweep_orphaned_download_audio(str(tmp_path))

    assert chapter.is_file(), "the reaper reached inside a Soulseek folder"
    assert [str(loose)] == removed


def test_a_soulseek_book_is_never_handed_to_the_music_download_engine():
    # The book's transfers are followed by the audiobook monitor alone. Going
    # through the orchestrator would put a book in a music batch.
    imported = _imports("core/audiobook_soulseek.py")
    assert "core.download_orchestrator" not in imported
    assert "core.download_engine" not in imported
    assert "core.downloads" not in imported


def test_the_soulseek_source_reads_only_the_audiobook_chain():
    # Music's source chain must never decide what a book search does.
    source = (_ROOT / "core/audiobook_soulseek.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        if "config_manager.get(" in line:
            assert "audiobooks." in line, line.strip()


def test_books_download_to_the_universal_folder_not_the_library():
    """In-progress books must not land in the finished library.

    The settings page fills library.audiobooks_path and
    audiobooks.download_path from ONE input, so passing the latter as a client
    save_path meant downloading into the library and then copying into a
    subfolder of it: every book on disk twice, half-finished ones on the
    shelf. Video has always passed nothing here.
    """
    from core.audiobook_grab import audiobook_download_path

    assert audiobook_download_path() is None


def test_the_audiobook_disk_guard_measures_the_disk_that_fills_up():
    # Audiobooks download to the universal folder, so the question "is there
    # room" is the one music already asks. A separate probe was measuring a
    # volume nothing downloads to.
    source = (_ROOT / "core/audiobook_grab.py").read_text(encoding="utf-8")
    assert "music_has_room" in source


def test_the_library_root_is_still_separate_from_the_download_folder():
    # The fix must not have collapsed the two: books are ORGANIZED into the
    # audiobook library, which is not where they download.
    organizer = (_ROOT / "core/audiobook_organizer.py").read_text(encoding="utf-8")
    assert "library.audiobooks_path" in organizer

    # The grab side must not READ any library path. Checked on the parsed
    # calls, not the text, so the docstring explaining this can mention it.
    grab = ast.parse((_ROOT / "core/audiobook_grab.py").read_text(encoding="utf-8"))
    reads = [
        node.args[0].value
        for node in ast.walk(grab)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
        and node.args and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]
    assert not [key for key in reads if "path" in key], reads


def test_a_search_job_persists_nothing():
    """The one POST in the subsystem that is allowed to write no state.

    It exists so a modal can render results as they arrive rather than sitting
    blank through three fan-outs. If it ever starts touching a database it
    stops being a view concern, and the read-only contract above quietly
    becomes a lie.
    """
    imported = _imports("core/audiobook_search_job.py")
    assert "core.audiobook_database" not in imported
    assert "sqlite3" not in imported

    source = (_ROOT / "core/audiobook_search_job.py").read_text(encoding="utf-8")
    assert "get_audiobook_db" not in source


def test_a_search_job_cannot_outlive_the_page_that_started_it():
    # A wedged indexer must not leave a thread running for the life of the
    # process, and finished jobs must not accumulate.
    from core.audiobook_search_job import JOB_MAX_SECONDS, JOB_TTL_SECONDS

    assert 0 < JOB_MAX_SECONDS <= 600
    assert 0 < JOB_TTL_SECONDS <= 3600

    source = (_ROOT / "core/audiobook_search_job.py").read_text(encoding="utf-8")
    assert "daemon=True" in source


def test_reading_a_release_stores_nothing():
    """The contents preview is a read, despite being a POST.

    It fetches the .torrent or NZB the indexer already offers and decodes it in
    memory. If it ever starts writing, the read-only contract above becomes a
    lie and a preview could enqueue something nobody asked for.
    """
    imported = _imports("core/audiobook_release_contents.py")
    assert "core.audiobook_database" not in imported

    source = (_ROOT / "core/audiobook_release_contents.py").read_text(encoding="utf-8")
    for forbidden in ("get_audiobook_db", "add_torrent", "add_nzb", "grab_release"):
        assert forbidden not in source, forbidden


def test_reading_a_release_never_starts_a_download():
    # It shares the torrent FETCHER with the grab path but must not touch the
    # part that hands anything to a client.
    imported = _imports("core/audiobook_release_contents.py")
    assert "core.audiobook_grab" not in imported
