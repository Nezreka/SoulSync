"""Manual cover-art override (docs §49 art picker): a user-picked cover must
win over the auto-resolved embedded/provider image, and must survive a later
force-refresh (e.g. "Refresh & Scan") rather than being silently clobbered —
mirrors the legacy picker's "a manual pick pins the choice" guarantee, but via
the existing lib2_metadata_overrides store instead of a parallel pin flag.
"""

from __future__ import annotations

import os
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from core.library2 import artwork
from core.library2.metadata_overrides import get_field_overrides, set_field_override


def _image_bytes(color=(255, 0, 0)) -> bytes:
    image = Image.new("RGB", (4, 3), color)
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _art_db(legacy_db):
    return SimpleNamespace(database_path=legacy_db.path)


@pytest.fixture
def album_id(imported_conn):
    return imported_conn.execute("SELECT id FROM lib2_albums LIMIT 1").fetchone()[0]


@pytest.fixture
def artist_id(imported_conn):
    return imported_conn.execute("SELECT id FROM lib2_artists LIMIT 1").fetchone()[0]


def test_manual_override_wins_over_embedded_art(imported_conn, legacy_db, monkeypatch, album_id):
    set_field_override(
        imported_conn, entity_type="release_group", entity_id=album_id,
        field_name="image_url", value="https://example.com/manual.jpg",
    )
    imported_conn.commit()

    override_bytes = _image_bytes((10, 20, 30))
    embedded_bytes = _image_bytes((200, 100, 50))
    monkeypatch.setattr(
        artwork, "_download_remote_artwork",
        lambda url: override_bytes if url == "https://example.com/manual.jpg" else None,
    )
    monkeypatch.setattr(artwork, "_embedded_art_for_album", lambda *_args: embedded_bytes)

    database = _art_db(legacy_db)
    path = artwork.build_artwork(database, imported_conn, None, "album", album_id, force=True)

    assert path is not None
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == pytest.approx((10, 20, 30), abs=2)


def test_no_override_falls_back_to_embedded(imported_conn, legacy_db, monkeypatch, album_id):
    embedded_bytes = _image_bytes((5, 6, 7))
    monkeypatch.setattr(artwork, "_embedded_art_for_album", lambda *_args: embedded_bytes)

    database = _art_db(legacy_db)
    path = artwork.build_artwork(database, imported_conn, None, "album", album_id, force=True)

    assert path is not None
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == pytest.approx((5, 6, 7), abs=2)


def test_artist_prefers_provider_photo_over_embedded_album_art(
    imported_conn, legacy_db, monkeypatch, artist_id,
):
    provider_bytes = _image_bytes((11, 22, 33))
    embedded_bytes = _image_bytes((200, 100, 50))
    monkeypatch.setattr(
        artwork, "_provider_art_url", lambda *_args, **_kwargs: "https://example.com/artist.jpg"
    )
    monkeypatch.setattr(
        artwork, "_download_remote_artwork", lambda _url: provider_bytes
    )
    monkeypatch.setattr(artwork, "_embedded_art_for_album", lambda *_args: embedded_bytes)

    path = artwork.build_artwork(
        _art_db(legacy_db), imported_conn, None, "artist", artist_id, force=True
    )

    assert path is not None
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == pytest.approx((11, 22, 33), abs=2)


def test_artist_falls_back_to_embedded_art_when_provider_has_no_photo(
    imported_conn, legacy_db, monkeypatch, artist_id,
):
    embedded_bytes = _image_bytes((44, 55, 66))
    monkeypatch.setattr(artwork, "_provider_art_url", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(artwork, "_embedded_art_for_album", lambda *_args: embedded_bytes)

    path = artwork.build_artwork(
        _art_db(legacy_db), imported_conn, None, "artist", artist_id, force=True
    )

    assert path is not None
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == pytest.approx((44, 55, 66), abs=2)


def test_apply_manual_artwork_downloads_validates_and_caches(
    imported_conn, legacy_db, monkeypatch, album_id,
):
    chosen_bytes = _image_bytes((1, 2, 3))
    monkeypatch.setattr(
        artwork, "_download_remote_artwork",
        lambda url: chosen_bytes if url == "https://example.com/cover.jpg" else None,
    )
    database = _art_db(legacy_db)

    ok = artwork.apply_manual_artwork(
        database, imported_conn, "album", album_id, "https://example.com/cover.jpg",
    )
    assert ok is True

    overrides = get_field_overrides(imported_conn, entity_type="release_group", entity_id=album_id)
    assert overrides["image_url"].value == "https://example.com/cover.jpg"
    row = imported_conn.execute(
        "SELECT image_url, art_locked FROM lib2_albums WHERE id=?", (album_id,)
    ).fetchone()
    assert tuple(row) == ("https://example.com/cover.jpg", 1)

    cached = artwork.artwork_file(database, "album", album_id)
    assert artwork.is_cached_jpeg(cached)
    with Image.open(cached) as image:
        assert image.getpixel((0, 0)) == pytest.approx((1, 2, 3), abs=2)


def test_apply_manual_artwork_returns_false_for_unreachable_url(
    imported_conn, legacy_db, monkeypatch, album_id,
):
    monkeypatch.setattr(artwork, "_download_remote_artwork", lambda url: None)
    database = _art_db(legacy_db)

    ok = artwork.apply_manual_artwork(
        database, imported_conn, "album", album_id, "https://example.com/dead.jpg",
    )
    assert ok is False
    overrides = get_field_overrides(imported_conn, entity_type="release_group", entity_id=album_id)
    assert "image_url" not in overrides


def test_apply_manual_artwork_returns_false_for_invalid_image_bytes(
    imported_conn, legacy_db, monkeypatch, album_id,
):
    monkeypatch.setattr(
        artwork, "_download_remote_artwork", lambda url: b"not-an-image",
    )
    database = _art_db(legacy_db)

    ok = artwork.apply_manual_artwork(
        database, imported_conn, "album", album_id, "https://example.com/garbage.jpg",
    )
    assert ok is False
    overrides = get_field_overrides(imported_conn, entity_type="release_group", entity_id=album_id)
    assert "image_url" not in overrides


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "http://127.0.0.1/admin",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/admin",
    "https://user:password@example.com/image.jpg",
])
def test_remote_artwork_rejects_non_public_targets(url):
    assert artwork._public_remote_artwork_url(url) is False


def test_remote_artwork_revalidates_redirect_targets(monkeypatch):
    monkeypatch.setattr(
        artwork.socket,
        "getaddrinfo",
        lambda host, port, **_kwargs: [
            (artwork.socket.AF_INET, artwork.socket.SOCK_STREAM, 6, "", (host, port))
        ],
    )
    responses = []

    class _Response:
        status_code = 302
        headers = {"Location": "http://127.0.0.1/internal"}
        raw = None

        def close(self):
            responses.append("closed")

    calls = []
    monkeypatch.setattr(
        "requests.get",
        lambda url, **_kwargs: calls.append(url) or _Response(),
    )

    assert artwork._download_remote_artwork("https://93.184.216.34/cover") is None
    assert calls == ["https://93.184.216.34/cover"]
    assert responses == ["closed"]


def test_remote_artwork_stream_enforces_byte_limit(monkeypatch):
    monkeypatch.setattr(
        artwork.socket,
        "getaddrinfo",
        lambda host, port, **_kwargs: [
            (artwork.socket.AF_INET, artwork.socket.SOCK_STREAM, 6, "", (host, port))
        ],
    )

    class _Response:
        status_code = 200
        headers = {"Content-Type": "image/png"}
        raw = None

        def iter_content(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield b"123"
            yield b"45"

        def close(self):
            return None

    monkeypatch.setattr("requests.get", lambda *_args, **_kwargs: _Response())

    assert artwork._download_remote_artwork(
        "https://93.184.216.34/cover", max_bytes=4,
    ) is None


def test_artwork_normalization_rejects_excessive_dimensions(monkeypatch):
    monkeypatch.setattr(artwork, "MAX_ARTWORK_PIXELS", 4)
    assert artwork._normalize_jpeg_variants(_image_bytes()) is None


def test_force_refresh_does_not_clobber_a_manual_pick(imported_conn, legacy_db, monkeypatch, album_id):
    """The whole point of storing the pick as an override (not just writing
    the cache file once): a later force-refresh (Refresh & Scan / precache)
    must still prefer it over the auto-resolved embedded art."""
    picked_bytes = _image_bytes((9, 9, 9))
    embedded_bytes = _image_bytes((200, 200, 200))
    monkeypatch.setattr(
        artwork, "_download_remote_artwork",
        lambda url: picked_bytes if url == "https://example.com/pick.jpg" else None,
    )
    database = _art_db(legacy_db)
    assert artwork.apply_manual_artwork(
        database, imported_conn, "album", album_id, "https://example.com/pick.jpg",
    )

    # Simulate a later "Refresh & Scan" / precache force-rebuild — a real
    # embedded cover is now available (e.g. a re-scan found the file), but the
    # user's manual pick must still win.
    monkeypatch.setattr(artwork, "_embedded_art_for_album", lambda *_args: embedded_bytes)
    path = artwork.build_artwork(database, imported_conn, None, "album", album_id, force=True)

    assert path is not None
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == pytest.approx((9, 9, 9), abs=2)


# ---------------------------------------------------------------------------
# Issue #2079: Discogs image downloads. _download_remote_artwork sent only
# "Accept: image/*", so requests went out as python-requests with no Discogs
# credential and i.discogs.com answered 403 — a Discogs pick in the art
# picker could never be applied. The fetcher now identifies itself, and
# sends the configured Discogs token to Discogs image hosts only.
# ---------------------------------------------------------------------------


def _public_dns(monkeypatch):
    """DNS that answers every host with one fixed public IP (the existing
    SSRF-test mock echoes the hostname back, which only parses for IP-literal
    URLs)."""
    monkeypatch.setattr(
        artwork.socket,
        "getaddrinfo",
        lambda host, port, **_kwargs: [
            (artwork.socket.AF_INET, artwork.socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
        ],
    )


class _FakeArtworkResponse:
    raw = None

    def __init__(self, status_code=200, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "image/jpeg"}
        self._body = body

    def iter_content(self, chunk_size):
        yield self._body

    def close(self):
        return None


def _capture_requests_get(monkeypatch, responses):
    """requests.get serving `responses` in order; returns the (url, headers)
    calls it received."""
    calls = []
    queue = list(responses)

    def _get(url, **kwargs):
        calls.append((url, kwargs.get("headers") or {}))
        return queue.pop(0)

    monkeypatch.setattr("requests.get", _get)
    return calls


def test_discogs_fetch_sends_token_and_user_agent(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    calls = _capture_requests_get(monkeypatch, [_FakeArtworkResponse(body=b"jpeg-bytes")])

    data = artwork._download_remote_artwork("https://i.discogs.com/abc/photo.jpeg")

    assert data == b"jpeg-bytes"
    assert len(calls) == 1
    headers = calls[0][1]
    assert headers["Authorization"] == "Discogs token=TOKEN123"
    assert headers["User-Agent"] == artwork._ARTWORK_USER_AGENT
    assert headers["Accept"] == "image/*"
    assert headers["Referer"] == "https://www.discogs.com/"


def test_discogs_fetch_without_token_sends_user_agent_only(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "")
    calls = _capture_requests_get(monkeypatch, [_FakeArtworkResponse(body=b"jpeg-bytes")])

    assert artwork._download_remote_artwork("https://i.discogs.com/abc/photo.jpeg") == b"jpeg-bytes"
    headers = calls[0][1]
    assert "Authorization" not in headers
    assert headers["User-Agent"] == artwork._ARTWORK_USER_AGENT
    assert headers["Referer"] == "https://www.discogs.com/"


def test_non_discogs_fetch_never_sends_the_discogs_token(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    calls = _capture_requests_get(monkeypatch, [_FakeArtworkResponse(body=b"jpeg-bytes")])

    assert artwork._download_remote_artwork("https://example.com/cover.jpg") == b"jpeg-bytes"
    headers = calls[0][1]
    assert "Authorization" not in headers
    assert "Referer" not in headers
    assert headers["User-Agent"] == artwork._ARTWORK_USER_AGENT


@pytest.mark.parametrize("url", [
    "https://i.discogs.com.evil.net/a.jpg",
    "https://x.i.discogs.com/a.jpg",
    "https://evil-i.discogs.com/a.jpg",
    "http://i.discogs.com/a.jpg",
    "https://discogs.com/a.jpg",
    "https://api.discogs.com/a.jpg",
    "https://user@i.discogs.com/a.jpg",
    "https://user:secret@img.discogs.com/a.jpg",
    "https://i.discogs.com../a.jpg",
])
def test_remote_artwork_headers_withhold_token_from_lookalikes(monkeypatch, url):
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    headers = artwork._remote_artwork_headers(url)
    assert "Authorization" not in headers
    assert "Referer" not in headers
    assert headers["User-Agent"] == artwork._ARTWORK_USER_AGENT


def test_remote_artwork_headers_match_the_host_case_insensitively(monkeypatch):
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    headers = artwork._remote_artwork_headers("https://I.DISCOGS.COM/a.jpg")
    assert headers["Authorization"] == "Discogs token=TOKEN123"
    assert headers["Referer"] == "https://www.discogs.com/"


def test_discogs_redirect_off_discogs_drops_the_token(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    calls = _capture_requests_get(monkeypatch, [
        _FakeArtworkResponse(
            status_code=302,
            headers={"Location": "https://cdn.example.net/b.jpg"},
        ),
        _FakeArtworkResponse(body=b"jpeg-bytes"),
    ])

    data = artwork._download_remote_artwork("https://i.discogs.com/a")

    assert data == b"jpeg-bytes"
    assert len(calls) == 2
    assert calls[0][1]["Authorization"] == "Discogs token=TOKEN123"
    assert "Authorization" not in calls[1][1]
    assert "Referer" not in calls[1][1]


def test_discogs_token_read_failure_means_no_token_and_the_fetch_proceeds(monkeypatch):
    import core.settings

    class _RaisingConfig:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("config unavailable")

    monkeypatch.setattr(core.settings, "config_manager", _RaisingConfig())
    assert artwork._discogs_token() == ""

    _public_dns(monkeypatch)
    calls = _capture_requests_get(monkeypatch, [_FakeArtworkResponse(body=b"jpeg-bytes")])
    assert artwork._download_remote_artwork("https://i.discogs.com/a.jpg") == b"jpeg-bytes"
    assert "Authorization" not in calls[0][1]


def test_manual_artwork_failure_message_discogs_without_token(monkeypatch):
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "")
    message = artwork.manual_artwork_failure_message(
        "https://i.discogs.com/a.jpg", artwork.MANUAL_ART_DOWNLOAD_FAILED)
    assert "Discogs token" in message
    assert "Settings" in message


def test_manual_artwork_failure_message_discogs_with_token(monkeypatch):
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    message = artwork.manual_artwork_failure_message(
        "https://img.discogs.com/a.jpg", artwork.MANUAL_ART_DOWNLOAD_FAILED)
    assert message.startswith("SoulSync could not download that image from Discogs")
    assert "still valid" in message


def test_manual_artwork_failure_message_other_hosts_unchanged(monkeypatch):
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    assert artwork.manual_artwork_failure_message(
        "https://example.com/a.jpg", artwork.MANUAL_ART_DOWNLOAD_FAILED) == (
        "Could not download or validate that image URL"
    )


def test_img_discogs_fetch_target_also_gets_the_token(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    calls = _capture_requests_get(monkeypatch, [_FakeArtworkResponse(body=b"jpeg-bytes")])

    assert artwork._download_remote_artwork("https://img.discogs.com/abc/photo.jpeg") == b"jpeg-bytes"
    assert calls[0][1]["Authorization"] == "Discogs token=TOKEN123"
    assert calls[0][1]["Referer"] == "https://www.discogs.com/"


def test_userinfo_lookalike_fetch_is_rejected_before_any_request(monkeypatch):
    """https://i.discogs.com@evil.net/... parses to host evil.net AND carries
    userinfo — the SSRF check refuses it, so no request (and no token) ever
    leaves, end to end through the fetch path."""
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    calls = _capture_requests_get(monkeypatch, [_FakeArtworkResponse(body=b"jpeg-bytes")])

    assert artwork._download_remote_artwork("https://i.discogs.com@evil.net/a.jpg") is None
    assert calls == []


def test_discogs_redirect_to_another_discogs_host_keeps_the_token(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    calls = _capture_requests_get(monkeypatch, [
        _FakeArtworkResponse(
            status_code=302,
            headers={"Location": "https://img.discogs.com/b.jpg"},
        ),
        _FakeArtworkResponse(body=b"jpeg-bytes"),
    ])

    assert artwork._download_remote_artwork("https://i.discogs.com/a") == b"jpeg-bytes"
    assert len(calls) == 2
    assert calls[0][1]["Authorization"] == "Discogs token=TOKEN123"
    assert calls[1][1]["Authorization"] == "Discogs token=TOKEN123"
    assert calls[0][1]["Referer"] == "https://www.discogs.com/"
    assert calls[1][1]["Referer"] == "https://www.discogs.com/"


def test_discogs_redirect_downgrade_to_http_drops_the_token(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    calls = _capture_requests_get(monkeypatch, [
        _FakeArtworkResponse(
            status_code=302,
            headers={"Location": "http://i.discogs.com/b.jpg"},
        ),
        _FakeArtworkResponse(body=b"jpeg-bytes"),
    ])

    assert artwork._download_remote_artwork("https://i.discogs.com/a") == b"jpeg-bytes"
    assert len(calls) == 2
    assert calls[0][1]["Authorization"] == "Discogs token=TOKEN123"
    assert "Authorization" not in calls[1][1]
    assert "Referer" not in calls[1][1]


def test_malformed_discogs_token_counts_as_unset(monkeypatch):
    import core.settings

    class _BadTokenConfig:
        def get(self, *_args, **_kwargs):
            return "token with a\nnewline"

    monkeypatch.setattr(core.settings, "config_manager", _BadTokenConfig())
    assert artwork._discogs_token() == ""

    _public_dns(monkeypatch)
    calls = _capture_requests_get(monkeypatch, [_FakeArtworkResponse(body=b"jpeg-bytes")])
    assert artwork._download_remote_artwork("https://i.discogs.com/a.jpg") == b"jpeg-bytes"
    assert "Authorization" not in calls[0][1]


def test_remote_artwork_headers_allow_one_trailing_dot_fqdn(monkeypatch):
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    headers = artwork._remote_artwork_headers("https://i.discogs.com./a.jpg")
    assert headers["Authorization"] == "Discogs token=TOKEN123"
    assert headers["Referer"] == "https://www.discogs.com/"


def test_discogs_redirect_with_relative_location_keeps_the_token(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    calls = _capture_requests_get(monkeypatch, [
        _FakeArtworkResponse(status_code=302, headers={"Location": "/b.jpg"}),
        _FakeArtworkResponse(body=b"jpeg-bytes"),
    ])

    assert artwork._download_remote_artwork("https://i.discogs.com/a.jpg") == b"jpeg-bytes"
    assert len(calls) == 2
    assert calls[1][0] == "https://i.discogs.com/b.jpg"
    assert calls[1][1]["Authorization"] == "Discogs token=TOKEN123"
    assert calls[0][1]["Referer"] == "https://www.discogs.com/"
    assert calls[1][1]["Referer"] == "https://www.discogs.com/"


@pytest.mark.parametrize("value", [
    12345,
    {"token": "TOKEN123"},
    "token with spaces",
    "token\twith\ttabs",
    "tökén",
])
def test_discogs_token_rejects_non_string_and_malformed_values(monkeypatch, value):
    import core.settings

    class _Config:
        def get(self, *_args, **_kwargs):
            return value

    monkeypatch.setattr(core.settings, "config_manager", _Config())
    assert artwork._discogs_token() == ""


def test_discogs_token_trims_a_padded_valid_value(monkeypatch):
    import core.settings

    class _Config:
        def get(self, *_args, **_kwargs):
            return "  TOKEN123  "

    monkeypatch.setattr(core.settings, "config_manager", _Config())
    assert artwork._discogs_token() == "TOKEN123"


@pytest.mark.parametrize("url", [
    "http://i.discogs.com/a.jpg",
    "https://user@i.discogs.com/a.jpg",
    "https://x.i.discogs.com/a.jpg",
    "https://i.discogs.com.evil.net/a.jpg",
    "https://i.discogs.com:8443/a.jpg",
    "https://i.discogs.com:abc/a.jpg",
])
def test_manual_artwork_failure_message_lookalikes_get_the_generic_string(monkeypatch, url):
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    assert artwork.manual_artwork_failure_message(url, artwork.MANUAL_ART_DOWNLOAD_FAILED) == (
        "Could not download or validate that image URL"
    )


# ---------------------------------------------------------------------------
# Revised plan tests: the routes learn WHY a pick failed, so the Discogs
# message only ever describes a download failure.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", [
    artwork.MANUAL_ART_INVALID_IMAGE,
    artwork.MANUAL_ART_WRITE_FAILED,
])
@pytest.mark.parametrize("token", ["", "TOKEN123"])
def test_failure_message_for_non_download_failures_is_generic_even_for_discogs(
    monkeypatch, outcome, token,
):
    monkeypatch.setattr(artwork, "_discogs_token", lambda: token)
    assert artwork.manual_artwork_failure_message("https://i.discogs.com/a.jpg", outcome) == (
        "Could not download or validate that image URL"
    )


def test_apply_outcome_applied(imported_conn, legacy_db, monkeypatch, album_id):
    monkeypatch.setattr(artwork, "_download_remote_artwork", lambda _url: _image_bytes())
    outcome = artwork.apply_manual_artwork_outcome(
        _art_db(legacy_db), imported_conn, "album", album_id, "https://example.com/a.jpg",
    )
    assert outcome == artwork.MANUAL_ART_APPLIED


def test_apply_outcome_download_failed(imported_conn, legacy_db, monkeypatch, album_id):
    monkeypatch.setattr(artwork, "_download_remote_artwork", lambda _url: None)
    outcome = artwork.apply_manual_artwork_outcome(
        _art_db(legacy_db), imported_conn, "album", album_id, "https://example.com/a.jpg",
    )
    assert outcome == artwork.MANUAL_ART_DOWNLOAD_FAILED


def test_apply_outcome_invalid_image(imported_conn, legacy_db, monkeypatch, album_id):
    monkeypatch.setattr(artwork, "_download_remote_artwork", lambda _url: b"not-an-image")
    outcome = artwork.apply_manual_artwork_outcome(
        _art_db(legacy_db), imported_conn, "album", album_id, "https://example.com/a.jpg",
    )
    assert outcome == artwork.MANUAL_ART_INVALID_IMAGE


def test_apply_outcome_write_failed(imported_conn, legacy_db, monkeypatch, album_id):
    monkeypatch.setattr(artwork, "_download_remote_artwork", lambda _url: _image_bytes())

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    # os.replace is the realistic failure point for the cache write (a full
    # or vanished disk), not the pure path construction in _unique_tmp_path.
    monkeypatch.setattr("os.replace", _boom)
    outcome = artwork.apply_manual_artwork_outcome(
        _art_db(legacy_db), imported_conn, "album", album_id, "https://example.com/a.jpg",
    )
    assert outcome == artwork.MANUAL_ART_WRITE_FAILED


def test_is_discogs_image_url_allows_the_default_port():
    assert artwork._is_discogs_image_url("https://i.discogs.com:443/a.jpg") is True
    assert artwork._is_discogs_image_url("https://i.discogs.com/a.jpg") is True


@pytest.mark.parametrize("url", [
    "https://i.discogs.com:8443/a.jpg",
    "https://i.discogs.com:0/a.jpg",
    "https://i.discogs.com:abc/a.jpg",
    "https://i.discogs.com:99999/a.jpg",
])
def test_is_discogs_image_url_rejects_other_ports(url):
    assert artwork._is_discogs_image_url(url) is False


def test_fetch_to_a_non_default_discogs_port_sends_no_token(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    calls = _capture_requests_get(monkeypatch, [_FakeArtworkResponse(body=b"jpeg-bytes")])

    assert artwork._download_remote_artwork("https://i.discogs.com:8443/a.jpg") == b"jpeg-bytes"
    assert "Authorization" not in calls[0][1]
    assert "Referer" not in calls[0][1]


def test_redirect_to_a_non_default_discogs_port_drops_the_token(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    calls = _capture_requests_get(monkeypatch, [
        _FakeArtworkResponse(
            status_code=302,
            headers={"Location": "https://i.discogs.com:8443/b.jpg"},
        ),
        _FakeArtworkResponse(body=b"jpeg-bytes"),
    ])

    assert artwork._download_remote_artwork("https://i.discogs.com/a") == b"jpeg-bytes"
    assert len(calls) == 2
    assert calls[0][1]["Authorization"] == "Discogs token=TOKEN123"
    assert "Authorization" not in calls[1][1]
    assert "Referer" not in calls[1][1]


def test_fetch_to_an_explicit_default_port_still_sends_the_token(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    calls = _capture_requests_get(monkeypatch, [_FakeArtworkResponse(body=b"jpeg-bytes")])

    assert artwork._download_remote_artwork("https://i.discogs.com:443/a.jpg") == b"jpeg-bytes"
    assert calls[0][1]["Authorization"] == "Discogs token=TOKEN123"
    assert calls[0][1]["Referer"] == "https://www.discogs.com/"


def test_failure_message_for_a_default_port_discogs_download_failure(monkeypatch):
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "")
    message = artwork.manual_artwork_failure_message(
        "https://i.discogs.com:443/a.jpg", artwork.MANUAL_ART_DOWNLOAD_FAILED)
    assert "Add your Discogs token" in message


def test_non_200_logs_host_and_status_without_secrets(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    _capture_requests_get(monkeypatch, [
        _FakeArtworkResponse(status_code=403, headers={"Content-Type": "text/html"}),
    ])
    rendered = []

    def _capture_debug(*args):
        rendered.append(args[0] % args[1:] if len(args) > 1 else str(args[0]))

    monkeypatch.setattr(artwork.logger, "debug", _capture_debug)

    data = artwork._download_remote_artwork("https://i.discogs.com/a.jpg?sig=SECRETQUERY")

    assert data is None
    refusals = [line for line in rendered if "refused" in line]
    assert refusals and "i.discogs.com" in refusals[0] and "403" in refusals[0]
    assert all("TOKEN123" not in line and "SECRETQUERY" not in line for line in rendered)


def test_refusal_after_a_redirect_logs_the_refusing_host(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    _capture_requests_get(monkeypatch, [
        _FakeArtworkResponse(
            status_code=302,
            headers={"Location": "https://cdn.example.net/b.jpg?sig=SECRETQUERY"},
        ),
        _FakeArtworkResponse(status_code=403, headers={"Content-Type": "text/html"}),
    ])
    rendered = []

    def _capture_debug(*args):
        rendered.append(args[0] % args[1:] if len(args) > 1 else str(args[0]))

    monkeypatch.setattr(artwork.logger, "debug", _capture_debug)

    data = artwork._download_remote_artwork("https://i.discogs.com/a.jpg")

    assert data is None
    refusals = [line for line in rendered if "refused" in line]
    assert refusals and "cdn.example.net" in refusals[0] and "403" in refusals[0]
    assert all("TOKEN123" not in line and "SECRETQUERY" not in line for line in rendered)


def test_fetch_exception_logs_host_and_error_type_without_secrets(monkeypatch):
    import requests

    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")

    def _raise(url, **_kwargs):
        # A real requests exception string embeds the full URL, signature
        # query included — the log line must not repeat it.
        raise requests.ConnectionError(
            "HTTPSConnectionPool(host='i.discogs.com', port=443): Max retries "
            f"exceeded with url: {url} (Caused by NewConnectionError)"
        )

    monkeypatch.setattr("requests.get", _raise)
    rendered = []

    def _capture_debug(*args):
        rendered.append(args[0] % args[1:] if len(args) > 1 else str(args[0]))

    monkeypatch.setattr(artwork.logger, "debug", _capture_debug)

    data = artwork._download_remote_artwork("https://i.discogs.com/a.jpg?sig=SECRETQUERY")

    assert data is None
    failures = [line for line in rendered if "failed" in line]
    assert failures and "i.discogs.com" in failures[0] and "ConnectionError" in failures[0]
    assert all("TOKEN123" not in line and "SECRETQUERY" not in line for line in rendered)


def test_scheme_relative_redirect_to_a_discogs_host_keeps_the_headers(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    calls = _capture_requests_get(monkeypatch, [
        _FakeArtworkResponse(
            status_code=302, headers={"Location": "//img.discogs.com/b.jpg"}),
        _FakeArtworkResponse(body=b"jpeg-bytes"),
    ])

    assert artwork._download_remote_artwork("https://i.discogs.com/a.jpg") == b"jpeg-bytes"
    assert len(calls) == 2
    assert calls[1][0] == "https://img.discogs.com/b.jpg"
    assert calls[1][1]["Authorization"] == "Discogs token=TOKEN123"
    assert calls[1][1]["Referer"] == "https://www.discogs.com/"


def test_http_to_https_upgrade_redirect_gains_the_discogs_headers(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(artwork, "_discogs_token", lambda: "TOKEN123")
    calls = _capture_requests_get(monkeypatch, [
        _FakeArtworkResponse(
            status_code=302, headers={"Location": "https://i.discogs.com/b.jpg"}),
        _FakeArtworkResponse(body=b"jpeg-bytes"),
    ])

    assert artwork._download_remote_artwork("http://i.discogs.com/a.jpg") == b"jpeg-bytes"
    assert len(calls) == 2
    assert "Authorization" not in calls[0][1]
    assert "Referer" not in calls[0][1]
    assert calls[1][1]["Authorization"] == "Discogs token=TOKEN123"
    assert calls[1][1]["Referer"] == "https://www.discogs.com/"


def test_apply_manual_artwork_returns_false_when_the_write_fails(
    imported_conn, legacy_db, monkeypatch, album_id,
):
    monkeypatch.setattr(artwork, "_download_remote_artwork", lambda _url: _image_bytes())

    real_replace = os.replace

    def _fail_artwork_replace(src, dst, *args, **kwargs):
        # Fail only the artwork cache publish — any other atomic replace in
        # the process must keep working.
        if str(dst).endswith(".jpg"):
            raise OSError("disk full")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr("os.replace", _fail_artwork_replace)
    database = _art_db(legacy_db)
    ok = artwork.apply_manual_artwork(
        database, imported_conn, "album", album_id, "https://example.com/a.jpg",
    )
    assert ok is False
    assert not artwork.artwork_file(database, "album", album_id).exists()
