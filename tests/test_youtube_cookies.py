"""Settings → YouTube cookie options: browser store vs a pasted cookies.txt.

#902: syncing a YouTube *Music* "Liked Music" playlist (list=LM) needs auth, and on
a server/Docker box there's no local browser for cookiesfrombrowser to read — so we
let users paste a cookies.txt (yt-dlp cookiefile). These pin the precedence (so the
two cookie sources can never both be emitted), the paste validation (junk must not be
written out and break yt-dlp), and the fail-safe write (a blank save never wipes a
saved file).
"""

from __future__ import annotations

from core.youtube_cookies import (
    PASTE_MODE,
    build_youtube_cookie_opts,
    looks_like_cookiefile,
    write_pasted_cookiefile,
)

NETSCAPE = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tLOGIN_INFO\tsecretvalue\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tanother\n"
)


# ── precedence (pure opts) ──────────────────────────────────────────────────

def test_empty_mode_is_anonymous():
    assert build_youtube_cookie_opts("") == {}
    assert build_youtube_cookie_opts(None) == {}


def test_browser_mode_uses_cookiesfrombrowser():
    assert build_youtube_cookie_opts("firefox") == {"cookiesfrombrowser": ("firefox",)}


def test_paste_mode_uses_cookiefile_when_present():
    opts = build_youtube_cookie_opts(PASTE_MODE, "/cfg/youtube_cookies.txt", cookiefile_exists=True)
    assert opts == {"cookiefile": "/cfg/youtube_cookies.txt"}


def test_paste_mode_without_a_real_file_is_anonymous_not_broken():
    # stale/missing path must NOT become a cookiefile arg yt-dlp would choke on
    assert build_youtube_cookie_opts(PASTE_MODE, "/cfg/gone.txt", cookiefile_exists=False) == {}
    assert build_youtube_cookie_opts(PASTE_MODE, "", cookiefile_exists=True) == {}


def test_sources_are_mutually_exclusive():
    # a browser name is never PASTE_MODE, so cookiefile + cookiesfrombrowser can't co-occur
    for mode in ("chrome", "firefox", PASTE_MODE, ""):
        opts = build_youtube_cookie_opts(mode, "/x.txt", cookiefile_exists=True)
        assert not ("cookiefile" in opts and "cookiesfrombrowser" in opts)


# ── paste validation ────────────────────────────────────────────────────────

def test_accepts_netscape_header_and_cookie_rows():
    assert looks_like_cookiefile(NETSCAPE) is True
    # no header but a valid tab-separated cookie row still counts
    assert looks_like_cookiefile(".youtube.com\tTRUE\t/\tTRUE\t123\tSID\tv") is True


def test_rejects_junk_paste():
    assert looks_like_cookiefile("") is False
    assert looks_like_cookiefile("   ") is False
    assert looks_like_cookiefile(None) is False
    assert looks_like_cookiefile("https://music.youtube.com/playlist?list=LM") is False
    assert looks_like_cookiefile('{"cookies": []}') is False
    assert looks_like_cookiefile("# Netscape HTTP Cookie File\n# only comments\n") is False


# ── fail-safe write ─────────────────────────────────────────────────────────

def test_write_persists_valid_cookiefile(tmp_path):
    dest = tmp_path / "youtube_cookies.txt"
    out = write_pasted_cookiefile(NETSCAPE, str(dest))
    assert out == str(dest)
    assert dest.read_text().startswith("# Netscape HTTP Cookie File")


def test_write_appends_trailing_newline(tmp_path):
    dest = tmp_path / "c.txt"
    write_pasted_cookiefile(NETSCAPE.rstrip("\n"), str(dest))
    assert dest.read_text().endswith("\n")


def test_write_refuses_junk_and_leaves_no_file(tmp_path):
    dest = tmp_path / "c.txt"
    assert write_pasted_cookiefile("not a cookie file", str(dest)) == ""
    assert not dest.exists()


def test_write_refuses_junk_without_clobbering_existing(tmp_path):
    # a blank/garbage save must NOT wipe a previously-saved cookie file
    dest = tmp_path / "c.txt"
    write_pasted_cookiefile(NETSCAPE, str(dest))
    before = dest.read_text()
    assert write_pasted_cookiefile("", str(dest)) == ""
    assert dest.read_text() == before


# ── regression: youtube_client must USE the helper, not pass 'custom' as a browser ──
# (Docker bug: pasted cookies threw yt-dlp 'unsupported browser: "custom"' because the
#  client built cookiesfrombrowser=('custom',) instead of a cookiefile.)

def test_resolve_cookie_opts_routes_custom_to_cookiefile(monkeypatch, tmp_path):
    import core.youtube_client as yt
    cookiefile = tmp_path / "youtube_cookies.txt"
    cookiefile.write_text(".youtube.com\tTRUE\t/\tTRUE\t123\tSID\tv\n")
    cfg = {'youtube.cookies_browser': 'custom', 'youtube.cookies_file': str(cookiefile)}
    monkeypatch.setattr('core.settings.config_manager.get',
                        lambda k, d=None: cfg.get(k, d))
    opts = yt._resolve_cookie_opts()
    assert opts == {'cookiefile': str(cookiefile)}
    assert 'cookiesfrombrowser' not in opts          # never the bogus browser arg


def test_resolve_cookie_opts_browser_mode_unchanged(monkeypatch):
    import core.youtube_client as yt
    cfg = {'youtube.cookies_browser': 'firefox', 'youtube.cookies_file': ''}
    monkeypatch.setattr('core.settings.config_manager.get',
                        lambda k, d=None: cfg.get(k, d))
    assert yt._resolve_cookie_opts() == {'cookiesfrombrowser': ('firefox',)}


def test_resolve_cookie_opts_custom_missing_file_is_anonymous(monkeypatch):
    import core.youtube_client as yt
    cfg = {'youtube.cookies_browser': 'custom', 'youtube.cookies_file': '/nope/gone.txt'}
    monkeypatch.setattr('core.settings.config_manager.get',
                        lambda k, d=None: cfg.get(k, d))
    assert yt._resolve_cookie_opts() == {}            # not a broken cookiefile arg


# ── ytmusicapi browser auth ───────────────────────────────────────────────
# ytmusicapi wants HEADERS, not a cookie file, so the same pasted cookies.txt
# is projected into them. Without this, only public playlists resolve — Liked
# Music (list=LM) is always private.

from core.youtube_cookies import (  # noqa: E402
    parse_netscape_cookies,
    ytmusic_auth_from_cookiefile,
    ytmusic_auth_headers,
)

# Real export shape. The domain is a non-YouTube Google property on purpose —
# see test_cookies_are_parsed_regardless_of_domain.
_JAR = (
    "# Netscape HTTP Cookie File\n"
    ".google.de\tTRUE\t/\tTRUE\t1799999999\t__Secure-3PAPISID\tsecret-sapisid\n"
    ".google.de\tTRUE\t/\tTRUE\t1799999999\tSID\tsid-value\n"
    ".google.de\tTRUE\t/\tTRUE\t1799999999\tHSID\thsid-value\n"
)


def test_cookies_are_parsed_regardless_of_domain():
    # The auth cookies are Google-wide; an export taken on google.de signs a
    # music.youtube.com request fine. Filtering on "youtube" in the domain
    # yields zero cookies for such a jar and reads as "not logged in".
    cookies = parse_netscape_cookies(_JAR)
    assert cookies["__Secure-3PAPISID"] == "secret-sapisid"
    assert cookies["SID"] == "sid-value"


def test_parse_skips_comments_and_short_rows():
    assert parse_netscape_cookies("# just a header\n") == {}
    assert parse_netscape_cookies(".x\tTRUE\t/\tTRUE\t1\tNAME\n") == {}  # 6 fields, no value
    assert parse_netscape_cookies(None) == {}
    assert parse_netscape_cookies(12345) == {}


def test_parse_reads_httponly_prefixed_rows():
    # Netscape format marks an HttpOnly cookie by prefixing its domain field
    # with "#HttpOnly_" instead of leaving the line plain. Treating that as an
    # ordinary comment silently drops exactly the session-identity cookies
    # (SID, __Secure-1PSID, HSID, ...) that authenticate the request — the
    # export still "looks" complete but the account reads as signed out.
    jar = (
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly_.google.de\tTRUE\t/\tTRUE\t1799999999\t__Secure-1PSID\thttponly-psid\n"
        "#HttpOnly_.google.de\tTRUE\t/\tTRUE\t1799999999\tHSID\thttponly-hsid\n"
        ".google.de\tTRUE\t/\tTRUE\t1799999999\t__Secure-3PAPISID\tsecret-sapisid\n"
    )
    cookies = parse_netscape_cookies(jar)
    assert cookies["__Secure-1PSID"] == "httponly-psid"
    assert cookies["HSID"] == "httponly-hsid"
    assert cookies["__Secure-3PAPISID"] == "secret-sapisid"


def test_looks_like_cookiefile_accepts_httponly_only_export():
    jar = "#HttpOnly_.google.de\tTRUE\t/\tTRUE\t1799999999\t__Secure-1PSID\thttponly-psid\n"
    assert looks_like_cookiefile(jar) is True


def test_later_duplicate_row_wins():
    jar = _JAR + ".youtube.com\tTRUE\t/\tTRUE\t1799999999\tSID\tnewer-sid\n"
    assert parse_netscape_cookies(jar)["SID"] == "newer-sid"


def test_auth_headers_are_reproducible_for_a_fixed_timestamp():
    import hashlib
    headers = ytmusic_auth_headers(parse_netscape_cookies(_JAR), timestamp=1_700_000_000)
    expected = hashlib.sha1(
        b"1700000000 secret-sapisid https://music.youtube.com").hexdigest()
    assert headers["Authorization"] == f"SAPISIDHASH 1700000000_{expected}"
    assert headers["Origin"] == "https://music.youtube.com"


def test_auth_headers_drop_non_essential_cookies():
    # A browser export can be 90 KB+; YouTube 413s on headers that large.
    jar = _JAR + ".google.de\tTRUE\t/\tTRUE\t1799999999\tNID\tbulky-unrelated-value\n"
    cookie = ytmusic_auth_headers(parse_netscape_cookies(jar))["Cookie"]
    assert "__Secure-3PAPISID=secret-sapisid" in cookie
    assert "bulky-unrelated-value" not in cookie


def test_auth_headers_keep_sidts_rotating_tokens():
    # Regression for the "Sign in to listen to your liked tracks" bug:
    # SAPISIDHASH alone verifies fine and generic library calls work, but
    # Liked Music (list=LM) serves the signed-out view without these two
    # (sigma67/ytmusicapi#962).
    jar = (
        _JAR
        + ".google.de\tTRUE\t/\tTRUE\t1799999999\t__Secure-1PSIDTS\tsidts-1p\n"
        + ".google.de\tTRUE\t/\tTRUE\t1799999999\t__Secure-3PSIDTS\tsidts-3p\n"
    )
    cookie = ytmusic_auth_headers(parse_netscape_cookies(jar))["Cookie"]
    assert "__Secure-1PSIDTS=sidts-1p" in cookie
    assert "__Secure-3PSIDTS=sidts-3p" in cookie


def test_sapisid_aliases_are_accepted_in_priority_order():
    for name in ("__Secure-3PAPISID", "__Secure-1PAPISID", "SAPISID"):
        headers = ytmusic_auth_headers({name: "v"}, timestamp=1)
        assert headers is not None
        assert "SAPISIDHASH" in headers["Authorization"]


def test_no_sapisid_means_no_auth():
    # A logged-out export must go anonymous, not send a bogus signature.
    assert ytmusic_auth_headers({"PREF": "x", "YSC": "y"}) is None
    assert ytmusic_auth_headers({}) is None
    assert ytmusic_auth_headers(None) is None


def test_auth_from_missing_or_bad_file_is_none(tmp_path):
    assert ytmusic_auth_from_cookiefile(str(tmp_path / "nope.txt")) is None
    assert ytmusic_auth_from_cookiefile("") is None
    assert ytmusic_auth_from_cookiefile(None) is None
    empty = tmp_path / "empty.txt"
    empty.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    assert ytmusic_auth_from_cookiefile(str(empty)) is None


def test_auth_from_real_file_round_trips(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(_JAR, encoding="utf-8")
    headers = ytmusic_auth_from_cookiefile(str(path))
    assert headers is not None
    assert "SAPISIDHASH" in headers["Authorization"]


def test_auth_from_config_anonymous_when_not_paste_mode(monkeypatch):
    monkeypatch.setattr(
        "core.settings.config_manager.get",
        lambda key, default="": "firefox" if key == "youtube.cookies_browser" else default,
    )
    from core.youtube_cookies import ytmusic_auth_from_config
    assert ytmusic_auth_from_config() is None


def test_auth_from_config_paste_mode_reads_cookiefile(monkeypatch, tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(_JAR, encoding="utf-8")

    def _get(key, default=""):
        if key == "youtube.cookies_browser":
            return PASTE_MODE
        if key == "youtube.cookies_file":
            return str(path)
        return default

    monkeypatch.setattr("core.settings.config_manager.get", _get)
    from core.youtube_cookies import ytmusic_auth_from_config
    headers = ytmusic_auth_from_config()
    assert headers is not None
    assert "SAPISIDHASH" in headers["Authorization"]


def test_auth_from_config_exception_is_anonymous(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("config down")

    monkeypatch.setattr("core.settings.config_manager.get", _boom)
    from core.youtube_cookies import ytmusic_auth_from_config
    assert ytmusic_auth_from_config() is None
