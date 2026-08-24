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
