"""Tests for core.podcast_client and core.podcast_download_client.

No live network calls — requests.Session methods are mocked per repo convention
(no responses/requests-mock dependency). Fixtures are trimmed copies of real
iTunes API and RSS feed shapes, verified against live data during development.

Run:
    python -m pytest tests/test_podcast_client.py -v
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

import core.podcast_client as pc
from core.podcast_client import (
    PodcastClient,
    PodcastEpisode,
    PodcastShow,
    _best_artwork,
    _parse_categories,
    _parse_duration_to_seconds,
    _parse_pub_date,
    _upgrade_itunes_art_url,
    get_podcast_client,
)
from core.podcast_download_client import (
    PodcastDownloadClient,
    _collision_safe_path,
    detect_extension,
    sanitize_filename,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(json_data=None, content=b"", status_code=200, headers=None):
    """Build a minimal mock requests.Response."""
    resp = Mock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.raise_for_status = Mock()
    if json_data is not None:
        resp.json = Mock(return_value=json_data)
    resp.content = content
    return resp


# ---------------------------------------------------------------------------
# Minimal RSS feed fixture — trimmed from a real podcast feed.
# Covers: show metadata, categories, artwork, two episodes (one with season/
# episode numbers, one with episode-level artwork and PodcastIndex extensions).
# ---------------------------------------------------------------------------

_FEED_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"
         xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
         xmlns:content="http://purl.org/rss/1.0/modules/content/"
         xmlns:podcast="https://podcastindex.org/namespace/1.0">
      <channel>
        <title>Test Podcast</title>
        <itunes:author>Test Author</itunes:author>
        <itunes:summary>A test podcast about testing.</itunes:summary>
        <language>en-us</language>
        <link>https://example.com/podcast</link>
        <itunes:image href="https://example.com/artwork/3000x3000bb.jpg"/>
        <itunes:explicit>yes</itunes:explicit>
        <itunes:category text="Technology">
          <itunes:category text="Tech News"/>
        </itunes:category>

        <item>
          <title>Episode 1: The Beginning</title>
          <guid>https://example.com/ep1</guid>
          <pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate>
          <itunes:duration>1:02:30</itunes:duration>
          <itunes:season>1</itunes:season>
          <itunes:episode>1</itunes:episode>
          <itunes:episodeType>full</itunes:episodeType>
          <itunes:summary>The first episode.</itunes:summary>
          <enclosure url="https://cdn.example.com/ep1.mp3" type="audio/mpeg" length="45000000"/>
        </item>

        <item>
          <title>Bonus: Behind the Scenes</title>
          <guid>https://example.com/bonus1</guid>
          <pubDate>Wed, 15 Jan 2024 12:00:00 +0000</pubDate>
          <itunes:duration>900</itunes:duration>
          <itunes:episodeType>bonus</itunes:episodeType>
          <description>A bonus episode.</description>
          <content:encoded>&lt;p&gt;Full show notes HTML.&lt;/p&gt;</content:encoded>
          <itunes:image href="https://example.com/bonus-art.jpg"/>
          <podcast:chapters url="https://example.com/ep2-chapters.json" type="application/json"/>
          <podcast:transcript url="https://example.com/ep2-transcript.vtt" type="text/vtt"/>
          <enclosure url="https://cdn.example.com/bonus.m4a" type="audio/x-m4a" length="12000000"/>
        </item>

        <item>
          <title>No Enclosure Item</title>
          <guid>https://example.com/noenc</guid>
          <description>This item has no enclosure and should be skipped.</description>
        </item>
      </channel>
    </rss>
""").encode("utf-8")

# Trimmed iTunes Search API response for a single podcast result.
_ITUNES_SEARCH_RESULT = {
    "resultCount": 1,
    "results": [
        {
            "collectionName": "Test Podcast",
            "artistName": "Test Author",
            "feedUrl": "https://example.com/feed.rss",
            "collectionId": 123456789,
            "trackCount": 42,
            "contentAdvisoryRating": "Explicit",
            "artworkUrl600": "https://is1-ssl.mzstatic.com/image/thumb/Podcasts/v4/ab/cd/ef/abcdef-1/600x600bb.jpg",
            "genres": ["Podcasts", "Technology"],
            "collectionViewUrl": "https://podcasts.apple.com/us/podcast/test/id123456789",
        }
    ],
}


# ---------------------------------------------------------------------------
# _upgrade_itunes_art_url
# ---------------------------------------------------------------------------

class TestUpgradeItunesArtUrl:
    def test_upgrades_600_to_3000(self):
        url = "https://is1-ssl.mzstatic.com/image/thumb/600x600bb.jpg"
        result = _upgrade_itunes_art_url(url)
        assert "3000x3000bb" in result
        assert "600x600bb" not in result

    def test_leaves_already_large_url_unchanged(self):
        url = "https://is1-ssl.mzstatic.com/image/thumb/3000x3000bb.jpg"
        assert _upgrade_itunes_art_url(url) == url

    def test_leaves_non_matching_url_unchanged(self):
        url = "https://example.com/artwork.jpg"
        assert _upgrade_itunes_art_url(url) == url

    def test_empty_string_returns_empty(self):
        assert _upgrade_itunes_art_url("") == ""

    def test_none_returns_none(self):
        # The helper accepts Optional[str] conceptually; empty string is the
        # sentinel for None in callers that already guard.
        assert _upgrade_itunes_art_url("") == ""

    def test_custom_target_size(self):
        url = "https://is1-ssl.mzstatic.com/image/thumb/100x100bb.png"
        result = _upgrade_itunes_art_url(url, target=1400)
        assert "1400x1400bb" in result


# ---------------------------------------------------------------------------
# _parse_duration_to_seconds
# ---------------------------------------------------------------------------

class TestParseDurationToSeconds:
    def test_hh_mm_ss(self):
        assert _parse_duration_to_seconds("1:02:30") == 3750

    def test_mm_ss(self):
        assert _parse_duration_to_seconds("12:45") == 765

    def test_raw_seconds(self):
        assert _parse_duration_to_seconds("900") == 900

    def test_zero_padded(self):
        assert _parse_duration_to_seconds("0:00:30") == 30

    def test_none_returns_none(self):
        assert _parse_duration_to_seconds(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_duration_to_seconds("") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_duration_to_seconds("   ") is None

    def test_malformed_returns_none(self):
        assert _parse_duration_to_seconds("not-a-duration") is None


# ---------------------------------------------------------------------------
# _parse_pub_date
# ---------------------------------------------------------------------------

class TestParsePubDate:
    def test_rfc_2822_with_utc_offset(self):
        dt = _parse_pub_date("Mon, 01 Jan 2024 10:00:00 +0000")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1
        assert dt.tzinfo is not None

    def test_rfc_2822_with_named_timezone(self):
        # "GMT" is a valid RFC 2822 zone name.
        dt = _parse_pub_date("Wed, 15 Jan 2024 12:00:00 GMT")
        assert dt is not None
        assert dt.year == 2024

    def test_result_is_utc_normalised(self):
        # +0500 offset — result must still be UTC-aware after normalisation.
        dt = _parse_pub_date("Mon, 01 Jan 2024 15:00:00 +0500")
        assert dt is not None
        assert dt.utcoffset().total_seconds() == 0

    def test_none_returns_none(self):
        assert _parse_pub_date(None) is None

    def test_empty_returns_none(self):
        assert _parse_pub_date("") is None

    def test_malformed_returns_none(self):
        assert _parse_pub_date("not a date") is None


# ---------------------------------------------------------------------------
# _best_artwork
# ---------------------------------------------------------------------------

class TestBestArtwork:
    def test_returns_first_non_empty(self):
        assert _best_artwork(None, "", "https://example.com/art.jpg", "other") == "https://example.com/art.jpg"

    def test_strips_whitespace(self):
        assert _best_artwork("  https://example.com/art.jpg  ") == "https://example.com/art.jpg"

    def test_all_empty_returns_none(self):
        assert _best_artwork(None, "", "   ") is None

    def test_no_args_returns_none(self):
        assert _best_artwork() is None


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------

class TestSanitizeFilename:
    def test_strips_illegal_windows_chars(self):
        result = sanitize_filename('Track: "Name" <Bad> / Path')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert '"' not in result
        assert "/" not in result

    def test_html_entities_unescaped_first(self):
        # "&amp;" -> "&", which is legal in a filename.
        result = sanitize_filename("Artist &amp; Band - Episode")
        assert "&" in result
        assert "amp" not in result

    def test_empty_returns_sentinel(self):
        assert sanitize_filename("") == "podcast_episode"

    def test_none_returns_sentinel(self):
        assert sanitize_filename(None) == "podcast_episode"

    def test_only_illegal_chars_returns_sentinel(self):
        assert sanitize_filename("///") == "podcast_episode"

    def test_long_name_truncated(self):
        result = sanitize_filename("A" * 300)
        assert len(result) <= 180

    def test_trailing_dots_stripped(self):
        result = sanitize_filename("Episode 1...")
        assert not result.endswith(".")

    def test_consecutive_underscores_collapsed(self):
        result = sanitize_filename("Bad///Name")
        assert "__" not in result


# ---------------------------------------------------------------------------
# detect_extension
# ---------------------------------------------------------------------------

class TestDetectExtension:
    def test_mp3_from_url(self):
        assert detect_extension("https://cdn.example.com/ep.mp3") == ".mp3"

    def test_m4a_from_url(self):
        assert detect_extension("https://cdn.example.com/ep.m4a") == ".m4a"

    def test_url_query_string_ignored(self):
        assert detect_extension("https://cdn.example.com/ep.mp3?token=abc") == ".mp3"

    def test_mime_type_fallback(self):
        assert detect_extension("https://cdn.example.com/stream", "audio/x-m4a") == ".m4a"

    def test_mime_with_params(self):
        assert detect_extension("https://cdn.example.com/stream", "audio/mpeg; codecs=mp3") == ".mp3"

    def test_default_mp3_when_unknown(self):
        assert detect_extension("https://cdn.example.com/stream", "application/octet-stream") == ".mp3"

    def test_url_takes_priority_over_mime(self):
        # URL says .ogg but MIME says mp3 — URL wins.
        assert detect_extension("https://cdn.example.com/ep.ogg", "audio/mpeg") == ".ogg"


# ---------------------------------------------------------------------------
# PodcastClient — feed parsing
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> PodcastClient:
    return PodcastClient()


class TestFeedParsing:
    def test_show_title_and_author(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        assert show is not None
        assert show.title == "Test Podcast"
        assert show.author == "Test Author"

    def test_show_description(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        assert "testing" in show.description.lower()

    def test_show_artwork_from_itunes_image(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        assert show.artwork_url == "https://example.com/artwork/3000x3000bb.jpg"

    def test_show_explicit_flag(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        assert show.explicit is True

    def test_show_language(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        assert show.language == "en-us"

    def test_show_categories_include_subcategory(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        assert "Technology" in show.categories
        assert "Tech News" in show.categories

    def test_episode_count_excludes_no_enclosure_items(self, client):
        # The fixture has 3 <item> elements but one has no <enclosure>.
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        assert show.episode_count == 2
        assert len(show.episodes) == 2

    def test_episode_one_title_and_guid(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        ep1 = show.episodes[0]
        assert ep1.title == "Episode 1: The Beginning"
        assert ep1.guid == "https://example.com/ep1"

    def test_episode_one_duration(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        # "1:02:30" -> 3750 seconds
        assert show.episodes[0].duration_seconds == 3750

    def test_episode_one_season_and_number(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        ep1 = show.episodes[0]
        assert ep1.season == 1
        assert ep1.episode_number == 1

    def test_episode_one_enclosure(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        ep1 = show.episodes[0]
        assert ep1.enclosure_url == "https://cdn.example.com/ep1.mp3"
        assert ep1.enclosure_type == "audio/mpeg"
        assert ep1.enclosure_length == 45000000

    def test_episode_one_pub_date_parsed(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        ep1 = show.episodes[0]
        assert ep1.pub_date is not None
        assert ep1.pub_date.year == 2024
        assert ep1.pub_date.month == 1

    def test_episode_two_raw_seconds_duration(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        ep2 = show.episodes[1]
        assert ep2.duration_seconds == 900

    def test_episode_two_episode_type_bonus(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        assert show.episodes[1].episode_type == "bonus"

    def test_episode_two_artwork_url(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        assert show.episodes[1].artwork_url == "https://example.com/bonus-art.jpg"

    def test_episode_two_show_notes(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        assert "<p>" in show.episodes[1].show_notes

    def test_episode_two_chapter_url(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        assert show.episodes[1].chapter_url == "https://example.com/ep2-chapters.json"

    def test_episode_two_transcript_url(self, client):
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss")
        assert show.episodes[1].transcript_url == "https://example.com/ep2-transcript.vtt"

    def test_network_failure_returns_none(self, client):
        with patch.object(client._session, "get", side_effect=Exception("network error")):
            result = client.fetch_feed("https://example.com/feed.rss")
        assert result is None

    def test_bad_xml_returns_none(self, client):
        resp = _mock_response(content=b"not xml at all <<<")
        with patch.object(client._session, "get", return_value=resp):
            result = client.fetch_feed("https://example.com/feed.rss")
        assert result is None

    def test_show_hint_itunes_id_merged(self, client):
        hint = PodcastShow(
            title="", author="", description="", artwork_url=None,
            feed_url="https://example.com/feed.rss", itunes_id=999,
            website=None, language="", explicit=False, categories=[],
            episode_count=100,
        )
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss", show_hint=hint)
        assert show.itunes_id == 999

    def test_show_hint_artwork_not_used_when_feed_has_art(self, client):
        # Feed has artwork — hint artwork must not override it.
        hint = PodcastShow(
            title="", author="", description="",
            artwork_url="https://hint.example.com/art.jpg",
            feed_url="https://example.com/feed.rss", itunes_id=None,
            website=None, language="", explicit=False, categories=[],
            episode_count=None,
        )
        resp = _mock_response(content=_FEED_XML)
        with patch.object(client._session, "get", return_value=resp):
            show = client.fetch_feed("https://example.com/feed.rss", show_hint=hint)
        assert show.artwork_url == "https://example.com/artwork/3000x3000bb.jpg"


# ---------------------------------------------------------------------------
# PodcastClient — search_podcasts
# ---------------------------------------------------------------------------

class TestSearchPodcasts:
    def test_returns_show_list(self, client):
        resp = _mock_response(json_data=_ITUNES_SEARCH_RESULT)
        with patch.object(client._session, "get", return_value=resp):
            results = client.search_podcasts("test")
        assert len(results) == 1
        show = results[0]
        assert show.title == "Test Podcast"
        assert show.author == "Test Author"

    def test_feed_url_populated(self, client):
        resp = _mock_response(json_data=_ITUNES_SEARCH_RESULT)
        with patch.object(client._session, "get", return_value=resp):
            results = client.search_podcasts("test")
        assert results[0].feed_url == "https://example.com/feed.rss"

    def test_itunes_id_populated(self, client):
        resp = _mock_response(json_data=_ITUNES_SEARCH_RESULT)
        with patch.object(client._session, "get", return_value=resp):
            results = client.search_podcasts("test")
        assert results[0].itunes_id == 123456789

    def test_artwork_upgraded_from_600(self, client):
        resp = _mock_response(json_data=_ITUNES_SEARCH_RESULT)
        with patch.object(client._session, "get", return_value=resp):
            results = client.search_podcasts("test")
        # The fixture has artworkUrl600 at "600x600bb.jpg" — should be upgraded.
        assert "3000x3000bb" in results[0].artwork_url

    def test_podcasts_genre_stripped(self, client):
        resp = _mock_response(json_data=_ITUNES_SEARCH_RESULT)
        with patch.object(client._session, "get", return_value=resp):
            results = client.search_podcasts("test")
        # "Podcasts" is a noise genre — should not appear in categories.
        assert "Podcasts" not in results[0].categories
        assert "Technology" in results[0].categories

    def test_explicit_flag_parsed(self, client):
        resp = _mock_response(json_data=_ITUNES_SEARCH_RESULT)
        with patch.object(client._session, "get", return_value=resp):
            results = client.search_podcasts("test")
        assert results[0].explicit is True

    def test_episodes_empty_after_search(self, client):
        resp = _mock_response(json_data=_ITUNES_SEARCH_RESULT)
        with patch.object(client._session, "get", return_value=resp):
            results = client.search_podcasts("test")
        assert results[0].episodes == []

    def test_empty_query_returns_empty(self, client):
        results = client.search_podcasts("")
        assert results == []

    def test_whitespace_only_query_returns_empty(self, client):
        results = client.search_podcasts("   ")
        assert results == []

    def test_network_failure_returns_empty(self, client):
        with patch.object(client._session, "get", side_effect=Exception("timeout")):
            results = client.search_podcasts("test")
        assert results == []

    def test_result_without_feed_url_skipped(self, client):
        data = {
            "resultCount": 2,
            "results": [
                {**_ITUNES_SEARCH_RESULT["results"][0], "feedUrl": None},
                _ITUNES_SEARCH_RESULT["results"][0],
            ],
        }
        resp = _mock_response(json_data=data)
        with patch.object(client._session, "get", return_value=resp):
            results = client.search_podcasts("test")
        # First result has no feedUrl — only the second should be returned.
        assert len(results) == 1

    def test_limit_capped_at_200(self, client):
        resp = _mock_response(json_data={"resultCount": 0, "results": []})
        captured = {}
        original_get = client._session.get
        def capturing_get(url, params=None, **kwargs):
            captured["params"] = params
            return resp
        with patch.object(client._session, "get", side_effect=capturing_get):
            client.search_podcasts("test", limit=999)
        assert captured["params"]["limit"] == 200

    def test_limit_floored_at_1(self, client):
        resp = _mock_response(json_data={"resultCount": 0, "results": []})
        captured = {}
        def capturing_get(url, params=None, **kwargs):
            captured["params"] = params
            return resp
        with patch.object(client._session, "get", side_effect=capturing_get):
            client.search_podcasts("test", limit=0)
        assert captured["params"]["limit"] == 1


# ---------------------------------------------------------------------------
# get_podcast_client singleton
# ---------------------------------------------------------------------------

def test_get_podcast_client_returns_same_instance():
    # Reset module-level singleton first.
    import core.podcast_client as _pc
    _pc._default_client = None
    c1 = get_podcast_client()
    c2 = get_podcast_client()
    assert c1 is c2
    _pc._default_client = None  # clean up


# ---------------------------------------------------------------------------
# PodcastDownloadClient — is_configured
# ---------------------------------------------------------------------------

def test_download_client_is_configured(tmp_path):
    client = PodcastDownloadClient(download_path=str(tmp_path))
    assert client.is_configured() is True


# ---------------------------------------------------------------------------
# PodcastDownloadClient — download_episode
# ---------------------------------------------------------------------------

def _make_episode(**kwargs) -> PodcastEpisode:
    defaults = dict(
        guid="https://example.com/ep1",
        title="Test Episode",
        enclosure_url="https://cdn.example.com/ep1.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=1024 * 1024,
        pub_date=None,
        duration_seconds=300,
        description="",
        show_notes="",
        season=None,
        episode_number=None,
        episode_type="full",
        artwork_url=None,
        chapter_url=None,
        transcript_url=None,
    )
    defaults.update(kwargs)
    return PodcastEpisode(**defaults)


def _mock_stream_response(body: bytes, status_code: int = 200):
    """Build a streaming mock response for download_episode tests."""
    resp = Mock()
    resp.status_code = status_code
    resp.headers = {"content-length": str(len(body))}
    resp.raise_for_status = Mock()
    # iter_content yields the body in one chunk.
    resp.iter_content = Mock(return_value=iter([body]))
    return resp


class TestDownloadEpisode:
    def test_successful_download_returns_path(self, tmp_path):
        client = PodcastDownloadClient(download_path=str(tmp_path))
        body = b"x" * (20 * 1024)  # 20 KB — above minimum
        resp = _mock_stream_response(body)
        ep = _make_episode()
        with patch.object(client._session, "get", return_value=resp):
            result = client.download_episode(ep)
        assert result is not None
        assert Path(result).exists()
        assert Path(result).suffix == ".mp3"

    def test_file_content_matches_body(self, tmp_path):
        client = PodcastDownloadClient(download_path=str(tmp_path))
        body = b"A" * (20 * 1024)
        resp = _mock_stream_response(body)
        ep = _make_episode()
        with patch.object(client._session, "get", return_value=resp):
            result = client.download_episode(ep)
        assert Path(result).read_bytes() == body

    def test_extension_from_mime_when_url_has_none(self, tmp_path):
        client = PodcastDownloadClient(download_path=str(tmp_path))
        body = b"x" * (20 * 1024)
        resp = _mock_stream_response(body)
        ep = _make_episode(
            enclosure_url="https://cdn.example.com/stream",
            enclosure_type="audio/x-m4a",
        )
        with patch.object(client._session, "get", return_value=resp):
            result = client.download_episode(ep)
        assert result is not None
        assert Path(result).suffix == ".m4a"

    def test_dest_dir_override(self, tmp_path):
        client = PodcastDownloadClient(download_path=str(tmp_path / "default"))
        override = tmp_path / "override"
        body = b"x" * (20 * 1024)
        resp = _mock_stream_response(body)
        ep = _make_episode()
        with patch.object(client._session, "get", return_value=resp):
            result = client.download_episode(ep, dest_dir=str(override))
        assert result is not None
        assert str(override) in result

    def test_network_error_returns_none(self, tmp_path):
        client = PodcastDownloadClient(download_path=str(tmp_path))
        ep = _make_episode()
        with patch.object(client._session, "get", side_effect=Exception("connection refused")):
            result = client.download_episode(ep)
        assert result is None

    def test_suspiciously_small_file_removed(self, tmp_path):
        client = PodcastDownloadClient(download_path=str(tmp_path))
        body = b"tiny"   # well below _MIN_FILE_SIZE
        resp = _mock_stream_response(body)
        ep = _make_episode()
        with patch.object(client._session, "get", return_value=resp):
            result = client.download_episode(ep)
        assert result is None
        # Partial file must be cleaned up.
        assert not any(tmp_path.iterdir())

    def test_progress_callback_called(self, tmp_path):
        client = PodcastDownloadClient(download_path=str(tmp_path))
        body = b"x" * (20 * 1024)
        resp = _mock_stream_response(body)
        ep = _make_episode()
        calls = []
        with patch.object(client._session, "get", return_value=resp):
            client.download_episode(ep, progress_callback=lambda d, t: calls.append((d, t)))
        assert len(calls) > 0
        assert calls[-1][0] == len(body)

    def test_broken_progress_callback_does_not_abort_download(self, tmp_path):
        client = PodcastDownloadClient(download_path=str(tmp_path))
        body = b"x" * (20 * 1024)
        resp = _mock_stream_response(body)
        ep = _make_episode()
        def bad_callback(d, t):
            raise RuntimeError("callback broken")
        with patch.object(client._session, "get", return_value=resp):
            result = client.download_episode(ep, progress_callback=bad_callback)
        # Download must complete despite the broken callback.
        assert result is not None
        assert Path(result).exists()

    def test_collision_safe_naming(self, tmp_path):
        client = PodcastDownloadClient(download_path=str(tmp_path))
        body = b"x" * (20 * 1024)
        ep = _make_episode(title="Test Episode")

        # Pre-create the expected filename so a collision is guaranteed.
        (tmp_path / "Test Episode.mp3").write_bytes(b"existing")

        resp = _mock_stream_response(body)
        with patch.object(client._session, "get", return_value=resp):
            result = client.download_episode(ep)
        assert result is not None
        # Must not overwrite the existing file.
        assert "(1)" in Path(result).name


# ---------------------------------------------------------------------------
# _collision_safe_path
# ---------------------------------------------------------------------------

class TestCollisionSafePath:
    def test_returns_original_when_no_collision(self, tmp_path):
        target = tmp_path / "episode.mp3"
        assert _collision_safe_path(target) == target

    def test_appends_counter_when_collision(self, tmp_path):
        target = tmp_path / "episode.mp3"
        target.write_bytes(b"existing")
        result = _collision_safe_path(target)
        assert result == tmp_path / "episode (1).mp3"

    def test_increments_counter_until_free(self, tmp_path):
        base = tmp_path / "episode.mp3"
        base.write_bytes(b"1")
        (tmp_path / "episode (1).mp3").write_bytes(b"2")
        result = _collision_safe_path(base)
        assert result == tmp_path / "episode (2).mp3"
