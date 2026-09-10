"""Tests for core/audiobook_client.py — pure helpers, payload parsing, and the
catalog client's request shaping.

Fully hermetic: no test here reaches the network. The client's _get_json is
patched wherever a request would otherwise happen, and the pure helpers need
nothing at all.

Several of these guard bugs that were found against the LIVE Audible API and are
invisible from the payload alone:
  - page is zero indexed, so a 1-based page number silently skips a page
  - product_extended_attrs re-ranks search results, so it must stay off search
  - series_asin is accepted and then ignored as a filter
  - a new release reports average_rating 0.0 rather than null
"""

from unittest.mock import patch

import pytest

from core.audiobook_client import (
    _DETAIL_RESPONSE_GROUPS,
    _MAX_PAGE_SIZE,
    _SEARCH_RESPONSE_GROUPS,
    AudiobookClient,
    AudiobookItem,
    AudiobookPerson,
    AudiobookSeries,
    _TTLCache,
    apple_result_to_item,
    best_cover,
    clean_summary_html,
    format_runtime,
    get_audiobook_client,
    collapse_editions,
    count_collaborators,
    group_by_series,
    is_credited,
    marketplace_chain,
    marketplace_host,
    marketplace_language,
    normalize_category_name,
    response_was_processed,
    page_offset,
    pick_highlights,
    parse_sequence,
    product_to_item,
    series_query_variants,
    upgrade_apple_artwork,
)


# ---------------------------------------------------------------------------
# Fixtures — payloads shaped exactly like the live catalog returns them
# ---------------------------------------------------------------------------

def _product(**overrides):
    product = {
        "asin": "B08G9PRS1K",
        "title": "Project Hail Mary",
        "subtitle": "A Novel",
        "authors": [{"asin": "B00G0WYW92", "name": "Andy Weir"}],
        "narrators": [{"name": "Ray Porter"}],
        "series": [{"asin": "B006K1P698", "sequence": "1", "title": "The Mistborn Saga"}],
        "publisher_name": "Audible Studios",
        "publisher_summary": "<p>A long <b>description</b>.</p><p>Second paragraph.</p>",
        "merchandising_summary": "<p>A short blurb.</p>",
        "release_date": "2021-05-04",
        "issue_date": "2021-05-01",
        "runtime_length_min": 1479,
        "product_images": {
            "252": "https://img/252.jpg",
            "500": "https://img/500.jpg",
            "1024": "https://img/1024.jpg",
        },
        "sample_url": "https://samples.audible.com/bk/sample.mp3",
        "rating": {
            "overall_distribution": {
                "average_rating": 4.791816,
                "num_ratings": 98461,
                "num_five_star_ratings": 81915,
                "num_four_star_ratings": 13650,
                "num_three_star_ratings": 2146,
                "num_two_star_ratings": 444,
                "num_one_star_ratings": 306,
            }
        },
        "category_ladders": [
            {"root": "Genres", "ladder": [
                {"id": "1", "name": "Science Fiction & Fantasy"},
                {"id": "2", "name": "Fantasy"},
                {"id": "3", "name": "Epic"},
            ]},
            {"root": "Genres", "ladder": [
                {"id": "1", "name": "Science Fiction & Fantasy"},
                {"id": "4", "name": "Adventure"},
            ]},
        ],
        "language": "english",
        "format_type": "unabridged",
        "is_adult_product": False,
    }
    product.update(overrides)
    return product


def _apple_result(**overrides):
    result = {
        "collectionId": 1565808256,
        "collectionName": "Project Hail Mary (Unabridged)",
        "artistName": "Andy Weir",
        "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/a/b/c.jpg/100x100bb.jpg",
        "description": "<b>A description.</b>",
        "releaseDate": "2021-05-04T07:00:00Z",
        "primaryGenreName": "Sci-Fi & Fantasy",
        "previewUrl": "https://audio-ssl.itunes.apple.com/preview.m4a",
        "copyright": "© 2021 Audible Studios",
        "country": "USA",
        "collectionExplicitness": "cleaned",
    }
    result.update(overrides)
    return result


@pytest.fixture
def client():
    """A fresh client per test so the TTL cache never leaks between them."""
    return AudiobookClient()


# ---------------------------------------------------------------------------
# clean_summary_html
# ---------------------------------------------------------------------------

def test_clean_summary_strips_tags_and_unescapes():
    out = clean_summary_html("<p>Hello &amp; welcome to <b>the</b> show.</p>")
    assert out == "Hello & welcome to the show."


def test_clean_summary_keeps_paragraph_breaks():
    out = clean_summary_html("<p>First para.</p><p>Second para.</p>")
    assert out.split("\n") == ["First para.", "Second para."]


def test_clean_summary_turns_br_into_a_break():
    assert clean_summary_html("line one<br />line two") == "line one\nline two"


def test_clean_summary_collapses_runs_of_blank_lines():
    out = clean_summary_html("<p>A</p><br><br><br><p>B</p>")
    assert "\n\n\n" not in out
    assert out.startswith("A") and out.endswith("B")


def test_clean_summary_collapses_horizontal_whitespace():
    assert clean_summary_html("too    many\t\tspaces") == "too many spaces"


@pytest.mark.parametrize("raw", [None, "", "   ", "<p></p>"])
def test_clean_summary_empty_inputs(raw):
    assert clean_summary_html(raw) == ""


# ---------------------------------------------------------------------------
# format_runtime
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("minutes,expected", [
    (1479, "24 hrs 39 mins"),
    (120, "2 hrs"),
    (60, "1 hr"),
    (61, "1 hr 1 min"),
    (45, "45 mins"),
    (1, "1 min"),
])
def test_format_runtime_shapes(minutes, expected):
    assert format_runtime(minutes) == expected


@pytest.mark.parametrize("bad", [None, 0, -30, "", "abc", [], {}])
def test_format_runtime_unknown_renders_as_nothing(bad):
    # An unknown runtime must render as nothing at all, never as "0 mins".
    assert format_runtime(bad) == ""


def test_format_runtime_accepts_numeric_strings():
    assert format_runtime("90") == "1 hr 30 mins"


# ---------------------------------------------------------------------------
# upgrade_apple_artwork
# ---------------------------------------------------------------------------

def test_upgrade_apple_artwork_rewrites_size():
    url = "https://is1-ssl.mzstatic.com/image/thumb/a/b/c.jpg/100x100bb.jpg"
    assert upgrade_apple_artwork(url).endswith("/1400x1400bb.jpg")


def test_upgrade_apple_artwork_leaves_larger_alone():
    url = "https://is1-ssl.mzstatic.com/image/thumb/a/b/c.jpg/3000x3000bb.jpg"
    assert upgrade_apple_artwork(url) == url


@pytest.mark.parametrize("url", [None, "", "https://example.com/cover.jpg"])
def test_upgrade_apple_artwork_passes_through_non_apple(url):
    assert upgrade_apple_artwork(url) == url


# ---------------------------------------------------------------------------
# best_cover
# ---------------------------------------------------------------------------

def test_best_cover_picks_card_and_hero_sizes():
    display, largest = best_cover({"252": "a", "500": "b", "1024": "c"})
    assert (display, largest) == ("b", "c")


def test_best_cover_falls_back_to_largest_when_all_small():
    display, largest = best_cover({"100": "a", "252": "b"})
    assert display == "b" and largest == "b"


def test_best_cover_accepts_a_bare_string():
    assert best_cover("https://img/x.jpg") == ("https://img/x.jpg", "https://img/x.jpg")


@pytest.mark.parametrize("images", [None, {}, "", [], {"bad": "x"}, {"500": None}])
def test_best_cover_degrades_to_none(images):
    assert best_cover(images) == (None, None)


# ---------------------------------------------------------------------------
# parse_sequence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1", 1.0),
    ("2.5", 2.5),
    ("1-3", 1.0),       # omnibus lands where its first book does
    (7, 7.0),
    (2.5, 2.5),
    ("  4 ", 4.0),
])
def test_parse_sequence_numbers(raw, expected):
    assert parse_sequence(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "Book", "Part One", []])
def test_parse_sequence_unparseable_is_none(raw):
    assert parse_sequence(raw) is None


# ---------------------------------------------------------------------------
# series_query_variants
# ---------------------------------------------------------------------------

def test_series_variants_drop_article_and_collective_noun():
    # "The Mistborn Saga" matches no book title; "Mistborn" matches all of them.
    assert series_query_variants("The Mistborn Saga") == [
        "The Mistborn Saga", "Mistborn Saga", "The Mistborn", "Mistborn",
    ]


def test_series_variants_keep_the_exact_name_first():
    assert series_query_variants("Discworld")[0] == "Discworld"


def test_series_variants_are_deduplicated():
    variants = series_query_variants("Dune")
    assert variants == ["Dune"]


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_series_variants_empty(raw):
    assert series_query_variants(raw) == []


# ---------------------------------------------------------------------------
# page_offset — guards the zero-indexed page bug
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("page,expected", [(1, 0), (2, 1), (3, 2), (10, 9)])
def test_page_offset_is_zero_based(page, expected):
    # Audible's page parameter is zero indexed. Sending 1 skips the whole first
    # page and looks exactly like "no results".
    assert page_offset(page) == expected


@pytest.mark.parametrize("bad", [0, -5, None, "", "abc", [], {}])
def test_page_offset_clamps_junk_to_first_page(bad):
    assert page_offset(bad) == 0


# ---------------------------------------------------------------------------
# marketplace_host
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code,host", [
    ("us", "api.audible.com"),
    ("uk", "api.audible.co.uk"),
    ("DE", "api.audible.de"),
    (" au ", "api.audible.com.au"),
])
def test_marketplace_host_known_codes(code, host):
    assert marketplace_host(code) == host


@pytest.mark.parametrize("code", [None, "", "zz", "not-a-store"])
def test_marketplace_host_unknown_falls_back_to_us(code):
    assert marketplace_host(code) == "api.audible.com"


# ---------------------------------------------------------------------------
# product_to_item
# ---------------------------------------------------------------------------

def test_product_to_item_full_payload():
    item = product_to_item(_product())
    assert item.asin == "B08G9PRS1K"
    assert item.title == "Project Hail Mary"
    assert item.subtitle == "A Novel"
    assert item.author_names == ["Andy Weir"]
    assert item.authors[0].asin == "B00G0WYW92"
    assert item.narrator_names == ["Ray Porter"]
    assert item.publisher == "Audible Studios"
    assert item.runtime_minutes == 1479
    assert item.runtime_formatted == "24 hrs 39 mins"
    assert item.release_date == "2021-05-04"
    assert item.language == "english"
    assert item.format_type == "unabridged"
    assert item.is_adult is False
    assert item.source == "audible"


def test_product_to_item_cleans_both_summaries():
    item = product_to_item(_product())
    assert item.summary == "A long description.\nSecond paragraph."
    assert item.short_summary == "A short blurb."


def test_product_to_item_falls_back_to_the_short_summary():
    # publisher_summary only ships with the detail response groups; a search
    # payload has only the merchandising blurb and must still describe the book.
    item = product_to_item(_product(publisher_summary=None))
    assert item.summary == "A short blurb."


def test_product_to_item_series_and_sequence():
    item = product_to_item(_product())
    assert item.series[0].title == "The Mistborn Saga"
    assert item.series[0].sequence == "1"
    assert item.series[0].sequence_value == 1.0
    assert item.series[0].asin == "B006K1P698"


def test_product_to_item_flattens_genres_without_repeats():
    item = product_to_item(_product())
    assert item.genres == ["Science Fiction & Fantasy", "Fantasy", "Epic", "Adventure"]


def test_product_to_item_covers():
    item = product_to_item(_product())
    assert item.cover_url == "https://img/500.jpg"
    assert item.cover_url_large == "https://img/1024.jpg"


def test_product_to_item_rating_distribution():
    rating = product_to_item(_product()).rating
    assert rating.average == 4.79
    assert rating.count == 98461
    assert rating.distribution == {"5": 81915, "4": 13650, "3": 2146, "2": 444, "1": 306}


def test_product_to_item_new_release_has_no_rating():
    # Audible reports average_rating 0.0 with zero ratings rather than null.
    # Keeping that would paint an empty star row on every brand new title.
    zeroed = _product(rating={"overall_distribution": {
        "average_rating": 0.0,
        "num_ratings": 0,
        "num_five_star_ratings": 0,
        "num_four_star_ratings": 0,
        "num_three_star_ratings": 0,
        "num_two_star_ratings": 0,
        "num_one_star_ratings": 0,
    }})
    assert product_to_item(zeroed).rating is None


def test_product_to_item_missing_rating_block():
    assert product_to_item(_product(rating=None)).rating is None


def test_product_to_item_falls_back_to_issue_date():
    item = product_to_item(_product(release_date=None))
    assert item.release_date == "2021-05-01"


def test_product_to_item_zero_runtime_reads_as_unknown():
    item = product_to_item(_product(runtime_length_min=0))
    assert item.runtime_minutes is None
    assert item.runtime_formatted == ""


def test_product_to_item_minimal_payload_survives():
    item = product_to_item({"asin": "B1", "title": "Bare"})
    assert item.asin == "B1"
    assert item.authors == [] and item.narrators == [] and item.series == []
    assert item.rating is None and item.cover_url is None and item.sample_url is None
    assert item.runtime_formatted == ""


@pytest.mark.parametrize("bad", [
    None, "", [], 42,
    {"title": "No asin"},
    {"asin": "B1"},
    {"asin": "", "title": "Empty asin"},
    {"asin": "B1", "title": "   "},
])
def test_product_to_item_rejects_unusable_payloads(bad):
    assert product_to_item(bad) is None


def test_product_to_item_skips_nameless_contributors():
    item = product_to_item(_product(authors=[{"name": ""}, {"asin": "x"}, {"name": "Real"}]))
    assert item.author_names == ["Real"]


def test_product_to_item_ignores_malformed_nested_blocks():
    item = product_to_item(_product(
        authors="not a list",
        series=[None, {"no_title": 1}],
        category_ladders=[{"ladder": [None, "junk"]}],
    ))
    assert item.authors == [] and item.series == [] and item.genres == []


def test_item_to_dict_carries_everything_the_ui_needs():
    payload = product_to_item(_product()).to_dict()
    for key in (
        "asin", "title", "subtitle", "authors", "narrators", "author_names",
        "narrator_names", "series", "publisher", "summary", "short_summary",
        "release_date", "runtime_minutes", "runtime_formatted", "cover_url",
        "cover_url_large", "sample_url", "rating", "genres", "language",
        "format_type", "is_adult", "source",
    ):
        assert key in payload, f"{key} missing from the audiobook payload"
    assert payload["rating"]["distribution"]["5"] == 81915
    assert payload["series"][0]["sequence_value"] == 1.0


# ---------------------------------------------------------------------------
# apple_result_to_item
# ---------------------------------------------------------------------------

def test_apple_result_to_item():
    item = apple_result_to_item(_apple_result())
    assert item.asin == "1565808256"
    assert item.source == "apple"
    assert item.title == "Project Hail Mary (Unabridged)"
    assert item.author_names == ["Andy Weir"]
    assert item.release_date == "2021-05-04"
    assert item.cover_url.endswith("/1400x1400bb.jpg")
    assert item.sample_url == "https://audio-ssl.itunes.apple.com/preview.m4a"
    assert item.summary == "A description."


def test_apple_result_has_none_of_the_audible_only_fields():
    # The reason Apple is a fallback and not a source: no narrator, no series,
    # no runtime, no ratings.
    item = apple_result_to_item(_apple_result())
    assert item.narrators == []
    assert item.series == []
    assert item.runtime_minutes is None
    assert item.rating is None


def test_apple_result_explicit_flag():
    assert apple_result_to_item(_apple_result(collectionExplicitness="explicit")).is_adult is True


@pytest.mark.parametrize("bad", [
    None, "", [], {"collectionName": "No id"}, {"collectionId": 1}, {"collectionId": None},
])
def test_apple_result_rejects_unusable_payloads(bad):
    assert apple_result_to_item(bad) is None


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------

def test_ttl_cache_hit_and_miss():
    cache = _TTLCache()
    assert cache.get("k") is None
    cache.set("k", {"v": 1}, ttl=60)
    assert cache.get("k") == {"v": 1}


def test_ttl_cache_expires():
    cache = _TTLCache()
    cache.set("k", "v", ttl=-1)
    assert cache.get("k") is None


def test_ttl_cache_clear():
    cache = _TTLCache()
    cache.set("k", "v", ttl=60)
    cache.clear()
    assert cache.get("k") is None


def test_ttl_cache_does_not_grow_without_bound():
    cache = _TTLCache()
    for i in range(_TTLCache._MAX_ENTRIES + 50):
        cache.set(f"k{i}", i, ttl=60)
    assert len(cache._store) <= _TTLCache._MAX_ENTRIES


# ---------------------------------------------------------------------------
# Request shaping
# ---------------------------------------------------------------------------

def _recorder(client, payload=None):
    """Patch _get_json and record (url, params) without touching the network.

    Accepts the shed-detection keyword too: every catalogue call now names the
    key a processed response must carry, and a stub that refused it would fail
    with a TypeError instead of exercising anything.
    """
    calls = []

    def fake(url, params, cache_ttl, label, result_key=None):
        calls.append({"url": url, "params": params, "ttl": cache_ttl,
                      "label": label, "result_key": result_key})
        return payload

    client._get_json = fake            # type: ignore[assignment]
    return calls


def test_search_omits_the_response_group_that_reranks_results(client):
    # product_extended_attrs visibly re-orders search results — the same
    # title=Mistborn query returns the English originals first without it and
    # the Spanish editions first with it. It belongs on detail calls only.
    calls = _recorder(client)
    client.search("mistborn")
    assert calls[0]["params"]["response_groups"] == _SEARCH_RESPONSE_GROUPS
    assert "product_extended_attrs" not in calls[0]["params"]["response_groups"]


def test_search_converts_the_page_number_to_a_zero_based_offset(client):
    calls = _recorder(client)
    client.search("dune", page=3)
    assert calls[0]["params"]["page"] == 2


def test_search_first_page_is_offset_zero(client):
    calls = _recorder(client)
    client.search("dune")
    assert calls[0]["params"]["page"] == 0


def test_search_clamps_the_page_size(client):
    # Audible 400s above 50, so an over-large limit must be clamped and not forwarded.
    calls = _recorder(client)
    client.search("dune", limit=500)
    assert calls[0]["params"]["num_results"] == _MAX_PAGE_SIZE


@pytest.mark.parametrize("mode,param", [
    ("keywords", "keywords"), ("title", "title"),
    ("author", "author"), ("narrator", "narrator"),
    ("NARRATOR", "narrator"), (" title ", "title"),
])
def test_search_maps_the_mode_to_the_right_parameter(client, mode, param):
    calls = _recorder(client)
    client.search("q", mode)
    assert param in calls[0]["params"]


@pytest.mark.parametrize("mode", ["", None, "isbn", "publisher"])
def test_search_falls_back_to_keywords_for_unknown_modes(client, mode):
    # Forwarding an unknown parameter name is a guaranteed 400 from the catalog.
    calls = _recorder(client)
    client.search("q", mode)
    assert "keywords" in calls[0]["params"]


@pytest.mark.parametrize("query", ["", "   ", None])
def test_search_short_circuits_an_empty_query(client, query):
    # An empty query against the catalog returns the entire storefront.
    calls = _recorder(client)
    assert client.search(query) == []
    assert calls == []


def test_search_only_sends_whitelisted_sorts(client):
    calls = _recorder(client)
    client.search("dune", sort="bestsellers")
    assert calls[0]["params"]["products_sort_by"] == "BestSellers"

    calls.clear()
    client.search("dune", sort="by-vibes")
    assert "products_sort_by" not in calls[0]["params"]


def test_search_targets_the_requested_marketplace(client):
    calls = _recorder(client)
    client.search("dune", marketplace="uk")
    assert calls[0]["url"].startswith("https://api.audible.co.uk/1.0/catalog/products")


def test_search_parses_the_products_array(client):
    _recorder(client, {"products": [_product(), {"asin": "", "title": "junk"}]})
    items = client.search("mistborn")
    assert len(items) == 1 and items[0].title == "Project Hail Mary"


def test_get_book_asks_for_the_long_description(client):
    calls = _recorder(client, {"product": _product()})
    item = client.get_book("B08G9PRS1K")
    assert calls[0]["params"]["response_groups"] == _DETAIL_RESPONSE_GROUPS
    assert "product_extended_attrs" in calls[0]["params"]["response_groups"]
    assert calls[0]["url"].endswith("/products/B08G9PRS1K")
    assert item.title == "Project Hail Mary"


@pytest.mark.parametrize("asin", ["", "   ", None])
def test_get_book_short_circuits_an_empty_asin(client, asin):
    calls = _recorder(client)
    assert client.get_book(asin) is None
    assert calls == []


def test_get_book_returns_none_when_the_lookup_fails(client):
    _recorder(client, None)
    assert client.get_book("B1") is None


def test_get_book_returns_none_for_an_empty_product(client):
    _recorder(client, {"product": None})
    assert client.get_book("B1") is None


def test_batch_lookup_sends_one_comma_list(client):
    calls = _recorder(client, {"products": [_product()]})
    client.get_books_by_asins(["B1", " B2 ", "", None, "B3"])
    assert calls[0]["params"]["asins"] == "B1,B2,B3"
    assert len(calls) == 1


def test_batch_lookup_caps_at_the_page_size(client):
    calls = _recorder(client, {"products": []})
    client.get_books_by_asins([f"B{i}" for i in range(120)])
    assert len(calls[0]["params"]["asins"].split(",")) == _MAX_PAGE_SIZE


def test_batch_lookup_short_circuits_an_empty_list(client):
    calls = _recorder(client)
    assert client.get_books_by_asins([]) == []
    assert client.get_books_by_asins([None, "", "  "]) == []
    assert calls == []


def test_similar_reads_the_similar_products_key(client):
    # The sims endpoint returns similar_products, not products — reading the
    # wrong key returns an empty shelf with no error anywhere.
    calls = _recorder(client, {"similar_products": [_product()], "products": []})
    items = client.get_similar("B08G9PRS1K")
    assert calls[0]["url"].endswith("/products/B08G9PRS1K/sims")
    assert len(items) == 1


def test_similar_short_circuits_an_empty_asin(client):
    calls = _recorder(client)
    assert client.get_similar("") == []
    assert calls == []


def test_browse_sends_a_genre_only_when_given(client):
    calls = _recorder(client, {"products": []})
    # A numeric value is already an id and passes straight through.
    client.browse(category="18580606011")
    assert calls[0]["params"]["category_id"] == "18580606011"

    calls.clear()
    client.browse()
    assert "category_id" not in calls[0]["params"]


def test_browse_defaults_to_the_bestseller_sort(client):
    calls = _recorder(client, {"products": []})
    client.browse(sort="nonsense")
    assert calls[0]["params"]["products_sort_by"] == "BestSellers"


def test_browse_sends_no_query_at_all(client):
    # A sorted query with no keywords IS the storefront chart. Sending a query
    # term would turn a chart row into a search.
    calls = _recorder(client, {"products": []})
    client.browse()
    for key in ("keywords", "title", "author", "narrator"):
        assert key not in calls[0]["params"]


def test_bestsellers_and_new_releases_pin_their_sorts(client):
    calls = _recorder(client, {"products": []})
    client.get_bestsellers()
    client.get_new_releases()
    assert calls[0]["params"]["products_sort_by"] == "BestSellers"
    assert calls[1]["params"]["products_sort_by"] == "-ReleaseDate"


def test_get_categories_builds_the_tree(client):
    _recorder(client, {"categories": [
        {"id": "1", "name": "Fantasy", "children": [{"id": "2", "name": "Epic"}]},
        {"id": "3", "name": "History"},
    ]})
    tree = client.get_categories()
    assert tree == [
        {"id": "1", "name": "Fantasy", "children": [{"id": "2", "name": "Epic"}]},
        {"id": "3", "name": "History", "children": []},
    ]


def test_get_categories_drops_malformed_nodes(client):
    _recorder(client, {"categories": [
        None, "junk", {"name": "No id"}, {"id": "1"},
        {"id": "2", "name": "Good", "children": [None, {"id": "3"}, {"id": "4", "name": "Child"}]},
    ]})
    assert client.get_categories() == [
        {"id": "2", "name": "Good", "children": [{"id": "4", "name": "Child"}]},
    ]


def test_get_categories_survives_a_failed_call(client):
    _recorder(client, None)
    assert client.get_categories() == []


# ---------------------------------------------------------------------------
# Fallback hierarchy
# ---------------------------------------------------------------------------

def test_fallback_prefers_audible(client):
    with patch.object(client, "search", return_value=[product_to_item(_product())]) as audible, \
         patch.object(client, "search_apple") as apple:
        items, source = client.search_with_fallback("dune")
    assert source == "audible" and len(items) == 1
    audible.assert_called_once()
    apple.assert_not_called()


def test_fallback_reaches_apple_when_audible_is_empty(client):
    apple_item = apple_result_to_item(_apple_result())
    with patch.object(client, "search", return_value=[]), \
         patch.object(client, "search_apple", return_value=[apple_item]) as apple:
        items, source = client.search_with_fallback("obscure title")
    assert source == "apple" and items == [apple_item]
    apple.assert_called_once()


def test_fallback_reports_audible_when_both_are_empty(client):
    with patch.object(client, "search", return_value=[]), \
         patch.object(client, "search_apple", return_value=[]):
        items, source = client.search_with_fallback("nothing at all")
    assert items == [] and source == "audible"


def test_fallback_skips_apple_for_narrator_search(client):
    # Apple cannot search narrators at all, so falling back is a guaranteed
    # empty round trip that also mislabels the source.
    with patch.object(client, "search", return_value=[]), \
         patch.object(client, "search_apple") as apple:
        items, source = client.search_with_fallback("Ray Porter", "narrator")
    assert items == [] and source == "audible"
    apple.assert_not_called()


def test_fallback_skips_apple_past_the_first_page(client):
    with patch.object(client, "search", return_value=[]), \
         patch.object(client, "search_apple") as apple:
        client.search_with_fallback("dune", page=2)
    apple.assert_not_called()


def test_search_apple_short_circuits_an_empty_query(client):
    calls = _recorder(client)
    assert client.search_apple("") == []
    assert calls == []


def test_search_apple_parses_results(client):
    _recorder(client, {"results": [_apple_result(), {"no": "id"}]})
    items = client.search_apple("project hail mary")
    assert len(items) == 1 and items[0].source == "apple"


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------

def _series_book(asin, title, sequence, series_asin="S1", series_title="The Mistborn Saga"):
    return product_to_item(_product(
        asin=asin, title=title,
        series=[{"asin": series_asin, "sequence": sequence, "title": series_title}],
    ))


def test_series_filters_out_books_that_are_not_in_it(client):
    # A title search for a series name drags in unrelated books. Without the
    # series filter the shelf fills with them.
    haul = [
        _series_book("B1", "The Final Empire", "1"),
        _series_book("BX", "Mistletoe Murders", "1", series_asin="OTHER", series_title="Mistletoe"),
    ]
    with patch.object(client, "search", return_value=haul):
        items = client.get_series("The Mistborn Saga", series_asin="S1")
    assert [i.asin for i in items] == ["B1"]


def test_series_sorts_by_sequence_not_by_string(client):
    haul = [_series_book(f"B{n}", f"Book {n}", str(n)) for n in (10, 2, 1)]
    with patch.object(client, "search", return_value=haul):
        items = client.get_series("The Mistborn Saga", series_asin="S1")
    assert [i.series[0].sequence for i in items] == ["1", "2", "10"]


def test_series_places_half_numbered_novellas_correctly(client):
    haul = [
        _series_book("B3", "Oathbringer", "3"),
        _series_book("B25", "Edgedancer", "2.5"),
        _series_book("B2", "Words of Radiance", "2"),
    ]
    with patch.object(client, "search", return_value=haul):
        items = client.get_series("The Stormlight Archive", series_asin="S1")
    assert [i.title for i in items] == ["Words of Radiance", "Edgedancer", "Oathbringer"]


def test_series_keeps_unnumbered_companions_at_the_end(client):
    haul = [
        _series_book("BC", "Arcanum Unbounded", ""),
        _series_book("B1", "The Final Empire", "1"),
    ]
    with patch.object(client, "search", return_value=haul):
        items = client.get_series("The Mistborn Saga", series_asin="S1")
    assert [i.title for i in items] == ["The Final Empire", "Arcanum Unbounded"]


def test_series_puts_the_requested_series_first_on_each_item(client):
    # The Lost Metal is book 7 of the Mistborn Saga and book 4 of Wax & Wayne.
    # A card on the saga shelf has to show 7, not 4.
    item = product_to_item(_product(asin="B7", title="The Lost Metal", series=[
        {"asin": "WW", "sequence": "4", "title": "Wax and Wayne"},
        {"asin": "S1", "sequence": "7", "title": "The Mistborn Saga"},
    ]))
    with patch.object(client, "search", return_value=[item]):
        items = client.get_series("The Mistborn Saga", series_asin="S1")
    assert items[0].series[0].title == "The Mistborn Saga"
    assert items[0].series[0].sequence == "7"
    assert len(items[0].series) == 2


def test_series_matches_on_name_when_no_asin_is_known(client):
    haul = [
        _series_book("B1", "The Final Empire", "1", series_asin=None),
        _series_book("BX", "Unrelated", "1", series_asin=None, series_title="Something Else"),
    ]
    with patch.object(client, "search", return_value=haul):
        items = client.get_series("the mistborn saga")
    assert [i.asin for i in items] == ["B1"]


def test_series_widens_the_query_until_something_matches(client):
    # "The Mistborn Saga" matches no book title; "Mistborn" matches all of them.
    hits = [_series_book("B1", "The Final Empire", "1")]
    seen = []

    def fake_search(query, mode="keywords", **kwargs):
        seen.append(query)
        return hits if query == "Mistborn" else []

    with patch.object(client, "search", side_effect=fake_search):
        items = client.get_series("The Mistborn Saga", series_asin="S1")
    assert [i.asin for i in items] == ["B1"]
    assert seen == ["The Mistborn Saga", "Mistborn Saga", "The Mistborn", "Mistborn"]


def test_series_falls_back_to_keyword_search_last(client):
    hits = [_series_book("B1", "The Final Empire", "1")]
    modes = []

    def fake_search(query, mode="keywords", **kwargs):
        modes.append(mode)
        return hits if mode == "keywords" else []

    with patch.object(client, "search", side_effect=fake_search):
        items = client.get_series("The Mistborn Saga", series_asin="S1")
    assert [i.asin for i in items] == ["B1"]
    assert modes[-1] == "keywords"
    assert modes[:-1] == ["title"] * (len(modes) - 1)


def test_series_never_sends_the_series_asin_to_the_catalog(client):
    # series_asin is accepted by the catalog and then IGNORED as a filter — it
    # returns the unfiltered storefront, tens of thousands of unrelated titles.
    # It is a local matching key only.
    calls = _recorder(client, {"products": []})
    client.get_series("The Mistborn Saga", series_asin="S1")
    assert calls, "expected the series lookup to search"
    for call in calls:
        assert "series_asin" not in call["params"]


def test_series_deduplicates_repeated_books(client):
    haul = [_series_book("B1", "The Final Empire", "1"),
            _series_book("B1", "The Final Empire", "1")]
    with patch.object(client, "search", return_value=haul):
        items = client.get_series("The Mistborn Saga", series_asin="S1")
    assert len(items) == 1


def test_series_honours_the_limit(client):
    haul = [_series_book(f"B{n}", f"Book {n}", str(n)) for n in range(1, 11)]
    with patch.object(client, "search", return_value=haul):
        items = client.get_series("The Mistborn Saga", series_asin="S1", limit=3)
    assert len(items) == 3


@pytest.mark.parametrize("name", ["", "   ", None])
def test_series_short_circuits_an_empty_name(client, name):
    calls = _recorder(client)
    assert client.get_series(name) == []
    assert calls == []


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

def test_author_and_narrator_use_their_own_search_modes(client):
    calls = _recorder(client, {"products": []})
    client.get_by_author("Brandon Sanderson")
    client.get_by_narrator("Ray Porter")
    assert calls[0]["params"]["author"] == "Brandon Sanderson"
    assert calls[1]["params"]["narrator"] == "Ray Porter"
    assert calls[0]["params"]["products_sort_by"] == "BestSellers"


# ---------------------------------------------------------------------------
# Transport: caching, pacing, failure
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_identical_requests_share_one_round_trip(client):
    with patch.object(client._session, "get",
                      return_value=_FakeResponse({"products": []})) as session_get:
        client.search("dune")
        client.search("dune")
    assert session_get.call_count == 1


def test_a_different_marketplace_is_a_different_cache_key(client):
    with patch.object(client._session, "get",
                      return_value=_FakeResponse({"products": []})) as session_get:
        client.search("dune", marketplace="us")
        client.search("dune", marketplace="uk")
    assert session_get.call_count == 2


def test_clear_cache_forces_a_refetch(client):
    with patch.object(client._session, "get",
                      return_value=_FakeResponse({"products": []})) as session_get:
        client.search("dune")
        client.clear_cache()
        client.search("dune")
    assert session_get.call_count == 2


def test_a_network_failure_returns_an_empty_shelf(client):
    # Fail open. One dead shelf must not take the browse page down with it.
    with patch.object(client._session, "get", side_effect=OSError("boom")):
        assert client.search("dune") == []
        assert client.get_book("B1") is None
        assert client.browse() == []
        assert client.get_categories() == []


def test_a_non_object_payload_is_rejected(client):
    with patch.object(client._session, "get", return_value=_FakeResponse(["not", "a", "dict"])):
        assert client.search("dune") == []


def test_a_failed_response_is_not_cached(client):
    # Nothing is cached, so the second search really goes out again. The exact
    # count is retries x storefronts; what matters is that the second call was
    # not served from a cached failure.
    with patch.object(client._session, "get", side_effect=OSError("boom")) as session_get:
        client.search("dune")
        first_round = session_get.call_count
        client.search("dune")
    assert first_round > 0
    assert session_get.call_count == first_round * 2


def test_outbound_calls_are_paced(client):
    # The reservation model: two callers arriving together get two different
    # slots instead of both computing "no wait" and firing at once.
    with patch("core.audiobook_client.time.sleep") as sleep:
        first = client._next_request_at
        client._pace()
        client._pace()
    assert client._next_request_at > first
    assert sleep.called


def test_the_pacer_is_private_and_touches_no_shared_budget():
    # Audible is a metadata service, not an indexer. Spending a slot from the
    # shared prowlarr/slskd budget here would slow real downloads for nothing.
    import inspect
    import core.audiobook_client as module
    source = inspect.getsource(module)
    assert "prowlarr_throttle" not in source.replace("core.prowlarr_throttle or", "")
    assert "reserve_search_slot" not in source


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

def test_singleton_is_shared():
    # The TTL cache and the pacer only mean anything if every handler shares one.
    assert get_audiobook_client() is get_audiobook_client()

# ---------------------------------------------------------------------------
# Person profiles
# ---------------------------------------------------------------------------

def _person_book(asin, title, authors=("Andy Weir",), narrators=("Ray Porter",),
                 series=(), runtime=600, ratings=1000, average=4.5,
                 language="english", genres=("Science Fiction & Fantasy",)):
    """A product shaped for bibliography grouping.

    series is [(series_title, sequence), ...] so a test can say what a book
    belongs to without spelling out the whole payload.
    """
    return product_to_item(_product(
        asin=asin,
        title=title,
        authors=[{"name": n} for n in authors],
        narrators=[{"name": n} for n in narrators],
        series=[{"asin": f"S-{t}", "sequence": q, "title": t} for t, q in series],
        runtime_length_min=runtime,
        language=language,
        category_ladders=[{"root": "Genres",
                           "ladder": [{"id": str(i), "name": g} for i, g in enumerate(genres)]}],
        rating={"overall_distribution": {
            "average_rating": average,
            "num_ratings": ratings,
            "num_five_star_ratings": ratings,
            "num_four_star_ratings": 0,
            "num_three_star_ratings": 0,
            "num_two_star_ratings": 0,
            "num_one_star_ratings": 0,
        }},
    ))


# ---- is_credited ----

def test_credit_matches_the_named_person():
    book = _person_book("B1", "Artemis", authors=("Andy Weir",))
    assert is_credited(book, "Andy Weir", "author") is True
    assert is_credited(book, "andy weir", "author") is True
    assert is_credited(book, "  Andy Weir  ", "author") is True


def test_credit_checks_the_right_role():
    # A narrator query can drag in a title the person only wrote, and the other
    # way round. One wrong book on someone's page is more visible than a gap.
    book = _person_book("B1", "Artemis", authors=("Andy Weir",), narrators=("Ray Porter",))
    assert is_credited(book, "Andy Weir", "narrator") is False
    assert is_credited(book, "Ray Porter", "author") is False


def test_credit_is_not_a_substring_match():
    # "Andy Weir" must not match "Andy Weirstein".
    book = _person_book("B1", "Artemis", authors=("Andy Weirstein",))
    assert is_credited(book, "Andy Weir", "author") is False


@pytest.mark.parametrize("name", ["", "   ", None])
def test_credit_rejects_an_empty_name(name):
    assert is_credited(_person_book("B1", "Artemis"), name, "author") is False


# ---- group_by_series ----

def test_series_grouping_uses_the_most_specific_series():
    # Every Mistborn novel is also in The Cosmere. Filing each book under both
    # would list the same title twice under two headings.
    books = [
        _person_book("B1", "The Final Empire", series=[("The Cosmere", "1"), ("Mistborn", "1")]),
        _person_book("B2", "The Well of Ascension", series=[("The Cosmere", "2"), ("Mistborn", "2")]),
        _person_book("B3", "Elantris", series=[("The Cosmere", "3")]),
        _person_book("B4", "Warbreaker", series=[("The Cosmere", "4")]),
    ]
    groups, standalone = group_by_series(books)
    titles = {title: [b.title for b in group] for title, _, group in groups}
    assert titles["Mistborn"] == ["The Final Empire", "The Well of Ascension"]
    assert titles["The Cosmere"] == ["Elantris", "Warbreaker"]
    assert standalone == []


def test_series_grouping_sorts_by_sequence():
    books = [
        _person_book("B3", "Third", series=[("Saga", "10")]),
        _person_book("B1", "First", series=[("Saga", "1")]),
        _person_book("B2", "Second", series=[("Saga", "2")]),
    ]
    groups, _ = group_by_series(books)
    assert [b.title for b in groups[0][2]] == ["First", "Second", "Third"]


def test_series_grouping_keeps_unnumbered_entries_last():
    books = [
        _person_book("BC", "Companion", series=[("Saga", "")]),
        _person_book("B1", "First", series=[("Saga", "1")]),
        _person_book("B2", "Second", series=[("Saga", "2")]),
    ]
    groups, _ = group_by_series(books)
    assert [b.title for b in groups[0][2]] == ["First", "Second", "Companion"]


def test_a_series_of_one_is_a_standalone():
    # A lone book carrying a series tag is not a series, it is a book.
    books = [
        _person_book("B1", "Only One", series=[("Tiny Saga", "1")]),
        _person_book("B2", "No Series At All"),
    ]
    groups, standalone = group_by_series(books)
    assert groups == []
    assert sorted(b.title for b in standalone) == ["No Series At All", "Only One"]


def test_series_groups_are_ordered_by_size():
    books = [
        _person_book("A1", "A1", series=[("Small", "1")]),
        _person_book("A2", "A2", series=[("Small", "2")]),
        _person_book("B1", "B1", series=[("Big", "1")]),
        _person_book("B2", "B2", series=[("Big", "2")]),
        _person_book("B3", "B3", series=[("Big", "3")]),
    ]
    groups, _ = group_by_series(books)
    assert [title for title, _, _ in groups] == ["Big", "Small"]


def test_series_grouping_carries_the_series_asin():
    books = [
        _person_book("B1", "First", series=[("Saga", "1")]),
        _person_book("B2", "Second", series=[("Saga", "2")]),
    ]
    groups, _ = group_by_series(books)
    assert groups[0][1] == "S-Saga"


def test_series_grouping_handles_an_empty_bibliography():
    assert group_by_series([]) == ([], [])


# ---- collapse_editions ----

def test_duplicate_editions_collapse_to_one_slot():
    # Audible sells the standard narration and a dramatized adaptation as two
    # products at the same position; both listed makes a 6-book series read as 12.
    books = [
        _person_book("B1", "Alcatraz", series=[("Alcatraz", "1")], ratings=5065),
        _person_book("B2", "Alcatraz [Dramatized Adaptation]", series=[("Alcatraz", "1")], ratings=256),
        _person_book("B3", "The Scrivener's Bones", series=[("Alcatraz", "2")], ratings=2869),
        _person_book("B4", "Scrivener's [Dramatized Adaptation]", series=[("Alcatraz", "2")], ratings=150),
    ]
    kept = collapse_editions(books, "Alcatraz")
    assert sorted(b.asin for b in kept) == ["B1", "B3"]


def test_collapsing_keeps_the_most_heard_edition():
    books = [
        _person_book("QUIET", "Quiet edition", series=[("Saga", "1")], ratings=10),
        _person_book("MAIN", "Main narration", series=[("Saga", "1")], ratings=9000),
    ]
    assert [b.asin for b in collapse_editions(books, "Saga")] == ["MAIN"]


def test_collapsing_never_merges_unnumbered_entries():
    # With no position to share there is no way to know two are the same book.
    books = [
        _person_book("B1", "Companion One", series=[("Saga", "")]),
        _person_book("B2", "Companion Two", series=[("Saga", "")]),
    ]
    assert len(collapse_editions(books, "Saga")) == 2


def test_collapsing_ignores_other_series_positions():
    # The Lost Metal is book 7 of one series and 4 of another; the slot that
    # matters is the one in the series being collapsed.
    books = [
        _person_book("B1", "One", series=[("Other", "1"), ("Saga", "1")]),
        _person_book("B2", "Two", series=[("Other", "1"), ("Saga", "2")]),
    ]
    assert len(collapse_editions(books, "Saga")) == 2


# ---- collaborators ----

def test_author_page_counts_narrators():
    books = [
        _person_book("B1", "One", narrators=("Michael Kramer",)),
        _person_book("B2", "Two", narrators=("Michael Kramer", "Kate Reading")),
        _person_book("B3", "Three", narrators=("Kate Reading",)),
    ]
    assert count_collaborators(books, "author") == [
        {"name": "Michael Kramer", "count": 2},
        {"name": "Kate Reading", "count": 2},
    ]


def test_narrator_page_counts_authors():
    books = [
        _person_book("B1", "One", authors=("Dennis E. Taylor",)),
        _person_book("B2", "Two", authors=("Dennis E. Taylor",)),
        _person_book("B3", "Three", authors=("Andy Weir",)),
    ]
    assert count_collaborators(books, "narrator")[0] == {"name": "Dennis E. Taylor", "count": 2}


def test_collaborators_of_an_empty_bibliography():
    assert count_collaborators([], "author") == []


# ---- highlights ----

def test_highlights_ignore_a_high_average_on_few_ratings():
    # A novella with four five-star votes must not outrank a novel with 90,000.
    books = [
        _person_book("TINY", "Tiny novella", average=5.0, ratings=4),
        _person_book("REAL", "Words of Radiance", average=4.9, ratings=90287),
    ]
    assert [b.asin for b in pick_highlights(books)] == ["REAL"]


def test_highlights_fall_back_when_nothing_clears_the_bar():
    # A newer name with only lightly-rated titles still gets a shelf.
    books = [
        _person_book("B1", "One", average=4.2, ratings=5),
        _person_book("B2", "Two", average=4.8, ratings=7),
    ]
    assert [b.asin for b in pick_highlights(books)] == ["B2", "B1"]


def test_highlights_skip_unrated_titles():
    rated = _person_book("RATED", "Rated", average=4.5, ratings=900)
    unrated = product_to_item(_product(asin="NONE", title="Unrated", rating=None))
    assert [b.asin for b in pick_highlights([rated, unrated])] == ["RATED"]


def test_highlights_honour_the_limit():
    books = [_person_book(f"B{i}", f"Book {i}", ratings=100 + i) for i in range(20)]
    assert len(pick_highlights(books, limit=5)) == 5


# ---- marketplace_language ----

@pytest.mark.parametrize("code,language", [
    ("us", "english"), ("uk", "english"), ("DE", "german"), (" fr ", "french"),
])
def test_marketplace_language_known(code, language):
    assert marketplace_language(code) == language


@pytest.mark.parametrize("code", [None, "", "zz"])
def test_marketplace_language_unknown_means_do_not_filter(code):
    assert marketplace_language(code) == ""


# ---- get_person_profile ----

def _profile_client(client, books, monkey_pages=None):
    """Patch search so the profile builds off a fixed bibliography."""
    pages = monkey_pages if monkey_pages is not None else [books]

    def fake_search(query, mode="keywords", limit=None, marketplace=None, page=1, sort=None):
        index = max(0, int(page) - 1)
        return pages[index] if index < len(pages) else []

    client.search = fake_search    # type: ignore[assignment]
    return client


def test_profile_shape(client):
    books = [
        _person_book("B1", "The Final Empire", series=[("Mistborn", "1")], runtime=1479),
        _person_book("B2", "The Well of Ascension", series=[("Mistborn", "2")], runtime=1736),
    ]
    _profile_client(client, books)
    profile = client.get_person_profile("Andy Weir", "author")

    assert profile["name"] == "Andy Weir"
    assert profile["role"] == "author"
    assert profile["total_books"] == 2
    assert profile["total_runtime_minutes"] == 3215
    assert profile["runtime_formatted"] == "53 hrs 35 mins"
    assert profile["series"][0]["title"] == "Mistborn"
    assert [b["title"] for b in profile["series"][0]["books"]] == [
        "The Final Empire", "The Well of Ascension",
    ]
    assert profile["collaborators"][0]["name"] == "Ray Porter"
    assert "Science Fiction & Fantasy" in profile["genres"]


def test_profile_drops_books_the_person_is_not_on(client):
    books = [
        _person_book("MINE", "Mine", authors=("Andy Weir",)),
        _person_book("THEIRS", "Theirs", authors=("Someone Else",)),
    ]
    _profile_client(client, books)
    profile = client.get_person_profile("Andy Weir", "author")
    assert profile["total_books"] == 1
    assert profile["standalone"][0]["asin"] == "MINE"


def test_profile_filters_out_other_language_editions(client):
    # A person search returns every translation Audible carries, which puts
    # "Trilogía Original Mistborn" beside "The Mistborn Saga" as if they were
    # different series.
    books = [
        _person_book("EN1", "The Final Empire", series=[("Mistborn", "1")], language="english"),
        _person_book("EN2", "The Hero of Ages", series=[("Mistborn", "2")], language="english"),
        _person_book("ES1", "El Imperio Final", series=[("Trilogía Mistborn", "1")], language="spanish"),
    ]
    _profile_client(client, books)
    profile = client.get_person_profile("Andy Weir", "author", marketplace="us")
    assert profile["total_books"] == 2
    assert [g["title"] for g in profile["series"]] == ["Mistborn"]


def test_profile_falls_back_to_translations_when_nothing_else_is_left(client):
    # A narrator who only works in one language would otherwise get a blank page.
    books = [
        _person_book("ES1", "El Imperio Final", language="spanish"),
        _person_book("ES2", "El Pozo de la Ascensión", language="spanish"),
    ]
    _profile_client(client, books)
    profile = client.get_person_profile("Andy Weir", "author", marketplace="us")
    assert profile["total_books"] == 2


def test_profile_keeps_every_language_on_an_unknown_marketplace(client):
    books = [
        _person_book("EN1", "English", language="english"),
        _person_book("ES1", "Spanish", language="spanish"),
    ]
    _profile_client(client, books)
    profile = client.get_person_profile("Andy Weir", "author", marketplace="zz")
    assert profile["total_books"] == 2


def test_profile_pages_past_the_result_cap(client):
    # A prolific author runs well past 50; a bibliography that stops there
    # silently hides half a career.
    page_one = [_person_book(f"A{i}", f"Book A{i}") for i in range(_MAX_PAGE_SIZE)]
    page_two = [_person_book(f"B{i}", f"Book B{i}") for i in range(10)]
    _profile_client(client, [], monkey_pages=[page_one, page_two])
    profile = client.get_person_profile("Andy Weir", "author")
    assert profile["total_books"] == _MAX_PAGE_SIZE + 10


def test_profile_stops_paging_on_a_short_page(client):
    calls = []

    def counting_search(query, mode="keywords", limit=None, marketplace=None, page=1, sort=None):
        calls.append(page)
        return [_person_book("B1", "Only one")] if page == 1 else []

    client.search = counting_search    # type: ignore[assignment]
    client.get_person_profile("Andy Weir", "author")
    assert calls == [1]


def test_profile_honours_the_book_cap(client):
    # Distinct ASINs per page, or de-duplication caps the count before max_books does.
    pages = [
        [_person_book(f"P{p}-{i}", f"Book {p}-{i}") for i in range(_MAX_PAGE_SIZE)]
        for p in range(4)
    ]
    _profile_client(client, [], monkey_pages=pages)
    profile = client.get_person_profile("Andy Weir", "author", max_books=60)
    assert profile["total_books"] == 60


def test_profile_deduplicates_across_pages(client):
    shared = _person_book("SAME", "Same book")
    _profile_client(client, [], monkey_pages=[[shared], [shared]])
    assert client.get_person_profile("Andy Weir", "author")["total_books"] == 1


@pytest.mark.parametrize("name", ["", "   ", None])
def test_profile_short_circuits_an_empty_name(client, name):
    calls = _recorder(client)
    profile = client.get_person_profile(name, "author")
    assert profile["total_books"] == 0
    assert profile["series"] == [] and profile["standalone"] == []
    assert calls == []


def test_profile_rejects_an_unknown_role(client):
    _profile_client(client, [_person_book("B1", "One")])
    assert client.get_person_profile("Andy Weir", "illustrator")["role"] == "author"


def test_profile_of_an_unknown_person_has_the_full_shape(client):
    _profile_client(client, [])
    profile = client.get_person_profile("Nobody At All", "narrator")
    for key in ("name", "role", "total_books", "total_runtime_minutes", "runtime_formatted",
                "genres", "collaborators", "series", "standalone", "highlights"):
        assert key in profile
    assert profile["role"] == "narrator"
    assert profile["total_books"] == 0


# ---------------------------------------------------------------------------
# Storefront sheds
#
# Audible answers 200 with an empty body when it is shedding load, and it does
# it per-storefront: the US store returned nothing for every search while the UK
# and AU stores answered the same queries from the same machine a second later,
# and the US store recovered on its own. Read as "no results" that shows an
# empty catalogue; CACHED as "no results" it keeps the catalogue empty for an
# hour after the store recovers. Both happened.
# ---------------------------------------------------------------------------

def test_a_real_no_match_is_a_processed_response():
    # A healthy store answering "nothing matched" still carries the key.
    assert response_was_processed({"products": [], "response_groups": ["product_desc"]}) is True


def test_a_shed_response_is_not_processed():
    # The signature seen live: no products key at all, no groups echoed back.
    assert response_was_processed({"response_groups": [], "total_results": 0}) is False


@pytest.mark.parametrize("data", [None, {}, [], "nope", {"total_results": 0}])
def test_unprocessed_shapes(data):
    assert response_was_processed(data) is False


@pytest.mark.parametrize("key", ["product", "similar_products", "categories"])
def test_other_endpoints_declare_their_own_result_key(key):
    assert response_was_processed({key: {}}, key) is True
    assert response_was_processed({"response_groups": []}, key) is False


def test_a_shed_response_is_never_cached(client):
    # The bug this guards: one shed request poisoned the cache and the page
    # stayed empty long after the storefront recovered.
    shed = _FakeResponse({"response_groups": [], "total_results": 0})
    with patch.object(client._session, "get", return_value=shed) as session_get:
        client.search("dune")
        first = session_get.call_count
        client.search("dune")
    assert session_get.call_count > first


def test_a_genuine_no_match_is_cached(client):
    # The other half: a real "nothing matched" must NOT be retried forever.
    empty = _FakeResponse({"products": [], "response_groups": ["product_desc"]})
    with patch.object(client._session, "get", return_value=empty) as session_get:
        assert client.search("zzzznothing") == []
        first = session_get.call_count
        assert client.search("zzzznothing") == []
    assert session_get.call_count == first


def test_a_shed_falls_through_to_another_storefront(client):
    # What actually fixed the page: ask a store that will answer.
    shed = _FakeResponse({"response_groups": [], "total_results": 0})
    good = _FakeResponse({"products": [_product()], "response_groups": ["product_desc"]})
    seen = []

    def by_host(url, params=None, timeout=None):
        seen.append(url)
        return good if "co.uk" in url else shed

    with patch.object(client._session, "get", side_effect=by_host):
        items = client.search("dune", marketplace="us")
    assert [i.title for i in items] == ["Project Hail Mary"]
    assert any("api.audible.com/" in url for url in seen)
    assert any("api.audible.co.uk" in url for url in seen)


def test_a_shed_is_retried_before_giving_up_on_a_store(client):
    shed = _FakeResponse({"response_groups": [], "total_results": 0})
    with patch.object(client._session, "get", return_value=shed) as session_get, \
         patch("core.audiobook_client.time.sleep"):
        client.search("dune", marketplace="de")
    # Germany has no English fallback, so every call went to the one store.
    assert session_get.call_count >= 2
    assert all("audible.de" in call.args[0] for call in session_get.call_args_list)


# ---------------------------------------------------------------------------
# Storefront fallback chain
# ---------------------------------------------------------------------------

def test_the_us_store_falls_back_to_english_stores():
    assert marketplace_chain("us") == ["us", "uk", "au"]


@pytest.mark.parametrize("code", ["de", "fr", "it", "es", "jp"])
def test_non_english_stores_have_no_fallback(code):
    # Falling back from Germany to the US would answer a German query with
    # English books, which is a worse failure than showing nothing.
    assert marketplace_chain(code) == [code]


@pytest.mark.parametrize("code", [None, "", "zz"])
def test_an_unknown_store_falls_back_to_the_default_chain(code):
    assert marketplace_chain(code)[0] == "us"


# ---------------------------------------------------------------------------
# Genres are named, not numbered
# ---------------------------------------------------------------------------

def test_category_names_fold_across_storefront_spelling():
    # The US sells "Comedy & Humor", the UK sells "Comedy & Humour".
    assert normalize_category_name("Comedy & Humor") == normalize_category_name("Comedy & Humour")


def test_category_names_fold_case_and_ampersands():
    assert (normalize_category_name("Science Fiction & Fantasy")
            == normalize_category_name("science fiction and fantasy"))


def test_a_numeric_category_passes_straight_through(client):
    _recorder(client, {"categories": []})
    assert client.resolve_category("18580606011", "us") == "18580606011"


def test_a_genre_name_resolves_against_the_store_being_asked(client):
    _recorder(client, {"categories": [
        {"id": "19378442031", "name": "Science Fiction & Fantasy", "children": []},
    ]})
    assert client.resolve_category("Science Fiction & Fantasy", "uk") == "19378442031"


def test_a_child_genre_resolves_too(client):
    _recorder(client, {"categories": [
        {"id": "1", "name": "Science Fiction & Fantasy",
         "children": [{"id": "2", "name": "Epic"}]},
    ]})
    assert client.resolve_category("Epic", "us") == "2"


def test_a_genre_the_store_does_not_have_resolves_to_nothing(client):
    _recorder(client, {"categories": [{"id": "1", "name": "History", "children": []}]})
    assert client.resolve_category("Underwater Basket Weaving", "us") == ""


def test_browse_skips_a_store_that_lacks_the_genre(client):
    # Asking a store without that genre for "the whole storefront" and calling
    # the result a genre shelf would be worse than skipping it.
    calls = _recorder(client, {"products": []})
    with patch.object(client, "resolve_category", return_value=""):
        assert client.browse(category="Nonexistent Genre") == []
    assert calls == []


def test_browse_resolves_the_genre_per_storefront(client):
    resolved = []

    def fake_resolve(category, code):
        resolved.append((category, code))
        return "" if code == "us" else "19378442031"

    calls = _recorder(client, {"products": []})
    with patch.object(client, "resolve_category", side_effect=fake_resolve):
        client.browse(category="Science Fiction & Fantasy", marketplace="us")
    # The US had no id for it, so the UK was asked with the UK's own id.
    assert ("Science Fiction & Fantasy", "us") in resolved
    assert calls[0]["params"]["category_id"] == "19378442031"
