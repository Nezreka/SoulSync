"""Tests for Podcast OPML import/export and custom RSS feed resolution."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from flask import Flask

from api.podcasts import create_podcasts_blueprint, generate_opml_content, parse_opml_content
from core.podcast_client import PodcastEpisode, PodcastShow


# ---------------------------------------------------------------------------
# Sample OPML Data
# ---------------------------------------------------------------------------

SAMPLE_OPML_POCKET_CASTS = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.0">
  <head>
    <title>Pocket Casts Feeds</title>
  </head>
  <body>
    <outline text="feeds">
      <outline type="rss" text="Huberman Lab" xmlUrl="https://feeds.megaphone.fm/hubermanlab" htmlUrl="https://hubermanlab.com" description="Health and neuroscience" />
      <outline type="rss" text="Hardcore History" xmlUrl="https://feed.podbean.com/dancarlin/feed.xml" htmlUrl="https://dancarlin.com" description="History documentary" />
      <outline type="rss" text="Duplicate Feed" xmlUrl="https://feeds.megaphone.fm/hubermanlab" />
    </outline>
  </body>
</opml>"""

SAMPLE_OPML_OVERCAST = """<?xml version="1.0" encoding="utf-8"?>
<opml version="2.0">
  <head>
    <title>Overcast Podcast Subscriptions</title>
  </head>
  <body>
    <outline text="Technology">
      <outline type="rss" text="ATP" title="Accidental Tech Podcast" xmlUrl="https://atp.fm/episodes?format=rss" htmlUrl="https://atp.fm" />
    </outline>
    <outline type="rss" text="Lex Fridman Podcast" xmlUrl="https://lexfridman.com/feed/podcast/" htmlUrl="https://lexfridman.com" />
  </body>
</opml>"""


# ---------------------------------------------------------------------------
# Unit Tests: Parser & Generator Helpers
# ---------------------------------------------------------------------------

def test_parse_opml_content_nested_and_deduped():
    feeds = parse_opml_content(SAMPLE_OPML_POCKET_CASTS)
    assert len(feeds) == 2
    assert feeds[0]["title"] == "Huberman Lab"
    assert feeds[0]["feed_url"] == "https://feeds.megaphone.fm/hubermanlab"
    assert feeds[0]["description"] == "Health and neuroscience"
    assert feeds[0]["html_url"] == "https://hubermanlab.com"
    assert feeds[1]["title"] == "Hardcore History"
    assert feeds[1]["feed_url"] == "https://feed.podbean.com/dancarlin/feed.xml"


def test_parse_opml_content_overcast():
    feeds = parse_opml_content(SAMPLE_OPML_OVERCAST)
    assert len(feeds) == 2
    titles = [f["title"] for f in feeds]
    assert "Accidental Tech Podcast" in titles or "ATP" in titles
    assert "Lex Fridman Podcast" in titles


def test_parse_opml_content_empty_or_invalid():
    assert parse_opml_content("") == []
    assert parse_opml_content("not xml content <><>") == []
    assert parse_opml_content(b"") == []


def test_parse_opml_content_pseudo_schemes():
    opml_with_schemes = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.0">
  <body>
    <outline text="Feed 1" xmlUrl="feed://https://example.com/rss1.xml" />
    <outline text="Feed 2" xmlUrl="feed://example.com/rss2.xml" />
    <outline text="Feed 3" xmlUrl="itpc://example.com/rss3.xml" />
    <outline text="Feed 4" xmlUrl="pcast://example.com/rss4.xml" />
  </body>
</opml>"""
    feeds = parse_opml_content(opml_with_schemes)
    assert len(feeds) == 4
    assert feeds[0]["feed_url"] == "https://example.com/rss1.xml"
    assert feeds[1]["feed_url"] == "http://example.com/rss2.xml"
    assert feeds[2]["feed_url"] == "https://example.com/rss3.xml"
    assert feeds[3]["feed_url"] == "https://example.com/rss4.xml"



def test_generate_opml_content():
    shows = [
        {
            "title": "Dan Carlin's Hardcore History",
            "feed_url": "https://feed.podbean.com/dancarlin/feed.xml",
            "website": "https://dancarlin.com",
            "description": "High energy history",
        },
        {
            "title": 'Special "Quotes" & Escaping',
            "feed_url": "https://example.com/feed.xml?a=1&b=2",
            "website": "",
            "description": "Quotes & notes",
        },
    ]
    xml_out = generate_opml_content(shows)
    assert '<?xml version="1.0" encoding="UTF-8"?>' in xml_out
    assert '<opml version="2.0">' in xml_out
    assert 'Dan Carlin' in xml_out
    assert 'xmlUrl="https://feed.podbean.com/dancarlin/feed.xml"' in xml_out
    assert 'Special &quot;Quotes&quot; &amp; Escaping' in xml_out
    assert 'https://example.com/feed.xml?a=1&amp;b=2' in xml_out

    # Parse it back to verify round-trip integrity
    parsed = parse_opml_content(xml_out)
    assert len(parsed) == 2
    assert parsed[0]["feed_url"] == "https://feed.podbean.com/dancarlin/feed.xml"
    assert parsed[1]["feed_url"] == "https://example.com/feed.xml?a=1&b=2"


# ---------------------------------------------------------------------------
# API Route Tests (Flask test client)
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = create_podcasts_blueprint()
    app.register_blueprint(bp, url_prefix="/api/podcasts")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_opml_import_preview_via_json(client):
    res = client.post(
        "/api/podcasts/opml/import?action=preview",
        json={"opml_text": SAMPLE_OPML_POCKET_CASTS},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["action"] == "preview"
    assert data["count"] == 2
    assert data["feeds"][0]["title"] == "Huberman Lab"


def test_opml_import_preview_via_file_upload(client):
    file_bytes = io.BytesIO(SAMPLE_OPML_OVERCAST.encode("utf-8"))
    res = client.post(
        "/api/podcasts/opml/import?action=preview",
        data={"file": (file_bytes, "subscriptions.opml")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["count"] == 2


def test_opml_import_subscribe_action(client, monkeypatch):
    mock_db = MagicMock()
    mock_db.add_watchlist_podcast.return_value = True

    from api import podcasts
    monkeypatch.setattr(podcasts, "_db", lambda: mock_db)

    res = client.post(
        "/api/podcasts/opml/import?action=subscribe",
        json={
            "shows": [
                {"title": "Show A", "feed_url": "https://a.com/rss"},
                {"title": "Show B", "feed_url": "https://b.com/rss"},
            ]
        },
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["action"] == "subscribe"
    assert data["imported_count"] == 2
    assert mock_db.add_watchlist_podcast.call_count == 2


def test_opml_export_endpoint(client, monkeypatch):
    mock_db = MagicMock()
    mock_db.get_watchlist_podcasts.return_value = [
        {
            "title": "Lex Fridman Podcast",
            "feed_url": "https://lexfridman.com/feed/podcast/",
            "website": "https://lexfridman.com",
            "description": "Conversations",
        }
    ]

    from api import podcasts
    monkeypatch.setattr(podcasts, "_db", lambda: mock_db)

    res = client.get("/api/podcasts/opml/export")
    assert res.status_code == 200
    assert res.mimetype == "application/xml"
    assert "attachment; filename=soulsync-podcasts.opml" in res.headers["Content-Disposition"]
    assert b"Lex Fridman Podcast" in res.data
    assert b"https://lexfridman.com/feed/podcast/" in res.data


def test_show_detail_endpoint_custom_feed_url(client, monkeypatch):
    mock_client = MagicMock()
    mock_show = PodcastShow(
        title="Patreon Exclusive Podcast",
        author="Creator",
        description="Subscriber feed",
        artwork_url="https://patreon.com/art.jpg",
        feed_url="https://feeds.patreon.com/private/12345",
        itunes_id=None,
        website=None,
        language="en",
        explicit=True,
        categories=["Exclusive"],
        episode_count=1,
        episodes=[
            PodcastEpisode(
                guid="patreon-ep-1",
                title="Episode 1: Behind the Scenes",
                enclosure_url="https://patreon.com/audio/ep1.mp3",
                enclosure_type="audio/mpeg",
                enclosure_length=50000,
                pub_date=None,
                duration_seconds=1800,
                description="Subscriber special",
                show_notes="",
                season=1,
                episode_number=1,
                episode_type="full",
                artwork_url=None,
                chapter_url=None,
                transcript_url=None,
            )
        ],
    )
    mock_client.fetch_feed.return_value = mock_show

    from api import podcasts
    monkeypatch.setattr(podcasts, "get_podcast_client", lambda: mock_client)

    res = client.get("/api/podcasts/show?url=https://feeds.patreon.com/private/12345")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["show"]["title"] == "Patreon Exclusive Podcast"
    assert data["show"]["feed_url"] == "https://feeds.patreon.com/private/12345"
    assert len(data["show"]["episodes"]) == 1
    assert data["show"]["episodes"][0]["title"] == "Episode 1: Behind the Scenes"
