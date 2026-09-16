"""Tests for podcast library history persistence, stats, and API endpoints."""

import pytest
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "test_history.db"))


def test_podcast_history_crud_and_stats(db):
    # Add regular download
    db.add_library_history_entry(
        event_type="download",
        title="Music Song",
        artist_name="Music Artist",
        album_name="Music Album",
        download_source="Soulseek",
        file_path="/music/song.flac",
    )

    # Add server import
    db.add_library_history_entry(
        event_type="import",
        title="Imported Track",
        artist_name="Import Artist",
        server_source="plex",
        file_path="/music/imported.mp3",
    )

    # Add podcast download
    db.add_library_history_entry(
        event_type="podcast",
        title="Tech News Today #42",
        artist_name="Tech Host",
        album_name="Tech Talk Daily",
        download_source="Podcast",
        quality="audio/mpeg",
        file_path="/podcasts/ep42.mp3",
        thumb_url="https://example.com/art.jpg",
        origin="podcast",
        origin_context="Tech Talk Daily",
    )

    # 1. Query by event_type='podcast'
    podcasts, total_podcasts = db.get_library_history(event_type="podcast")
    assert total_podcasts == 1
    assert len(podcasts) == 1
    assert podcasts[0]["title"] == "Tech News Today #42"
    assert podcasts[0]["artist_name"] == "Tech Host"
    assert podcasts[0]["album_name"] == "Tech Talk Daily"
    assert podcasts[0]["download_source"] == "Podcast"

    # 2. Query by tuple event_type=('download', 'podcast')
    all_dl, total_all_dl = db.get_library_history(event_type=("download", "podcast"))
    assert total_all_dl == 2
    assert len(all_dl) == 2
    titles = {e["title"] for e in all_dl}
    assert titles == {"Music Song", "Tech News Today #42"}

    # 3. Query stats
    stats = db.get_library_history_stats()
    assert stats["downloads"] == 1
    assert stats["imports"] == 1
    assert stats["podcasts"] == 1
    assert stats["source_counts"].get("Podcast") == 1
    assert stats["source_counts"].get("Soulseek") == 1


def test_clear_completed_download_history_clears_podcasts(db):
    db.add_library_history_entry(
        event_type="download",
        title="Song A",
        artist_name="Artist A",
    )
    db.add_library_history_entry(
        event_type="podcast",
        title="Podcast Ep 1",
        artist_name="Host A",
    )
    db.add_library_history_entry(
        event_type="import",
        title="Import 1",
        artist_name="Artist B",
    )

    cleared = db.clear_completed_download_history()
    assert cleared == 2

    # Verify import remains, download and podcast are deleted
    _, dl_count = db.get_library_history(event_type="download")
    _, pc_count = db.get_library_history(event_type="podcast")
    _, im_count = db.get_library_history(event_type="import")
    assert dl_count == 0
    assert pc_count == 0
    assert im_count == 1


def test_api_library_history_route(db):
    from flask import Flask, request, jsonify

    app = Flask(__name__)

    @app.route("/api/library/history")
    def get_library_history():
        event_type = request.args.get("type", None)
        if event_type == "podcasts":
            event_type = "podcast"
        if event_type and event_type not in ("download", "import", "podcast"):
            event_type = None
        page = max(1, int(request.args.get("page", 1)))
        limit = min(200, max(1, int(request.args.get("limit", 50))))
        entries, total = db.get_library_history(event_type=event_type, page=page, limit=limit)
        stats = db.get_library_history_stats()
        return jsonify({
            "success": True,
            "entries": entries,
            "total": total,
            "page": page,
            "limit": limit,
            "stats": stats,
        })

    client = app.test_client()

    db.add_library_history_entry(event_type="download", title="Song 1")
    db.add_library_history_entry(event_type="podcast", title="Pod 1")
    db.add_library_history_entry(event_type="import", title="Import 1")

    # type=podcast
    res = client.get("/api/library/history?type=podcast")
    assert res.status_code == 200
    data = res.get_json()
    assert data["total"] == 1
    assert data["entries"][0]["title"] == "Pod 1"
    assert data["stats"]["podcasts"] == 1

    # type=podcasts (alias)
    res2 = client.get("/api/library/history?type=podcasts")
    assert res2.status_code == 200
    data2 = res2.get_json()
    assert data2["total"] == 1
    assert data2["entries"][0]["title"] == "Pod 1"

    # type=download
    res3 = client.get("/api/library/history?type=download")
    assert res3.status_code == 200
    data3 = res3.get_json()
    assert data3["total"] == 1
    assert data3["entries"][0]["title"] == "Song 1"
