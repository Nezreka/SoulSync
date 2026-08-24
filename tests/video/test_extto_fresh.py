from __future__ import annotations

from core.video.extto_fresh import parse_fresh_releases


HOME_HTML = """
<div class="card card-nav-tabs main-raised">
  <div class="title-block-with-tabs"><h4 class="titleH4"><a href="/movies/">Movies Torrents</a></h4></div>
  <div class="tab-content">
    <div class="tab-pane" id="torrents-day-1"><table><tbody>
      <tr>
        <td class="text-left"><div class="float-left"><a href="/interstellar-2014-1080p-123/"><b>Interstellar 2014 1080p BluRay</b></a></div><a class="dwn-btn torrent-dwn" href="magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"></a></td>
        <td><div class="add-block-wrapper"><span class="add-block">Size</span><span>2.1 GB</span></div></td>
        <td><div class="add-block-wrapper"><span class="add-block">Files</span><span>3</span></div></td>
        <td><div class="add-block-wrapper"><span class="add-block">Age</span><span>8 hours ago</span></div></td>
        <td><div class="add-block-wrapper"><span class="add-block">Seeds</span><span class="text-success">1684</span></div></td>
        <td><div class="add-block-wrapper"><span class="add-block">Leechs</span><span class="text-danger">147</span></div></td>
      </tr>
    </tbody></table></div>
    <div class="tab-pane" id="torrents-week-1"><table><tbody>
      <tr><td><a href="/arrival-2016-456/"><b>Arrival 2016 2160p</b></a></td><td><span>8 GB</span></td><td><span>1</span></td><td><span>1 week ago</span></td><td><span>42</span></td><td><span>5</span></td></tr>
    </tbody></table></div>
    <div class="tab-pane" id="torrents-month-1"><table><tbody></tbody></table></div>
  </div>
</div>
<div class="card card-nav-tabs main-raised">
  <div class="title-block-with-tabs"><h4 class="titleH4"><a href="/tv/">TV Torrents</a></h4></div>
  <div class="tab-content">
    <div class="tab-pane" id="torrents-day-2"><table><tbody>
      <tr>
        <td><div class="float-left"><a href="/silo-s03e08-789/"><b>Silo S03E08 1080p WEB</b></a></div><a class="torrent-dwn" href="magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"></a></td>
        <td><div class="add-block-wrapper"><span class="add-block">Size</span><span>517.38 MB</span></div></td>
        <td><div class="add-block-wrapper"><span class="add-block">Files</span><span>1</span></div></td>
        <td><div class="add-block-wrapper"><span class="add-block">Age</span><span>3 days ago</span></div></td>
        <td><div class="add-block-wrapper"><span class="add-block">Seeds</span><span>4923</span></div></td>
        <td><div class="add-block-wrapper"><span class="add-block">Leechs</span><span>5149</span></div></td>
      </tr>
    </tbody></table></div>
    <div class="tab-pane" id="torrents-week-2"><table><tbody></tbody></table></div>
    <div class="tab-pane" id="torrents-month-2"><table><tbody></tbody></table></div>
  </div>
</div>
"""


def test_parse_fresh_releases_extracts_movies_and_tv_periods():
    out = parse_fresh_releases(HOME_HTML, "https://ext.to")

    movie = out["movies"]["day"][0]
    assert movie["title"] == "Interstellar 2014 1080p BluRay"
    assert movie["url"] == "https://ext.to/interstellar-2014-1080p-123/"
    assert movie["size_text"] == "2.1 GB"
    assert movie["size_bytes"] > 2_000_000_000
    assert movie["files"] == 3
    assert movie["age"] == "8 hours ago"
    assert movie["seeders"] == 1684
    assert movie["leechers"] == 147
    assert movie["category"] == "movies"
    assert movie["period"] == "day"
    assert movie["download_url"].startswith("magnet:?xt=urn:btih:aaaaaaaa")
    assert movie["magnet_uri"] == movie["download_url"]
    assert movie["protocol"] == "torrent"
    assert movie["indexer_id"] == "extto"
    assert movie["username"] == "EXT.to"
    assert movie["search_title"] == "Interstellar"
    assert movie["year"] == 2014
    assert movie["quality_label"] == "1080p bluray"

    assert out["movies"]["week"][0]["title"] == "Arrival 2016 2160p"
    assert out["tv"]["day"][0]["title"] == "Silo S03E08 1080p WEB"
    assert out["tv"]["day"][0]["seeders"] == 4923
    assert out["tv"]["day"][0]["season"] == 3
    assert out["tv"]["day"][0]["episode"] == 8
    assert out["tv"]["day"][0]["download_url"].startswith("magnet:?xt=urn:btih:bbbbbbbb")
    assert out["movies"]["month"] == []


# ── the grab path ─────────────────────────────────────────────────────────────
# EXT.to is a discovery provider, not a transport. Its grabs were stored with
# source='extto', which nothing downstream knows: the monitor sent the row to the
# slskd watcher, found no transfer, and after eight polls flipped it to
# "Trying another release" while the torrent was downloading fine in the client.
def test_an_extto_grab_is_stored_as_a_torrent_row(tmp_path, monkeypatch):
    import api.video as videoapi
    from flask import Flask
    from database.video_database import VideoDatabase

    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    videoapi._video_db = db
    try:
        import core.video.client_grab as cg
        seen = {}

        def _grab(source, url, **kw):
            seen["source"], seen["url"] = source, url
            return {"ok": True, "ref": "hash123"}

        monkeypatch.setattr(cg, "grab", _grab)
        app = Flask(__name__)
        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        r = app.test_client().post("/api/video/downloads/grab", json={
            "source": "extto", "kind": "movie", "title": "Interstellar", "year": 2014,
            "release_title": "Interstellar 2014 1080p BluRay",
            "download_url": "magnet:?xt=urn:btih:aaaa", "magnet_uri": "magnet:?xt=urn:btih:aaaa",
            "username": "EXT.to", "indexer_id": "extto",
            "media_id": "157336", "media_source": "tmdb", "size_bytes": 2_100_000_000})
        assert r.status_code == 200 and r.get_json().get("ok"), r.get_json()
        # it went to the TORRENT client...
        assert seen["source"] == "torrent"
        row = db.list_video_downloads()[0]
        # ...so the row must say torrent too, or the monitor/importer/seed sweep miss it
        assert row["source"] == "torrent", "an EXT.to grab was stored under a source nothing polls"
        assert row["client_ref"] == "hash123"
        # EXT.to keeps its identity where indexers keep theirs
        assert row["username"] == "EXT.to" and row["indexer_id"] == "extto"
    finally:
        videoapi._video_db = None


def test_the_monitor_polls_the_download_client_for_an_extto_grab(tmp_path, monkeypatch):
    """The fracture point: _tick's source dispatch. A row born from EXT.to has to
    land on the client poller, never the slskd one."""
    from core.video import client_download, download_monitor

    row = {"id": 1, "source": "torrent", "status": "downloading", "client_ref": "hash123",
           "username": "EXT.to", "indexer_id": "extto", "filename": "Interstellar 2014 1080p",
           "progress": 0, "progress_at": None}

    class _DB:
        def get_active_video_downloads(self):
            return [row]

        def update_video_download(self, *a, **kw):
            pass

    routed = []
    monkeypatch.setattr(download_monitor, "list_downloads", lambda: [])
    monkeypatch.setattr(client_download, "process_active_client_download",
                        lambda dl, **kw: routed.append("client") or {})
    monkeypatch.setattr(download_monitor, "process_download",
                        lambda dl, *a, **kw: routed.append("slskd") or {})

    download_monitor._tick(_DB())
    assert routed == ["client"], "the EXT.to grab was handed to the wrong watcher"
