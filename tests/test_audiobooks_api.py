"""Tests for api/audiobooks.py — the read-only audiobook discovery endpoints.

Fully hermetic: the audiobook client is patched in every test, so nothing here
touches Audible, Apple, the database, or any other subsystem.

The blueprint is read-only and additive by design. test_audiobooks_isolation.py
holds the guards that say so.
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from api.audiobooks import create_audiobooks_blueprint, is_allowed_sample_url
from core.audiobook_client import product_to_item


@pytest.fixture
def app():
    app = Flask(__name__)
    app.register_blueprint(create_audiobooks_blueprint())
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def no_real_db():
    """Keep every route off the real database file.

    Read-only routes consult it for garnish — whether an author is followed,
    whether a book is owned — and without this a discovery test would open
    database/audiobooks.db on the developer's machine. Tests that want a real
    database ask for wishlist_db, whose patch is applied after this one and
    therefore wins.
    """
    stub = MagicMock()
    stub.is_following.return_value = False
    stub.is_owned.return_value = False
    with patch("api.audiobooks.get_audiobook_db", return_value=stub):
        yield stub


@pytest.fixture
def catalog():
    """Patch the client singleton the routes call, and hand back the mock."""
    fake = MagicMock()
    with patch("api.audiobooks.get_audiobook_client", return_value=fake):
        yield fake


def _item(asin="B1", title="Project Hail Mary", **overrides):
    payload = {
        "asin": asin,
        "title": title,
        "authors": [{"asin": "A1", "name": "Andy Weir"}],
        "narrators": [{"name": "Ray Porter"}],
        "runtime_length_min": 1479,
        "product_images": {"500": "https://img/500.jpg"},
    }
    payload.update(overrides)
    return product_to_item(payload)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def test_search_returns_results_and_the_source_that_answered(client, catalog):
    catalog.search_with_fallback.return_value = ([_item()], "audible")
    body = client.get("/api/audiobooks/search?q=hail+mary").get_json()
    assert body["success"] is True
    assert body["source"] == "audible"
    assert body["results"][0]["title"] == "Project Hail Mary"
    assert body["results"][0]["runtime_formatted"] == "24 hrs 39 mins"


def test_search_reports_when_the_apple_fallback_answered(client, catalog):
    # The UI must not promise narrator data that an Apple result does not carry.
    catalog.search_with_fallback.return_value = ([_item()], "apple")
    assert client.get("/api/audiobooks/search?q=obscure").get_json()["source"] == "apple"


@pytest.mark.parametrize("qs", ["", "?q=", "?q=%20%20"])
def test_search_with_no_query_returns_empty_without_calling_out(client, catalog, qs):
    body = client.get(f"/api/audiobooks/search{qs}").get_json()
    assert body["success"] is True and body["results"] == []
    catalog.search_with_fallback.assert_not_called()


@pytest.mark.parametrize("mode", ["keywords", "title", "author", "narrator"])
def test_search_forwards_the_valid_modes(client, catalog, mode):
    catalog.search_with_fallback.return_value = ([], "audible")
    client.get(f"/api/audiobooks/search?q=x&type={mode}")
    assert catalog.search_with_fallback.call_args.kwargs["search_type"] == mode


@pytest.mark.parametrize("mode", ["isbn", "", "publisher", "DROP TABLE"])
def test_search_rejects_unknown_modes_into_keywords(client, catalog, mode):
    catalog.search_with_fallback.return_value = ([], "audible")
    client.get(f"/api/audiobooks/search?q=x&type={mode}")
    assert catalog.search_with_fallback.call_args.kwargs["search_type"] == "keywords"


@pytest.mark.parametrize("raw,expected", [("5", 5), ("500", 50), ("0", 1), ("-3", 1), ("abc", 20)])
def test_search_clamps_the_limit(client, catalog, raw, expected):
    catalog.search_with_fallback.return_value = ([], "audible")
    client.get(f"/api/audiobooks/search?q=x&limit={raw}")
    assert catalog.search_with_fallback.call_args.kwargs["limit"] == expected


@pytest.mark.parametrize("raw,expected", [("1", 1), ("4", 4), ("0", 1), ("-2", 1), ("junk", 1)])
def test_search_normalises_the_page(client, catalog, raw, expected):
    catalog.search_with_fallback.return_value = ([], "audible")
    client.get(f"/api/audiobooks/search?q=x&page={raw}")
    assert catalog.search_with_fallback.call_args.kwargs["page"] == expected


def test_search_only_forwards_whitelisted_sorts(client, catalog):
    catalog.search_with_fallback.return_value = ([], "audible")
    client.get("/api/audiobooks/search?q=x&sort=newest")
    assert catalog.search_with_fallback.call_args.kwargs["sort"] == "newest"
    client.get("/api/audiobooks/search?q=x&sort=by-vibes")
    assert catalog.search_with_fallback.call_args.kwargs["sort"] == "relevance"


def test_search_forwards_the_marketplace(client, catalog):
    catalog.search_with_fallback.return_value = ([], "audible")
    client.get("/api/audiobooks/search?q=x&marketplace=UK")
    assert catalog.search_with_fallback.call_args.kwargs["marketplace"] == "uk"


# ---------------------------------------------------------------------------
# Detail and similar
# ---------------------------------------------------------------------------

def test_book_detail(client, catalog):
    catalog.get_book.return_value = _item(asin="B08G9PRS1K")
    body = client.get("/api/audiobooks/book/B08G9PRS1K").get_json()
    assert body["success"] is True
    assert body["book"]["asin"] == "B08G9PRS1K"
    assert body["book"]["narrator_names"] == ["Ray Porter"]


def test_book_detail_missing_is_a_404(client, catalog):
    catalog.get_book.return_value = None
    resp = client.get("/api/audiobooks/book/NOPE")
    assert resp.status_code == 404
    assert resp.get_json()["success"] is False


def test_similar(client, catalog):
    catalog.get_similar.return_value = [_item(asin="B2", title="The Martian")]
    body = client.get("/api/audiobooks/similar/B1").get_json()
    assert body["asin"] == "B1"
    assert body["results"][0]["title"] == "The Martian"


# ---------------------------------------------------------------------------
# Series, author, narrator
# ---------------------------------------------------------------------------

def test_series(client, catalog):
    catalog.get_series.return_value = [_item(asin="B1", title="The Final Empire")]
    body = client.get("/api/audiobooks/series?name=The+Mistborn+Saga&asin=S1").get_json()
    assert body["series"] == "The Mistborn Saga"
    assert body["results"][0]["title"] == "The Final Empire"
    assert catalog.get_series.call_args.kwargs["series_asin"] == "S1"


def test_series_without_an_asin_passes_none(client, catalog):
    catalog.get_series.return_value = []
    client.get("/api/audiobooks/series?name=Discworld")
    assert catalog.get_series.call_args.kwargs["series_asin"] is None


@pytest.mark.parametrize("path", [
    "/api/audiobooks/series", "/api/audiobooks/series?name=",
    "/api/audiobooks/author", "/api/audiobooks/author?name=%20",
    "/api/audiobooks/narrator", "/api/audiobooks/narrator?name=",
])
def test_name_is_required(client, catalog, path):
    resp = client.get(path)
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_author_bibliography(client, catalog):
    catalog.get_by_author.return_value = [_item()]
    body = client.get("/api/audiobooks/author?name=Andy+Weir").get_json()
    assert body["author"] == "Andy Weir" and len(body["results"]) == 1


def test_narrator_performances(client, catalog):
    catalog.get_by_narrator.return_value = [_item()]
    body = client.get("/api/audiobooks/narrator?name=Ray+Porter").get_json()
    assert body["narrator"] == "Ray Porter" and len(body["results"]) == 1


def test_people_endpoints_default_to_the_bestseller_sort(client, catalog):
    catalog.get_by_author.return_value = []
    catalog.get_by_narrator.return_value = []
    client.get("/api/audiobooks/author?name=x")
    client.get("/api/audiobooks/narrator?name=y")
    assert catalog.get_by_author.call_args.kwargs["sort"] == "bestsellers"
    assert catalog.get_by_narrator.call_args.kwargs["sort"] == "bestsellers"


# ---------------------------------------------------------------------------
# Person profile
# ---------------------------------------------------------------------------

def _profile(**overrides):
    payload = {
        "name": "Andy Weir",
        "role": "author",
        "total_books": 2,
        "total_runtime_minutes": 3215,
        "runtime_formatted": "53 hrs 35 mins",
        "genres": ["Science Fiction & Fantasy"],
        "collaborators": [{"name": "Ray Porter", "count": 2}],
        "series": [{"title": "Mistborn", "asin": "S1", "books": [_item().to_dict()]}],
        "standalone": [_item(asin="B9", title="Artemis").to_dict()],
        "highlights": [_item().to_dict()],
    }
    payload.update(overrides)
    return payload


def test_person_profile(client, catalog):
    catalog.get_person_profile.return_value = _profile()
    body = client.get("/api/audiobooks/person?name=Andy+Weir&role=author").get_json()
    assert body["success"] is True
    assert body["profile"]["name"] == "Andy Weir"
    assert body["profile"]["series"][0]["title"] == "Mistborn"
    assert catalog.get_person_profile.call_args.kwargs["role"] == "author"


def test_person_profile_narrator_role(client, catalog):
    catalog.get_person_profile.return_value = _profile(role="narrator")
    client.get("/api/audiobooks/person?name=Ray+Porter&role=narrator")
    assert catalog.get_person_profile.call_args.kwargs["role"] == "narrator"


@pytest.mark.parametrize("role", ["", "illustrator", "translator", "DROP TABLE"])
def test_person_profile_rejects_unknown_roles_into_author(client, catalog, role):
    catalog.get_person_profile.return_value = _profile()
    client.get(f"/api/audiobooks/person?name=x&role={role}")
    assert catalog.get_person_profile.call_args.kwargs["role"] == "author"


@pytest.mark.parametrize("path", [
    "/api/audiobooks/person", "/api/audiobooks/person?name=", "/api/audiobooks/person?name=%20",
])
def test_person_profile_requires_a_name(client, catalog, path):
    resp = client.get(path)
    assert resp.status_code == 400
    catalog.get_person_profile.assert_not_called()


def test_person_profile_of_an_unknown_person_is_not_an_error(client, catalog):
    # An empty profile is a page that says "nothing found", not a 500.
    catalog.get_person_profile.return_value = _profile(
        total_books=0, series=[], standalone=[], highlights=[], collaborators=[],
    )
    resp = client.get("/api/audiobooks/person?name=Nobody")
    assert resp.status_code == 200
    assert resp.get_json()["profile"]["total_books"] == 0


def test_person_profile_forwards_the_marketplace(client, catalog):
    catalog.get_person_profile.return_value = _profile()
    client.get("/api/audiobooks/person?name=x&marketplace=UK")
    assert catalog.get_person_profile.call_args.kwargs["marketplace"] == "uk"


# ---------------------------------------------------------------------------
# Shelves
# ---------------------------------------------------------------------------

def test_browse_forwards_the_genre_and_sort(client, catalog):
    catalog.browse.return_value = [_item()]
    body = client.get(
        "/api/audiobooks/browse?category=Science+Fiction+%26+Fantasy&sort=newest"
    ).get_json()
    assert body["success"] is True
    assert body["category"] == "Science Fiction & Fantasy"
    assert catalog.browse.call_args.kwargs["category"] == "Science Fiction & Fantasy"
    assert catalog.browse.call_args.kwargs["sort"] == "newest"


def test_browse_still_accepts_a_legacy_category_id(client, catalog):
    # Older bookmarks carry an id; they keep working even though ids do not
    # survive a storefront change.
    catalog.browse.return_value = []
    client.get("/api/audiobooks/browse?category_id=18580606011")
    assert catalog.browse.call_args.kwargs["category"] == "18580606011"


def test_browse_without_a_genre_passes_none(client, catalog):
    catalog.browse.return_value = []
    client.get("/api/audiobooks/browse")
    assert catalog.browse.call_args.kwargs["category"] is None
    assert catalog.browse.call_args.kwargs["sort"] == "bestsellers"


def test_bestsellers_and_new_releases(client, catalog):
    catalog.get_bestsellers.return_value = [_item()]
    catalog.get_new_releases.return_value = [_item()]
    assert client.get("/api/audiobooks/bestsellers").get_json()["success"] is True
    assert client.get("/api/audiobooks/new-releases").get_json()["success"] is True
    catalog.get_bestsellers.assert_called_once()
    catalog.get_new_releases.assert_called_once()


def test_categories(client, catalog):
    catalog.get_categories.return_value = [{"id": "1", "name": "Fantasy", "children": []}]
    body = client.get("/api/audiobooks/categories").get_json()
    assert body["categories"][0]["name"] == "Fantasy"


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

def test_home_builds_every_shelf_and_a_hero(client, catalog):
    catalog.browse.return_value = [_item(asin="B1"), _item(asin="B2")]
    body = client.get("/api/audiobooks/home").get_json()
    assert body["success"] is True
    assert len(body["shelves"]) == 6
    assert {s["key"] for s in body["shelves"]} == {
        "bestsellers", "new", "scifi", "mystery", "biographies", "fiction",
    }
    assert body["hero"]["asin"] == "B1"
    for shelf in body["shelves"]:
        assert shelf["title"] and "results" in shelf
        # Genres travel as names: Audible's ids differ per storefront, so an id
        # here would produce an empty shelf whenever the server fell back.
        assert not shelf["category"].isdigit()


def test_home_survives_a_failing_shelf(client, catalog):
    # Five shelves and a hero is a page. A 500 is not.
    #
    # Each shelf gets DISTINCT books: the page de-duplicates across shelves, so
    # a stub handing back the same ASIN every time would empty every shelf but
    # the first and hide the behaviour under test.
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("shelf blew up")
        return [_item(asin=f"B{calls['n']}", title=f"Book {calls['n']}")]

    catalog.browse.side_effect = flaky
    body = client.get("/api/audiobooks/home").get_json()
    assert body["success"] is True
    assert len(body["shelves"]) == 6
    assert sum(1 for s in body["shelves"] if not s["results"]) == 1
    assert body["hero"] is not None


def test_home_never_repeats_a_title_across_shelves(client, catalog):
    # The bug this guards: the same mega-seller sits in several genres at once,
    # so without de-duplication the browse page showed the same covers three
    # rows apart and looked broken.
    catalog.browse.return_value = [_item(asin=f"B{i}", title=f"Book {i}") for i in range(30)]
    body = client.get("/api/audiobooks/home?limit=5").get_json()
    seen = [book["asin"] for shelf in body["shelves"] for book in shelf["results"]]
    assert len(seen) == len(set(seen))


def test_home_fetches_deeper_than_it_shows(client, catalog):
    # De-duplication happens after the fetch, so trimming repeats out of an
    # exactly-sized shelf would leave a visibly short row.
    catalog.browse.return_value = []
    client.get("/api/audiobooks/home?limit=10")
    assert catalog.browse.call_args.kwargs["limit"] > 10


def test_home_hero_is_none_when_everything_is_empty(client, catalog):
    catalog.browse.return_value = []
    body = client.get("/api/audiobooks/home").get_json()
    assert body["hero"] is None
    assert all(s["results"] == [] for s in body["shelves"])


# ---------------------------------------------------------------------------
# Wishlist
# ---------------------------------------------------------------------------

@pytest.fixture
def wishlist_db(tmp_path):
    """A temp audiobook database wired into the blueprint.

    The grab route's wake-up is stubbed out too: it starts a real daemon thread
    that polls a download client, and a test suite must never spawn one. The
    wishlist route starts nothing — the automation engine schedules that.
    """
    from core.audiobook_database import AudiobookDatabase

    db = AudiobookDatabase(str(tmp_path / "audiobooks.db"))
    with patch("api.audiobooks.get_audiobook_db", return_value=db), \
         patch("core.audiobook_download_monitor.ensure_started", return_value=False):
        yield db
    db.close()


def test_wishlist_is_empty_to_begin_with(client, catalog, wishlist_db):
    body = client.get("/api/audiobooks/wishlist").get_json()
    assert body["success"] is True
    assert body["items"] == []
    assert body["counts"]["total"] == 0


def test_wanting_a_book_resolves_it_server_side(client, catalog, wishlist_db):
    # Only an ASIN comes from the browser. A row built from a browser payload
    # could be half-filled, and the wishlist searches from what is in it.
    catalog.get_book.return_value = _item(asin="B08G9PRS1K", title="Project Hail Mary")
    resp = client.post("/api/audiobooks/wishlist", json={"asin": "B08G9PRS1K"})
    assert resp.status_code == 200
    assert resp.get_json()["wishlisted"] is True
    catalog.get_book.assert_called_once()
    assert wishlist_db.is_wishlisted("B08G9PRS1K") is True


def test_wanting_the_same_book_twice_is_still_a_success(client, catalog, wishlist_db):
    # The book is wanted either way; a 409 would make the button look broken.
    catalog.get_book.return_value = _item(asin="B1")
    client.post("/api/audiobooks/wishlist", json={"asin": "B1"})
    resp = client.post("/api/audiobooks/wishlist", json={"asin": "B1"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["added"] is False


def test_wanting_an_unknown_book_is_a_404(client, catalog, wishlist_db):
    catalog.get_book.return_value = None
    resp = client.post("/api/audiobooks/wishlist", json={"asin": "NOPE"})
    assert resp.status_code == 404
    assert wishlist_db.get_wishlist() == []


@pytest.mark.parametrize("body", [{}, {"asin": ""}, {"asin": "   "}])
def test_wanting_without_an_asin_is_a_400(client, catalog, wishlist_db, body):
    resp = client.post("/api/audiobooks/wishlist", json=body)
    assert resp.status_code == 400
    catalog.get_book.assert_not_called()


def test_removing_from_the_wishlist(client, catalog, wishlist_db):
    catalog.get_book.return_value = _item(asin="B1")
    client.post("/api/audiobooks/wishlist", json={"asin": "B1"})
    resp = client.delete("/api/audiobooks/wishlist/B1")
    assert resp.get_json() == {"success": True, "removed": True, "wishlisted": False}
    assert wishlist_db.get_wishlist() == []


def test_removing_something_never_wanted_is_not_an_error(client, catalog, wishlist_db):
    resp = client.delete("/api/audiobooks/wishlist/NEVER")
    assert resp.status_code == 200
    assert resp.get_json()["removed"] is False


def test_the_wishlist_reports_counts_and_worker_state(client, catalog, wishlist_db):
    catalog.get_book.return_value = _item(asin="B1")
    client.post("/api/audiobooks/wishlist", json={"asin": "B1"})
    body = client.get("/api/audiobooks/wishlist").get_json()
    assert body["counts"]["wanted"] == 1
    assert body["counts"]["total"] == 1
    assert "worker" in body


def test_a_manual_pass_runs_the_same_code_as_the_timer(client, catalog, wishlist_db):
    # The manual button and the background worker must not drift apart.
    with patch("core.audiobook_wishlist_worker.run_pass",
               return_value={"checked": 2, "grabbed": 1}) as run:
        body = client.post("/api/audiobooks/wishlist/search", json={}).get_json()
    run.assert_called_once()
    assert body["summary"]["checked"] == 2


def test_a_failing_pass_is_reported_not_raised(client, catalog, wishlist_db):
    with patch("core.audiobook_wishlist_worker.run_pass", side_effect=RuntimeError("boom")):
        resp = client.post("/api/audiobooks/wishlist/search", json={})
    assert resp.status_code == 500
    assert resp.get_json()["success"] is False


# ---------------------------------------------------------------------------
# Releases and grabbing
# ---------------------------------------------------------------------------

def _candidate(**overrides):
    payload = {
        "protocol": "torrent",
        "title": "Project Hail Mary M4B",
        "indexer": "SomeTracker",
        "size_bytes": 700 * 1024 * 1024,
        "guid": "g1",
        "download_url": "https://example.invalid/a.torrent",
        "magnet_uri": None,
    }
    payload.update(overrides)
    return payload


def test_releases_returns_ranked_candidates(client, catalog, wishlist_db):
    from core.audiobook_release_search import AudiobookRelease

    catalog.get_book.return_value = _item(asin="B1")
    found = [AudiobookRelease(source="prowlarr", protocol="torrent", title="Book M4B",
                              indexer="X", size_bytes=1, download_url="u")]
    with patch("core.audiobook_release_search.search_all_sources", return_value=found):
        body = client.get("/api/audiobooks/releases/B1").get_json()
    assert body["success"] is True
    assert body["releases"][0]["title"] == "Book M4B"


def test_releases_for_an_unknown_book_is_a_404(client, catalog, wishlist_db):
    catalog.get_book.return_value = None
    assert client.get("/api/audiobooks/releases/NOPE").status_code == 404


def test_a_failing_indexer_search_is_a_502(client, catalog, wishlist_db):
    catalog.get_book.return_value = _item(asin="B1")
    with patch("core.audiobook_release_search.search_releases",
               side_effect=RuntimeError("prowlarr down")):
        resp = client.get("/api/audiobooks/releases/B1")
    assert resp.status_code == 502


def test_grabbing_sends_the_release_to_the_client(client, catalog, wishlist_db):
    catalog.get_book.return_value = _item(asin="B1")
    with patch("core.audiobook_grab.grab_release",
               return_value={"ok": True, "ref": "hash-1"}) as grab:
        body = client.post("/api/audiobooks/grab",
                           json={"asin": "B1", "release": _candidate()}).get_json()
    grab.assert_called_once()
    assert body["success"] is True
    assert body["ref"] == "hash-1"


def test_grabbing_passes_the_release_back_unchanged(client, catalog, wishlist_db):
    # Re-searching to find "the same" release would race the indexer and could
    # grab something else entirely.
    with patch("core.audiobook_grab.grab_release", return_value={"ok": True}) as grab:
        client.post("/api/audiobooks/grab",
                    json={"asin": "B1", "release": _candidate(guid="exact")})
    assert grab.call_args[0][0]["guid"] == "exact"


def test_grabbing_moves_a_wishlisted_row_along(client, catalog, wishlist_db):
    catalog.get_book.return_value = _item(asin="B1")
    client.post("/api/audiobooks/wishlist", json={"asin": "B1"})
    with patch("core.audiobook_grab.grab_release", return_value={"ok": True}):
        client.post("/api/audiobooks/grab", json={"asin": "B1", "release": _candidate()})
    assert wishlist_db.get_wishlist()[0]["status"] == "grabbed"


def test_grabbing_something_never_wishlisted_does_not_add_it(client, catalog, wishlist_db):
    with patch("core.audiobook_grab.grab_release", return_value={"ok": True}):
        client.post("/api/audiobooks/grab", json={"asin": "NEVER", "release": _candidate()})
    assert wishlist_db.get_wishlist() == []


@pytest.mark.parametrize("body", [{}, {"asin": "B1"}, {"release": "not a dict"}, {"release": None}])
def test_grabbing_without_a_release_is_a_400(client, catalog, wishlist_db, body):
    with patch("core.audiobook_grab.grab_release") as grab:
        resp = client.post("/api/audiobooks/grab", json=body)
    assert resp.status_code == 400
    grab.assert_not_called()


def test_a_refused_grab_is_a_502_with_the_client_message(client, catalog, wishlist_db):
    with patch("core.audiobook_grab.grab_release",
               return_value={"ok": False, "error": "No torrent client configured"}):
        resp = client.post("/api/audiobooks/grab",
                           json={"asin": "B1", "release": _candidate()})
    assert resp.status_code == 502
    assert "No torrent client" in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# Sample proxy allowlist — this endpoint must never become a general fetcher
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://samples.audible.com/bk/aren/000900/bk_aren_000900_sample.mp3",
    "https://api.audible.com/x.mp3",
    "https://audible.com/x.mp3",
    "https://samples.audible.co.uk/x.mp3",
    "https://audio-ssl.itunes.apple.com/preview.m4a",
    "https://is1-ssl.mzstatic.com/preview.m4a",
])
def test_allowed_sample_hosts(url):
    assert is_allowed_sample_url(url) is True


@pytest.mark.parametrize("url", [
    "",
    None,
    "   ",
    "http://samples.audible.com/x.mp3",              # https only
    "https://audible.com.evil.net/x.mp3",            # suffix must be a real boundary
    "https://notaudible.com/x.mp3",
    "https://evil.com/x.mp3",
    "https://evil.com/?x=https://samples.audible.com/a.mp3",
    "http://127.0.0.1:8888/admin",
    "https://127.0.0.1/admin",
    "https://localhost/admin",
    "https://169.254.169.254/latest/meta-data/",      # cloud metadata
    "file:///etc/passwd",
    "ftp://samples.audible.com/x.mp3",
    "https:///x.mp3",
    "samples.audible.com/x.mp3",
])
def test_refused_sample_hosts(url):
    assert is_allowed_sample_url(url) is False


def test_sample_proxy_requires_a_url(client):
    resp = client.get("/api/audiobooks/sample-proxy")
    assert resp.status_code == 400


def test_sample_proxy_refuses_a_disallowed_host(client):
    with patch("api.audiobooks.requests.get") as fetch:
        resp = client.get("/api/audiobooks/sample-proxy?url=https://evil.com/x.mp3")
    assert resp.status_code == 400
    fetch.assert_not_called()


def test_sample_proxy_streams_an_allowed_sample(client):
    upstream = MagicMock()
    upstream.status_code = 200
    upstream.headers = {"Content-Type": "audio/mpeg", "Content-Length": "9"}
    upstream.iter_content.return_value = [b"abc", b"def", b"ghi"]
    with patch("api.audiobooks.requests.get", return_value=upstream):
        resp = client.get("/api/audiobooks/sample-proxy?url=https://samples.audible.com/a.mp3")
    assert resp.status_code == 200
    assert resp.data == b"abcdefghi"
    assert resp.headers["Content-Type"] == "audio/mpeg"
    assert resp.headers["Accept-Ranges"] == "bytes"


def test_sample_proxy_forwards_the_range_header(client):
    upstream = MagicMock()
    upstream.status_code = 206
    upstream.headers = {"Content-Type": "audio/mpeg", "Content-Range": "bytes 0-3/9"}
    upstream.iter_content.return_value = [b"abcd"]
    with patch("api.audiobooks.requests.get", return_value=upstream) as fetch:
        resp = client.get(
            "/api/audiobooks/sample-proxy?url=https://samples.audible.com/a.mp3",
            headers={"Range": "bytes=0-3"},
        )
    assert fetch.call_args.kwargs["headers"]["Range"] == "bytes=0-3"
    assert resp.status_code == 206
    assert resp.headers["Content-Range"] == "bytes 0-3/9"


def test_sample_proxy_does_not_follow_redirects(client):
    # The allowlist checked the URL we were given. A 302 could point anywhere.
    upstream = MagicMock()
    upstream.status_code = 302
    upstream.headers = {"Location": "https://evil.com/payload"}
    with patch("api.audiobooks.requests.get", return_value=upstream) as fetch:
        resp = client.get("/api/audiobooks/sample-proxy?url=https://samples.audible.com/a.mp3")
    assert fetch.call_args.kwargs["allow_redirects"] is False
    assert resp.status_code == 502


def test_sample_proxy_reports_an_upstream_error(client):
    upstream = MagicMock()
    upstream.status_code = 404
    upstream.headers = {}
    with patch("api.audiobooks.requests.get", return_value=upstream):
        resp = client.get("/api/audiobooks/sample-proxy?url=https://samples.audible.com/a.mp3")
    assert resp.status_code == 502


def test_sample_proxy_reports_a_network_failure(client):
    with patch("api.audiobooks.requests.get", side_effect=OSError("boom")):
        resp = client.get("/api/audiobooks/sample-proxy?url=https://samples.audible.com/a.mp3")
    assert resp.status_code == 502
    assert resp.get_json()["success"] is False


# ---------------------------------------------------------------------------
# Narrator choice
#
# On Audible the narrator is baked into the ASIN, so wishing for a book has
# already chosen a reading. The only question is whether the DOWNLOAD must match
# it — and the answer is per book, never a list, because a book is always
# exactly one narrator.
# ---------------------------------------------------------------------------

def test_wishlisting_defaults_to_the_narrator_you_picked(client, catalog, wishlist_db):
    catalog.get_book.return_value = _item(asin="B1")
    body = client.post("/api/audiobooks/wishlist", json={"asin": "B1"}).get_json()
    assert body["narrator_mode"] == "exact"
    assert wishlist_db.get_wishlist()[0]["narrator_mode"] == "exact"


def test_wishlisting_can_accept_any_narrator(client, catalog, wishlist_db):
    catalog.get_book.return_value = _item(asin="B1")
    body = client.post(
        "/api/audiobooks/wishlist", json={"asin": "B1", "narrator_mode": "any"},
    ).get_json()
    assert body["narrator_mode"] == "any"
    assert wishlist_db.get_wishlist()[0]["narrator_mode"] == "any"


@pytest.mark.parametrize("mode", ["", "both", "mixed", None, "EXACT "])
def test_an_unknown_narrator_mode_falls_back_to_exact(client, catalog, wishlist_db, mode):
    # Never a combination: anything unrecognised holds to the chosen reading.
    catalog.get_book.return_value = _item(asin="B1")
    body = client.post(
        "/api/audiobooks/wishlist", json={"asin": "B1", "narrator_mode": mode},
    ).get_json()
    assert body["narrator_mode"] == "exact"


def test_searching_releases_honours_the_stored_choice(client, catalog, wishlist_db):
    # Asking again from the detail page must not quietly widen what the listener
    # asked for.
    catalog.get_book.return_value = _item(asin="B1")
    client.post("/api/audiobooks/wishlist", json={"asin": "B1", "narrator_mode": "any"})

    with patch("core.audiobook_release_search.search_releases", return_value=[]) as search:
        body = client.get("/api/audiobooks/releases/B1").get_json()
    assert search.call_args.kwargs["narrator_mode"] == "any"
    assert body["narrator_mode"] == "any"


def test_a_book_not_on_the_wishlist_searches_exactly(client, catalog, wishlist_db):
    catalog.get_book.return_value = _item(asin="B1")
    with patch("core.audiobook_release_search.search_releases", return_value=[]) as search:
        client.get("/api/audiobooks/releases/B1")
    assert search.call_args.kwargs["narrator_mode"] == "exact"


def test_the_caller_can_override_the_mode_for_one_search(client, catalog, wishlist_db):
    catalog.get_book.return_value = _item(asin="B1")
    with patch("core.audiobook_release_search.search_releases", return_value=[]) as search:
        client.get("/api/audiobooks/releases/B1?narrator_mode=any")
    assert search.call_args.kwargs["narrator_mode"] == "any"


def test_the_releases_response_names_the_narrator_that_was_wanted(client, catalog, wishlist_db):
    # So the modal can say whose reading it is holding out for.
    catalog.get_book.return_value = _item(asin="B1")
    with patch("core.audiobook_release_search.search_releases", return_value=[]):
        body = client.get("/api/audiobooks/releases/B1").get_json()
    assert body["narrators"] == ["Ray Porter"]


def test_the_narrator_choice_can_be_changed_later(client, catalog, wishlist_db):
    # It moved off the add-to-wishlist path: asking on every heart click taxed
    # the commonest action to serve the rarest intent.
    catalog.get_book.return_value = _item(asin="B1")
    client.post("/api/audiobooks/wishlist", json={"asin": "B1"})
    assert wishlist_db.get_wishlist()[0]["narrator_mode"] == "exact"

    body = client.patch(
        "/api/audiobooks/wishlist/B1", json={"narrator_mode": "any"},
    ).get_json()
    assert body["success"] is True
    assert wishlist_db.get_wishlist()[0]["narrator_mode"] == "any"


def test_changing_the_choice_does_not_reset_the_backoff(client, catalog, wishlist_db):
    # Otherwise loosening a book would let it jump the retry queue.
    from core.audiobook_database import STATUS_FAILED

    catalog.get_book.return_value = _item(asin="B1")
    client.post("/api/audiobooks/wishlist", json={"asin": "B1"})
    wishlist_db.mark_wishlist_status("B1", STATUS_FAILED, count_attempt=True)
    before = wishlist_db.get_wishlist()[0]

    client.patch("/api/audiobooks/wishlist/B1", json={"narrator_mode": "any"})
    after = wishlist_db.get_wishlist()[0]
    assert after["attempt_count"] == before["attempt_count"]
    assert after["last_attempt_at"] == before["last_attempt_at"]


def test_re_adding_never_rewrites_the_choice(client, catalog, wishlist_db):
    catalog.get_book.return_value = _item(asin="B1")
    client.post("/api/audiobooks/wishlist", json={"asin": "B1", "narrator_mode": "any"})
    client.post("/api/audiobooks/wishlist", json={"asin": "B1", "narrator_mode": "exact"})
    assert wishlist_db.get_wishlist()[0]["narrator_mode"] == "any"


@pytest.mark.parametrize("mode", ["", "both", "mixed", None])
def test_an_invalid_narrator_mode_is_refused(client, catalog, wishlist_db, mode):
    catalog.get_book.return_value = _item(asin="B1")
    client.post("/api/audiobooks/wishlist", json={"asin": "B1"})
    resp = client.patch("/api/audiobooks/wishlist/B1", json={"narrator_mode": mode})
    assert resp.status_code == 400
    assert wishlist_db.get_wishlist()[0]["narrator_mode"] == "exact"


def test_changing_the_choice_on_an_unwanted_book_is_a_404(client, catalog, wishlist_db):
    resp = client.patch("/api/audiobooks/wishlist/NEVER", json={"narrator_mode": "any"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Already owned
#
# The library table was written on every import and never read back, so nothing
# stopped a book being fetched twice.
# ---------------------------------------------------------------------------

def test_grabbing_a_book_you_already_own_is_refused(client, catalog, wishlist_db):
    catalog.get_book.return_value = _item(asin="B1")
    wishlist_db.add_to_library({"asin": "B1", "title": "The Final Empire"},
                               "/library/Sanderson/Book")

    with patch("core.audiobook_grab.grab_release") as grab:
        resp = client.post("/api/audiobooks/grab",
                           json={"asin": "B1", "release": _candidate()})
    assert resp.status_code == 409
    assert resp.get_json()["owned"] is True
    grab.assert_not_called()


def test_an_owned_book_can_still_be_grabbed_deliberately(client, catalog, wishlist_db):
    # For a better edition, or to replace a bad rip.
    catalog.get_book.return_value = _item(asin="B1")
    wishlist_db.add_to_library({"asin": "B1", "title": "The Final Empire"},
                               "/library/Sanderson/Book")

    with patch("core.audiobook_grab.grab_release",
               return_value={"ok": True, "ref": "h1"}) as grab:
        resp = client.post("/api/audiobooks/grab",
                           json={"asin": "B1", "release": _candidate(), "force": True})
    assert resp.status_code == 200
    grab.assert_called_once()


def test_a_book_you_do_not_own_grabs_normally(client, catalog, wishlist_db):
    catalog.get_book.return_value = _item(asin="B1")
    with patch("core.audiobook_grab.grab_release",
               return_value={"ok": True, "ref": "h1"}) as grab:
        resp = client.post("/api/audiobooks/grab",
                           json={"asin": "B1", "release": _candidate()})
    assert resp.status_code == 200
    grab.assert_called_once()


# ---------------------------------------------------------------------------
# Owned badges
# ---------------------------------------------------------------------------

def test_search_results_say_whether_the_book_is_already_owned(client, catalog, no_real_db):
    no_real_db.owned_asins.return_value = {"B1"}
    catalog.search_with_fallback.return_value = ([_item(asin="B1"), _item(asin="B2")], "audible")
    results = client.get("/api/audiobooks/search?q=hail").get_json()["results"]
    assert [r["owned"] for r in results] == [True, False]


def test_a_book_detail_says_whether_it_is_owned(client, catalog, no_real_db):
    no_real_db.owned_asins.return_value = {"B1"}
    catalog.get_book.return_value = _item(asin="B1")
    assert client.get("/api/audiobooks/book/B1").get_json()["book"]["owned"] is True


def test_a_shelf_says_whether_its_books_are_owned(client, catalog, no_real_db):
    no_real_db.owned_asins.return_value = set()
    catalog.get_bestsellers.return_value = [_item(asin="B1")]
    results = client.get("/api/audiobooks/bestsellers").get_json()["results"]
    assert results[0]["owned"] is False


def test_the_badge_costs_one_database_read_per_page(client, catalog, no_real_db):
    # Twenty round trips to answer "do I have this" is nineteen too many.
    no_real_db.owned_asins.return_value = set()
    catalog.search_with_fallback.return_value = ([_item(asin=f"B{i}") for i in range(20)], "audible")
    client.get("/api/audiobooks/search?q=x")
    assert no_real_db.owned_asins.call_count == 1


def test_an_unreadable_database_costs_the_badge_not_the_page(client, catalog, no_real_db):
    no_real_db.owned_asins.side_effect = RuntimeError("locked")
    catalog.search_with_fallback.return_value = ([_item(asin="B1")], "audible")
    body = client.get("/api/audiobooks/search?q=x").get_json()
    assert body["success"] is True
    assert body["results"][0]["owned"] is False


def test_an_author_page_marks_owned_books_across_every_group(client, catalog, no_real_db):
    no_real_db.owned_asins.return_value = {"B1", "B3"}
    catalog.get_person_profile.return_value = {
        "name": "Andy Weir", "role": "author", "total_books": 3,
        "total_runtime_minutes": 0, "runtime_formatted": "", "genres": [],
        "collaborators": [],
        "series": [{"title": "S", "asin": "S1", "books": [_item(asin="B1").to_dict()]}],
        "standalone": [_item(asin="B2").to_dict()],
        "highlights": [_item(asin="B3").to_dict()],
    }
    profile = client.get("/api/audiobooks/person?name=Andy+Weir").get_json()["profile"]
    assert profile["series"][0]["books"][0]["owned"] is True
    assert profile["standalone"][0]["owned"] is False
    assert profile["highlights"][0]["owned"] is True


def test_a_soulseek_grab_stores_the_folder_handle_not_the_card_id(client, catalog, wishlist_db):
    # The card id is short and readable; the poller needs every transfer id.
    # They are different values, and storing the card id would lose the folder.
    release = {"protocol": "soulseek", "title": "Book", "indexer": "soulseek:peer",
               "size_bytes": 1, "soulseek": {"username": "peer", "album_path": "x/Book",
                                             "files": [{"filename": "x/Book/01.mp3", "size": 1}],
                                             "file_count": 1, "queue_length": 0}}
    catalog.get_book.return_value = _item(asin="B1")
    grabbed = {"ok": True, "ref": "slsk:t1", "client_ref": '{"username": "peer", "refs": ["t1"], "folder": "Book"}',
               "client": "soulseek", "files": 1}
    with patch("core.audiobook_grab.grab_release", return_value=grabbed):
        body = client.post("/api/audiobooks/grab",
                           json={"asin": "B1", "release": release}).get_json()

    assert body["success"] is True and body["ref"] == "slsk:t1"
    row = wishlist_db.get_downloads()[0]
    assert row["download_id"] == "slsk:t1"
    assert "t1" in row["client_id"] and "peer" in row["client_id"]


# ---------------------------------------------------------------------------
# Streaming release search
# ---------------------------------------------------------------------------

def test_starting_a_search_returns_an_id_without_waiting(client, catalog, wishlist_db):
    # The whole point: the request must not block on the search.
    catalog.get_book.return_value = _item(asin="B1")
    with patch("core.audiobook_search_job.start", return_value="job-1") as start:
        body = client.post("/api/audiobooks/releases/B1/start").get_json()
    assert body["success"] is True and body["id"] == "job-1"
    assert body["poll_ms"] > 0
    start.assert_called_once()


def test_starting_a_search_for_an_unknown_book_is_a_404(client, catalog, wishlist_db):
    catalog.get_book.return_value = None
    assert client.post("/api/audiobooks/releases/NOPE/start").status_code == 404


def test_a_search_honours_the_wishlisted_narrator_choice(client, catalog, wishlist_db):
    # Asking again from the detail page must not quietly widen a choice the
    # listener already made.
    catalog.get_book.return_value = _item(asin="B1")
    wishlist_db.add_to_wishlist({"asin": "B1", "title": "T"}, narrator_mode="any")
    with patch("core.audiobook_search_job.start", return_value="job-1") as start:
        body = client.post("/api/audiobooks/releases/B1/start").get_json()
    assert body["narrator_mode"] == "any"
    assert start.call_args.kwargs["narrator_mode"] == "any"


def test_polling_returns_the_pool_so_far(client, catalog, wishlist_db):
    state = {"id": "job-1", "stage": "Searching indexers", "releases": [{"title": "A"}],
             "complete": False, "error": ""}
    with patch("core.audiobook_search_job.poll", return_value=state):
        body = client.get("/api/audiobooks/releases/poll?id=job-1").get_json()
    assert body["success"] is True
    assert body["complete"] is False
    assert body["releases"] == [{"title": "A"}]
    assert body["stage"] == "Searching indexers"


def test_polling_an_expired_job_says_so_rather_than_erroring(client, catalog, wishlist_db):
    # The client stops polling and keeps what it already rendered.
    with patch("core.audiobook_search_job.poll", return_value=None):
        resp = client.get("/api/audiobooks/releases/poll?id=gone")
    assert resp.status_code == 404
    assert resp.get_json()["expired"] is True


def test_a_search_can_be_cancelled(client, catalog, wishlist_db):
    # Closing the modal should stop the work, not leave it running for a book
    # nobody is looking at.
    with patch("core.audiobook_search_job.forget", return_value=True) as forget:
        body = client.delete("/api/audiobooks/releases/poll?id=job-1").get_json()
    assert body["success"] is True and body["dropped"] is True
    forget.assert_called_once_with("job-1")


def test_the_blocking_endpoint_still_works(client, catalog, wishlist_db):
    # The wishlist path and any script still use it.
    from core.audiobook_release_search import AudiobookRelease

    catalog.get_book.return_value = _item(asin="B1")
    found = [AudiobookRelease(source="prowlarr", protocol="torrent", title="Book M4B",
                              indexer="X", size_bytes=1, download_url="u")]
    with patch("core.audiobook_release_search.search_all_sources", return_value=found):
        body = client.get("/api/audiobooks/releases/B1").get_json()
    assert body["success"] is True
    assert body["releases"][0]["title"] == "Book M4B"
