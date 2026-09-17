"""the image cache index heals itself when sqlite says it is malformed.

boulder's recently played rail, sept 15 2026: every tile either 404'd or
rendered half. the files on disk were fine; image_cache.sqlite3 was corrupt.
the serve path then went: row expired -> re-download (a second each) ->
os.replace the file -> the db insert fails -> _record_error fails on the same
db -> the "serve stale" branch never returned -> 404. sixty-four warnings and
no way out short of deleting the file by hand.

now: a corruption error moves the index aside, rebuilds it from the files on
disk (gigabytes of them, none of which need downloading again) and redoes
the serve. _record_error never masks a stale serve again either.
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

from core.image_cache import ImageCache, ImageCacheError, _is_corruption

from tests.test_image_cache import FakeResponse

URL_A = "https://cdn.example.invalid/a.jpg"
URL_B = "https://cdn.example.invalid/b.jpg"


def _fetcher(calls):
    def fetch(url, **_):
        calls.append(url)
        return FakeResponse(b"\xff\xd8" + url.encode() + b"\xff\xd9")
    return fetch


def _corrupt(db_path):
    """overwrite the index with bytes sqlite refuses to open.

    the wal and shm go too: with a wal beside it sqlite reads page 1 from
    the wal and never notices the main file is garbage, so the damage would
    only show once the wal was checkpointed."""
    with open(db_path, "wb") as handle:
        handle.write(b"not a sqlite file at all" * 200)
    for suffix in ("-wal", "-shm"):
        side = pathlib.Path(f"{db_path}{suffix}")
        if side.exists():
            side.unlink()


def test_is_corruption_names_the_sqlite_messages():
    assert _is_corruption(sqlite3.DatabaseError("database disk image is malformed"))
    assert _is_corruption(sqlite3.DatabaseError("file is not a database"))
    assert not _is_corruption(sqlite3.OperationalError("database is locked"))
    assert not _is_corruption(RuntimeError("database disk image is malformed"))


def test_serve_rebuilds_the_index_and_keeps_every_file(tmp_path):
    calls = []
    cache = ImageCache(tmp_path, fetcher=_fetcher(calls))
    first = cache.get_url(URL_A)
    cache.get_url(URL_B)
    assert len(calls) == 2

    _corrupt(cache.db_path)

    # the very request that hits the damage is served, and served from disk
    served = cache.get_url(URL_A)
    assert served.path == first.path
    assert served.path.read_bytes().startswith(b"\xff\xd8")
    assert calls == [URL_A, URL_B], "a rebuild must not re-download what is on disk"

    # the damaged file was quarantined, not deleted, and the new index is sane
    assert list(tmp_path.glob("image_cache.sqlite3.corrupt-*"))
    assert cache.stats()["entries"] == 2
    with sqlite3.connect(cache.db_path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_rebuild_drops_files_that_are_not_images(tmp_path):
    calls = []
    cache = ImageCache(tmp_path, fetcher=_fetcher(calls))
    good = cache.get_url(URL_A).path
    zeros = tmp_path / "aa" / "bb" / ("a" * 64 + ".jpg")
    zeros.parent.mkdir(parents=True)
    zeros.write_bytes(b"\x00" * 40000)                       # the lost-write shape
    fake = tmp_path / "cc" / "dd" / ("c" * 64 + ".jpg")
    fake.parent.mkdir(parents=True)
    fake.write_bytes(b"x")                                    # the test-leak shape
    _corrupt(cache.db_path)

    cache.get_url(URL_A)
    assert good.exists()
    assert not zeros.exists() and not fake.exists()
    assert cache.stats()["entries"] == 1


def test_a_recovered_row_serves_by_key_before_the_page_registers_it_again(tmp_path):
    calls = []
    cache = ImageCache(tmp_path, fetcher=_fetcher(calls))
    key = ImageCache.key_for_url(URL_A)
    cache.get_url(URL_A)
    _corrupt(cache.db_path)

    # the browser holds /api/image-cache/<key> from the last render; nothing
    # has told the new index which url that key was
    served = cache.get(key)
    assert served.status == "hit"
    assert served.path.exists()
    assert calls == [URL_A]

    # and once the page registers the url again, the row is whole
    cache.cache_url_for(URL_A)
    cache._flush_registrations()
    with sqlite3.connect(cache.db_path) as conn:
        assert conn.execute("SELECT original_url FROM image_cache WHERE key = ?", (key,)).fetchone()[0] == URL_A


def test_recovered_variant_keeps_serving_the_resized_file(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image
    calls = []

    def fetch(url, **_):
        calls.append(url)
        import io
        buf = io.BytesIO()
        Image.new("RGB", (1600, 1600), "red").save(buf, format="JPEG")
        return FakeResponse(buf.getvalue())

    cache = ImageCache(tmp_path, fetcher=fetch)
    original_key = ImageCache.key_for_url(URL_A)
    cache.cache_url_for(URL_A)
    cache._flush_registrations()
    variant = cache.get_variant_of(original_key, "rail")
    variant_key = ImageCache.key_for_url(URL_A, "rail")
    assert variant.path.suffix == ".jpg" and variant.path != cache.get_url(URL_A).path

    _corrupt(cache.db_path)
    cache.get_url(URL_A)                       # heals
    # re-registering the variant restores its variant flag on the recovered row
    cache.cache_url_for(URL_A, variant="rail")
    cache._flush_registrations()
    again = cache.get_variant_of(original_key, "rail")
    assert again.path == variant.path
    assert again.status == "hit"
    assert calls == [URL_A], "the resized file on disk is reused, not rebuilt"
    with sqlite3.connect(cache.db_path) as conn:
        assert conn.execute("SELECT variant FROM image_cache WHERE key = ?", (variant_key,)).fetchone()[0] == "rail"


def test_record_error_swallows_its_own_db_failure(tmp_path, monkeypatch):
    # it runs inside the "serve the stale file" branch; raising there is what
    # turned a perfectly good file on disk into a 404
    calls = []
    cache = ImageCache(tmp_path, fetcher=_fetcher(calls))
    cache.get_url(URL_A)

    def broken_connect():
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(cache, "_connect", broken_connect)
    import time
    cache._record_error(ImageCache.key_for_url(URL_A), "boom", time.time(), keep_status=True)
    cache._record_error(ImageCache.key_for_url(URL_A), "boom", time.time())


def test_stale_file_is_served_when_the_refresh_and_the_bookkeeping_both_fail(tmp_path, monkeypatch):
    calls = []
    cache = ImageCache(tmp_path, fetcher=_fetcher(calls), ttl_seconds=1)
    first = cache.get_url(URL_A)
    import time
    time.sleep(1.1)

    # upstream is down, and the row can't be updated either (locked, say)
    monkeypatch.setattr(cache, "fetcher", lambda url, **_: FakeResponse(b"", status_code=503))
    real_connect = cache._connect
    state = {"reads": 0}

    def flaky_connect():
        state["reads"] += 1
        if state["reads"] > 1:            # the first read finds the row; every write after that fails
            raise sqlite3.OperationalError("database is locked")
        return real_connect()
    monkeypatch.setattr(cache, "_connect", flaky_connect)

    served = cache.get_url(URL_A)
    assert served.status == "stale"
    assert served.path == first.path


def test_rebuild_is_rate_limited_when_the_file_cannot_be_moved(tmp_path, monkeypatch):
    calls = []
    cache = ImageCache(tmp_path, fetcher=_fetcher(calls))
    cache.get_url(URL_A)
    _corrupt(cache.db_path)

    moves = []

    real_replace = __import__("os").replace

    def refuse(src, dst):
        if "image_cache.sqlite3" not in str(src):
            return real_replace(src, dst)      # the download's tmp file is fine
        moves.append(src)
        raise OSError("sharing violation")
    monkeypatch.setattr("core.image_cache.os.replace", refuse)
    monkeypatch.setattr("core.image_cache.Path.unlink", lambda self, *a, **k: (_ for _ in ()).throw(OSError("in use")))
    monkeypatch.setattr(cache, "MOVE_ASIDE_WAIT_SECONDS", 0.0)

    with pytest.raises((ImageCacheError, sqlite3.DatabaseError)):
        cache.get_url(URL_B)
    with pytest.raises((ImageCacheError, sqlite3.DatabaseError)):
        cache.get_url(URL_B)
    # one rebuild (with its rename retries) inside the cooldown, not one per request
    assert len(moves) == cache.MOVE_ASIDE_ATTEMPTS


def test_rebuild_waits_out_a_handle_that_is_about_to_close(tmp_path, monkeypatch):
    # windows: WinError 32 while another thread's connection is still open.
    # boulder's server hit this one minute after the heal first fired.
    calls = []
    cache = ImageCache(tmp_path, fetcher=_fetcher(calls))
    first = cache.get_url(URL_A)
    _corrupt(cache.db_path)

    real_replace = __import__("os").replace
    refusals = {"left": 2}

    def busy_then_free(src, dst):
        if "image_cache.sqlite3" in str(src) and refusals["left"] > 0:
            refusals["left"] -= 1
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real_replace(src, dst)
    monkeypatch.setattr("core.image_cache.os.replace", busy_then_free)
    real_unlink = pathlib.Path.unlink

    def busy_unlink(self, *a, **k):
        if "image_cache.sqlite3" in str(self):
            raise PermissionError(32, "in use")
        return real_unlink(self, *a, **k)
    monkeypatch.setattr("core.image_cache.Path.unlink", busy_unlink)
    monkeypatch.setattr(cache, "MOVE_ASIDE_WAIT_SECONDS", 0.0)

    served = cache.get_url(URL_A)
    assert served.path == first.path
    assert refusals["left"] == 0
    assert list(tmp_path.glob("image_cache.sqlite3.corrupt-*"))


def test_connections_are_closed_not_just_committed(tmp_path):
    cache = ImageCache(tmp_path, fetcher=_fetcher([]))
    with cache._db() as conn:
        conn.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")            # closed: nothing left holding the file


def test_a_healthy_index_is_never_touched(tmp_path):
    calls = []
    cache = ImageCache(tmp_path, fetcher=_fetcher(calls))
    cache.get_url(URL_A)
    cache.get_url(URL_A)
    assert not list(tmp_path.glob("image_cache.sqlite3.corrupt-*"))
    assert cache._rebuilt_at == 0.0


# ── the suite never touches the live cache ───────────────────────────────────

def test_the_suite_points_the_image_cache_away_from_the_repo():
    import os
    from core.settings import config_manager
    override = os.environ.get("SOULSYNC_IMAGE_CACHE_DIR")
    assert override, "conftest must isolate the image cache dir"
    live = str(config_manager.base_dir / "storage" / "image_cache")
    assert os.path.abspath(override) != os.path.abspath(live)


def test_get_image_cache_honours_the_override(tmp_path, monkeypatch):
    import core.image_cache as ic
    monkeypatch.setenv("SOULSYNC_IMAGE_CACHE_DIR", str(tmp_path / "isolated"))
    ic.reset_image_cache()
    try:
        assert ic.get_image_cache().cache_dir == tmp_path / "isolated"
    finally:
        ic.reset_image_cache()


def test_a_corrupt_index_is_rebuilt_at_startup(tmp_path):
    calls = []
    first = ImageCache(tmp_path, fetcher=_fetcher(calls))
    kept = first.get_url(URL_A).path
    _corrupt(first.db_path)

    # the restart: nothing holds the file, the constructor finds the damage
    second = ImageCache(tmp_path, fetcher=_fetcher(calls))
    assert list(tmp_path.glob("image_cache.sqlite3.corrupt-*"))
    assert second.stats()["entries"] == 1
    assert second.get_url(URL_A).path == kept
    assert calls == [URL_A]


def test_a_healthy_index_passes_the_startup_check_untouched(tmp_path):
    ImageCache(tmp_path, fetcher=_fetcher([])).get_url(URL_A)
    again = ImageCache(tmp_path, fetcher=_fetcher([]))
    assert again._rebuilt_at == 0.0
    assert not list(tmp_path.glob("image_cache.sqlite3.corrupt-*"))
