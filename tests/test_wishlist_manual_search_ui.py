"""Music wishlist 'Search manually' must search for the FILE: the person is
there because the automatic download failed, so they want download sources
(Soulseek and the rest), not the default metadata source. Source guard; the
route's behaviour is tested in tests/downloads/test_inspector_routes.py."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SERVER = (_ROOT / "web_server.py").read_text(encoding="utf-8")


def _route_body(source: str, route: str) -> str:
    body = source[source.index(f"@app.route('{route}'"):]
    return body[:body.index("\n@app.route(", 10)]


def test_manual_search_searches_download_sources_not_metadata():
    fn = _route_body(_SERVER, "/api/wishlist/inspect")
    assert "_inspect_sources_stream(" in fn        # the per-source file search
    assert "search_metadata" not in fn and "enhanced" not in fn


def test_the_pick_downloads_exactly_that_file():
    fn = _route_body(_SERVER, "/api/wishlist/inspect/download")
    assert "_pinned_batch" in fn
