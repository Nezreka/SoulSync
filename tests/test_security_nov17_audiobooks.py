"""downloads off means no audiobook downloads, nov 17 (S5) + the owner check
the background passes share with the youtube drain and scans (S9).

a wished or followed book is grabbed by the wishlist pass, so wanting one is
a download. audiobooks have no request flow yet: a profile with downloads off
is refused, and the passes (which run as the system) ask about each row's
owner at grab time, so a switch turned off after the add still counts.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from core.audiobook_database import STATUS_WANTED, AudiobookDatabase
from core.permissions import owner_may_download
from tests.test_audiobook_wishlist_worker import _book, _release

PROFILES = {
    2: {"id": 2, "name": "Kid", "can_download": False},
    3: {"id": 3, "name": "Member", "can_download": True},
    4: {"id": 4, "name": "Second admin", "is_admin": True, "can_download": False},
}


class _Profiles:
    def get_profile(self, pid):
        return PROFILES.get(pid)


@pytest.fixture
def profiles(monkeypatch):
    import database.music_database as music_db
    monkeypatch.setattr(music_db, "get_database", lambda *a, **k: _Profiles())


@pytest.fixture
def db(tmp_path):
    d = AudiobookDatabase(str(tmp_path / "audiobooks.db"))
    yield d
    d.close()


# ── the owner check ──────────────────────────────────────────────────────────

def test_owner_may_download_reads_the_owner(profiles):
    assert owner_may_download(1) is True
    assert owner_may_download(3) is True
    assert owner_may_download(2) is False
    assert owner_may_download(4) is True        # an admin always may
    assert owner_may_download(99) is False      # a profile that's gone
    assert owner_may_download(None) is False


def test_owner_may_download_fails_closed_on_a_db_error(monkeypatch):
    import database.music_database as music_db

    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(music_db, "get_database", _boom)
    assert owner_may_download(3) is False      # skipped this pass, not grabbed
    assert owner_may_download(1) is True


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(db, profiles, monkeypatch):
    import core.profile_context as pc
    from api.audiobooks import create_audiobooks_blueprint
    who = {"pid": 1}
    monkeypatch.setattr(pc, "get_current_profile_id", lambda: who["pid"])
    catalog = MagicMock()
    catalog.get_book.return_value = MagicMock(to_dict=lambda: _book())
    app = Flask(__name__)
    app.register_blueprint(create_audiobooks_blueprint())
    with patch("api.audiobooks.get_audiobook_db", return_value=db), \
         patch("api.audiobooks.get_audiobook_client", return_value=catalog):
        yield app.test_client(), who


def test_a_profile_with_downloads_off_cannot_wish_or_follow(client, db):
    c, who = client
    who["pid"] = 2
    r = c.post("/api/audiobooks/wishlist", json={"asin": "B1"}, headers={"X-Profile-Id": "2"})
    assert r.status_code == 403 and r.get_json()["error"] == "Downloads are disabled for this profile."
    r = c.post("/api/audiobooks/watchlist", json={"name": "Brandon Sanderson"},
               headers={"X-Profile-Id": "2"})
    assert r.status_code == 403
    assert db.get_wishlist(2) == [] and db.get_watchlist(2) == []


def test_a_profile_that_can_download_still_wishes_and_follows(client, db):
    c, who = client
    who["pid"] = 3
    assert c.post("/api/audiobooks/wishlist", json={"asin": "B1"},
                  headers={"X-Profile-Id": "3"}).status_code == 200
    assert c.post("/api/audiobooks/watchlist", json={"name": "Brandon Sanderson"},
                  headers={"X-Profile-Id": "3"}).status_code == 200
    assert [r["asin"] for r in db.get_wishlist(3)] == ["B1"]


# ── the wishlist pass ────────────────────────────────────────────────────────

def test_the_pass_skips_a_book_whose_owner_cannot_download(db, profiles):
    from core.audiobook_wishlist_worker import run_pass
    db.add_to_wishlist(_book(asin="B2"), profile_id=2)
    db.add_to_wishlist(_book(asin="B3"), profile_id=3)
    grabs = []
    with patch("core.audiobook_release_search.search_releases", return_value=[_release()]), \
         patch("core.audiobook_grab.grab_release",
               side_effect=lambda rel: grabs.append(rel.title) or {"ok": True, "ref": "h"}), \
         patch("core.audiobook_download_state.register_download"):
        summary = run_pass(db)
    assert grabs == ["The Final Empire M4B"], "only profile 3's book is grabbed"
    assert summary["skipped"] == 1 and summary["errors"] == 0
    kid_row = db.get_wishlist(2)[0]
    assert kid_row["status"] == STATUS_WANTED, "left wanted for when downloads come back"


# ── the author watchlist scan ────────────────────────────────────────────────

def test_the_author_scan_wishlists_nothing_for_an_owner_who_cannot_download(db, profiles):
    from core.audiobook_client import product_to_item
    from core.audiobook_watchlist import scan_author
    fresh = product_to_item({"asin": "N1", "title": "New One", "release_date": "2026-10-01",
                             "authors": [{"name": "Brandon Sanderson"}], "narrators": []})
    client = MagicMock()
    client.get_by_author.return_value = [fresh]
    db.follow_author("Brandon Sanderson", profile_id=2, since_date="2026-01-01")
    db.follow_author("Brandon Sanderson", profile_id=3, since_date="2026-01-01")
    kid = next(r for r in db.get_watchlist(2))
    member = next(r for r in db.get_watchlist(3))
    out = scan_author(kid, db=db, client=client)
    assert out["found"] == 1 and out["wishlisted"] == 0
    assert db.get_wishlist(2) == []
    assert scan_author(member, db=db, client=client)["wishlisted"] == 1
