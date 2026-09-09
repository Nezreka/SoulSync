"""Audiobooks and podcasts under the profile system.

Three separate gaps, all of which meant one profile's choices leaked into
everyone's:

  * Neither page could be granted or denied. They are nav pages with no
    checkbox in the profile editor, so they could never appear in
    allowed_pages — and a profile given ANY restricted list lost them outright.
  * The audiobook API never read a profile at all, so every profile wrote into
    profile 1's wishlist, watchlist and blocklist.
  * The timed automations swept profile 1 only, so a second person's wishlist
    was never searched and their followed authors never checked.

The LIBRARY and the download queue are deliberately NOT per profile: one
filesystem, one download client, so a book on disk is on disk for everybody.
"""

from pathlib import Path

import pytest
from flask import Flask

from core.audiobook_database import AudiobookDatabase

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db(tmp_path):
    database = AudiobookDatabase(str(tmp_path / "audiobooks.db"))
    yield database
    database.close()


def _book(asin="B1", title="Project Hail Mary"):
    return {"asin": asin, "title": title, "author_names": ["Andy Weir"],
            "narrator_names": ["Ray Porter"], "series": [], "runtime_minutes": 970}


def _read(rel):
    return (_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# The admin can grant the pages at all
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("page", ["podcasts", "audiobooks"])
def test_the_page_can_be_granted_to_a_profile(page):
    """Both are nav pages. Without a checkbox they could never be listed in
    allowed_pages, so restricting a profile to ANY set of pages hid them with
    no way for an admin to grant them back."""
    index = _read("webui/index.html")
    assert f'<input type="checkbox" value="{page}"' in index


@pytest.mark.parametrize("page", ["podcasts", "audiobooks"])
def test_the_page_is_actually_reachable(page):
    # A permission for a page that does not exist would be worse than none.
    index = _read("webui/index.html")
    assert f'data-page="{page}"' in index


def test_the_pages_sit_with_the_rest_of_the_audio_side():
    # Not under Video, where an admin would never look for them.
    index = _read("webui/index.html")
    block = index.split('id="new-profile-allowed-pages"', 1)[1].split("Video", 1)[0]
    assert 'value="podcasts"' in block
    assert 'value="audiobooks"' in block


# ---------------------------------------------------------------------------
# Reading lists belong to a profile
# ---------------------------------------------------------------------------

def test_two_profiles_keep_separate_wishlists(db):
    db.add_to_wishlist(_book("A"), profile_id=1)
    db.add_to_wishlist(_book("B"), profile_id=2)

    assert [r["asin"] for r in db.get_wishlist(1)] == ["A"]
    assert [r["asin"] for r in db.get_wishlist(2)] == ["B"]


def test_two_profiles_keep_separate_followed_authors(db):
    db.follow_author("Andy Weir", profile_id=1)
    db.follow_author("Brandon Sanderson", profile_id=2)

    assert [a["name"] for a in db.get_watchlist(1)] == ["Andy Weir"]
    assert [a["name"] for a in db.get_watchlist(2)] == ["Brandon Sanderson"]


def test_two_profiles_keep_separate_blocklists(db):
    db.block_release({"guid": "a"}, profile_id=1)
    db.block_release({"guid": "b"}, profile_id=2)

    assert db.blocked_keys(1) == {"a"}
    assert db.blocked_keys(2) == {"b"}


def test_the_library_is_shared(db):
    """One filesystem. A book on disk is on disk for everyone, so ownership is
    deliberately NOT per profile — otherwise the same book would be downloaded
    once per person."""
    db.add_to_library(_book("A"), "/books/A")
    assert db.is_owned("A") is True
    assert "A" in db.owned_asins()


def test_the_api_reads_the_profile_from_the_request():
    api = _read("api/audiobooks.py")
    assert "def _profile()" in api
    assert "parse_profile_id" in api
    # Every scoped call site takes it.
    for call in ("get_wishlist(_profile())", "get_watchlist(_profile())",
                 "get_blocklist(_profile())", "profile_id=_profile()"):
        assert call in api, call


def test_the_frontend_sends_the_profile():
    client = _read("webui/src/routes/audiobooks/-audiobooks.api.ts")
    assert "X-Profile-Id" in client
    assert "getShellProfileContext" in client


def test_the_shared_api_client_is_left_alone():
    """Music and video use it too.

    The header goes on an audiobook-only client, so this cannot change a single
    request the rest of the app makes.
    """
    shared = _read("webui/src/app/api-client.ts")
    assert "X-Profile-Id" not in shared
    client = _read("webui/src/routes/audiobooks/-audiobooks.api.ts")
    assert "apiClient.extend(" in client


# ---------------------------------------------------------------------------
# The automations serve everyone
# ---------------------------------------------------------------------------

def test_every_profile_with_rows_is_found(db):
    db.add_to_wishlist(_book("A"), profile_id=1)
    db.follow_author("Brandon Sanderson", profile_id=3)
    assert db.profiles_with_rows() == [1, 3]


def test_an_empty_database_still_names_one_profile(db):
    # A pass with no profiles at all would be a silent no-op.
    assert db.profiles_with_rows() == [1]


def test_the_wishlist_pass_sweeps_every_profile():
    worker = _read("core/audiobook_wishlist_worker.py")
    assert "profiles_with_rows()" in worker
    assert "profile_id=profile_id" in worker


def test_the_author_scan_sweeps_every_profile():
    scan = _read("core/audiobook_watchlist.py")
    assert "profiles_with_rows()" in scan
    assert "profile_id=profile_id" in scan


def test_a_profile_listing_failure_still_serves_the_default():
    # A broken lookup must not stop the pass entirely.
    for rel in ("core/audiobook_wishlist_worker.py", "core/audiobook_watchlist.py"):
        assert "profiles = [1]" in _read(rel)


# ---------------------------------------------------------------------------
# Podcasts
# ---------------------------------------------------------------------------

def test_the_podcast_api_accepts_the_profile_header():
    api = _read("api/podcasts.py")
    assert "def _profile()" in api
    assert "parse_profile_id" in api


def test_an_explicit_podcast_profile_still_wins():
    # Callers that already pass one must not change behaviour.
    api = _read("api/podcasts.py")
    assert 'request.args.get("profile_id") or _profile()' in api


def test_the_podcast_api_no_longer_falls_back_to_profile_one():
    api = _read("api/podcasts.py")
    block = api.split("def create_podcasts_blueprint(", 1)[1]
    assert 'request.args.get("profile_id", 1)' not in block


# ---------------------------------------------------------------------------
# Downloads answer to the profile's "Can download" switch
# ---------------------------------------------------------------------------

class _Profiles:
    """Stands in for the music database's profile table."""

    def __init__(self, rows):
        self._rows = rows

    def get_profile(self, pid):
        return self._rows.get(pid)


def _permission(monkeypatch, rows, profile_id):
    import database.music_database as music_db
    from api.helpers import download_permission_error

    monkeypatch.setattr(music_db, "get_database", lambda *a, **k: _Profiles(rows))
    # the refusal is a jsonify response, which needs an app to render into
    with Flask(__name__).app_context():
        return download_permission_error(profile_id)


def test_a_profile_with_downloads_off_is_refused(monkeypatch):
    denied = _permission(monkeypatch, {2: {"can_download": False}}, 2)
    assert denied is not None
    _body, status = denied
    assert status == 403


def test_a_profile_with_downloads_on_is_allowed(monkeypatch):
    assert _permission(monkeypatch, {2: {"can_download": True}}, 2) is None


def test_an_older_profile_row_without_the_column_is_allowed(monkeypatch):
    """Backwards compatibility: rows written before can_download existed have
    no such key, and those people could already download. Absence must read as
    yes, never as no."""
    assert _permission(monkeypatch, {2: {"name": "Kid"}}, 2) is None


def test_the_admin_is_never_refused(monkeypatch):
    # Profile 1 is the admin. Not even a bad row can lock them out.
    assert _permission(monkeypatch, {1: {"can_download": False}}, 1) is None


def test_a_broken_lookup_fails_open(monkeypatch):
    """A permission check that cannot read the profile must not take away
    downloads from somebody who has them — the music side fails open too."""
    import database.music_database as music_db
    from api.helpers import download_permission_error

    def _explode(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(music_db, "get_database", _explode)
    with Flask(__name__).app_context():
        assert download_permission_error(2) is None


def test_a_missing_profile_is_allowed(monkeypatch):
    # No row is not the same as a row that says no.
    assert _permission(monkeypatch, {}, 2) is None


def test_the_audiobook_grab_asks_before_spending_the_download_client():
    api = _read("api/audiobooks.py")
    grab = api.split('@bp.route("/grab"', 1)[1]
    assert "_download_denied()" in grab.split("def ", 2)[1]


def test_a_manual_wishlist_pass_asks_too():
    # It grabs what it finds, so it is a download action like any other.
    api = _read("api/audiobooks.py")
    passes = api.split('@bp.route("/wishlist/search"', 1)[1].split("@bp.route", 1)[0]
    assert "_download_denied()" in passes


def test_the_podcast_download_asks_too():
    api = _read("api/podcasts.py")
    route = api.split('@bp.route("/download"', 1)[1].split("@bp.route", 1)[0]
    assert "download_permission_error(_profile())" in route


def test_the_catalogue_stays_open_to_everyone():
    """Browsing is not downloading. A profile that cannot download can still
    search, read and sample — taking that away would be a bigger change than
    the switch asks for."""
    api = _read("api/audiobooks.py")
    search = api.split('@bp.route("/search"', 1)[1].split("@bp.route", 1)[0]
    assert "_download_denied" not in search


def test_the_download_buttons_carry_the_hook_the_css_hides():
    """The music and video sides hide download buttons by class name. React
    hashes its class names, so both pages need the attribute the stylesheet
    was taught to look for — otherwise the button stays visible and only the
    API refuses, which reads as a broken button."""
    css = _read("webui/static/style.css")
    assert "body.downloads-disabled [data-download-action]" in css
    for rel in ("webui/src/routes/audiobooks/-ui/audiobook-releases-modal.tsx",
                "webui/src/routes/podcasts/-ui/podcast-episode-list.tsx",
                "webui/src/routes/podcasts/-ui/podcast-show-notes-modal.tsx"):
        assert "data-download-action" in _read(rel), rel


def test_the_permission_label_names_what_it_covers():
    # One switch, four media types. The label used to promise two.
    index = _read("webui/index.html")
    assert "Can download (music, podcasts, audiobooks &amp; video)" in index
    init = _read("webui/static/init.js")
    assert "Can download (music, podcasts, audiobooks & video)" in init
