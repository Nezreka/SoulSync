"""YouTube cookie options for yt-dlp — a browser store *or* a pasted cookies.txt.

Settings → YouTube offers two ways to authenticate yt-dlp:

* a **browser dropdown** (Chrome/Firefox/…) → yt-dlp ``cookiesfrombrowser``, which
  reads a logged-in browser's cookie store *on the same machine as SoulSync*. Great
  for local installs, useless on a headless server / Docker box (no browser there).
* a **"Paste cookies.txt"** mode → yt-dlp ``cookiefile``, a Netscape-format cookie
  file the user exports (e.g. with a "Get cookies.txt LOCALLY" extension) and pastes
  in. This is the only path that works for server/Docker users, and it's what makes
  *private* playlists — a user's "Liked Music" (``list=LM``) — actually visible.

This module centralises the precedence and the pasted-file validation so the live
opts (:func:`build_youtube_cookie_opts`) and the settings-save write agree, and so
the seam is unit-testable without I/O. The web layer owns *where* the file lives
(next to ``config.json``); this module only decides the opts and validates content.
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Dict, Optional

# Sentinel dropdown value meaning "use a pasted cookies.txt file" rather than a
# browser name. Anything else non-empty is treated as a browser for cookiesfrombrowser.
PASTE_MODE = "custom"

# ytmusicapi speaks to the same YouTube backend but wants HEADERS, not a cookie
# file, so the pasted cookies.txt has to be projected into them (below).

# The cookie that signs a YouTube API request. Google issues several aliases;
# any one of them works, so take the first present in this order.
_SAPISID_NAMES = ("__Secure-3PAPISID", "__Secure-1PAPISID", "SAPISID")

# A browser export can carry 90 KB+ of cookies across every Google property,
# and YouTube rejects a request whose headers are that large (HTTP 413). Only
# these actually matter for auth.
#
# __Secure-1PSIDTS / __Secure-3PSIDTS are rotating session-refresh tokens
# Google now binds the SID/SAPISID family to.
_ESSENTIAL_COOKIES = frozenset({
    "APISID", "HSID", "SSID", "SID", "SAPISID",
    "__Secure-1PAPISID", "__Secure-3PAPISID",
    "__Secure-1PSID", "__Secure-3PSID",
    "__Secure-1PSIDTS", "__Secure-3PSIDTS",
    "LOGIN_INFO", "PREF", "SOCS", "VISITOR_INFO1_LIVE", "YSC",
})

# Must match the Origin header ytmusicapi sends, or the hash is rejected.
_YTMUSIC_ORIGIN = "https://music.youtube.com"


def build_youtube_cookie_opts(
    mode: Any,
    cookiefile_path: str = "",
    *,
    cookiefile_exists: bool = False,
) -> Dict[str, Any]:
    """Return the yt-dlp cookie options for a given Settings→YouTube ``mode``. Pure.

    * ``mode == PASTE_MODE`` → ``{'cookiefile': path}`` when the file exists, else
      ``{}`` (a stale/missing path must never become a broken cookiefile arg).
    * ``mode`` is any other non-empty string → ``{'cookiesfrombrowser': (mode,)}``.
    * ``mode`` falsy → ``{}`` (anonymous; public playlists only).

    Precedence is structural: a browser name is never ``PASTE_MODE``, so the two
    cookie sources can't both be emitted. No I/O here — the caller passes
    ``cookiefile_exists`` (the ``os.path.exists`` result) so this stays pure.
    """
    m = str(mode or "").strip()
    if m == PASTE_MODE:
        if cookiefile_path and cookiefile_exists:
            return {"cookiefile": str(cookiefile_path)}
        return {}
    if m:
        return {"cookiesfrombrowser": (m,)}
    return {}


def looks_like_cookiefile(content: Any) -> bool:
    """True when ``content`` plausibly is a Netscape/Mozilla ``cookies.txt``.

    Requires at least one real cookie row — a non-comment line with >= 6 TAB-separated
    fields (domain, flag, path, secure, expiry, name[, value]). The ``# Netscape HTTP
    Cookie File`` header alone is NOT enough: a header-only paste carries no auth and
    would silently save a useless file. This guards the save path so pasting junk (a
    URL, JSON, or just the header) is rejected up front instead of being written out
    and making yt-dlp raise mid-extraction.
    """
    if not content or not isinstance(content, str):
        return False
    for raw in content.splitlines():
        line = raw.rstrip("\n")
        if not line or line.lstrip().startswith("#"):
            continue
        if len(line.split("\t")) >= 6:
            return True
    return False


def write_pasted_cookiefile(content: Any, dest_path: str) -> str:
    """Validate + write a pasted ``cookies.txt`` to ``dest_path``.

    Returns the written path on success, or ``""`` when the content is empty /
    doesn't look like a cookie file / can't be written — in which case the caller
    leaves any existing file untouched (a blank save must not wipe a saved cookie).
    Best-effort ``0600`` perms since the file holds live session secrets.
    """
    if not looks_like_cookiefile(content):
        return ""
    try:
        text = content if content.endswith("\n") else content + "\n"
        with open(dest_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            os.chmod(dest_path, 0o600)
        except OSError:
            pass
        return str(dest_path)
    except OSError:
        return ""


def parse_netscape_cookies(content: Any) -> Dict[str, str]:
    """Parse a Netscape ``cookies.txt`` into ``{name: value}``. Pure.

    Deliberately ignores the domain column. The cookies that authenticate a
    YouTube request are Google-wide and an export taken on any Google property
    carries the same values — a jar exported from ``google.de`` authenticates
    music.youtube.com fine. Filtering on "youtube" in the domain silently
    yields zero cookies for those exports, which reads as "not logged in".

    Later rows win, matching how a browser resolves duplicate names.
    """
    cookies: Dict[str, str] = {}
    if not content or not isinstance(content, str):
        return cookies
    for raw in content.splitlines():
        line = raw.rstrip("\n")
        if not line or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 7:
            continue
        name, value = fields[5].strip(), fields[6].strip()
        if name:
            cookies[name] = value
    return cookies


def ytmusic_auth_headers(
    cookies: Dict[str, str], *, timestamp: Optional[int] = None
) -> Optional[Dict[str, str]]:
    """Build ytmusicapi browser-auth headers from parsed cookies, or ``None``. Pure.

    ``None`` means "no usable auth" — the caller then goes anonymous, which
    still resolves public playlists. Only a signed-in request can see a private
    one, and Liked Music (``list=LM``) is always private.

    The ``Authorization: SAPISIDHASH <ts>_<sha1(ts SAPISID origin)>`` scheme is
    YouTube's own; SHA-1 is not a choice we get to make here. ``timestamp`` is
    injectable so the header is reproducible in tests.
    """
    if not isinstance(cookies, dict) or not cookies:
        return None
    sapisid = next((cookies[n] for n in _SAPISID_NAMES if cookies.get(n)), "")
    if not sapisid:
        return None

    stamp = str(int(timestamp if timestamp is not None else time.time()))
    digest = hashlib.sha1(  # noqa: S324 - required by YouTube's auth scheme
        f"{stamp} {sapisid} {_YTMUSIC_ORIGIN}".encode("utf-8")
    ).hexdigest()

    essential = {k: v for k, v in cookies.items() if k in _ESSENTIAL_COOKIES}
    return {
        "Cookie": "; ".join(f"{k}={v}" for k, v in essential.items()),
        "Authorization": f"SAPISIDHASH {stamp}_{digest}",
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "X-Goog-AuthUser": "0",
        "Origin": _YTMUSIC_ORIGIN,
    }


def ytmusic_auth_from_cookiefile(path: Any) -> Optional[Dict[str, str]]:
    """Read ``path`` and project it into ytmusicapi auth headers, or ``None``.

    The one impure entry point: everything it can't do (missing file, unreadable,
    logged-out export) collapses to ``None`` so the caller just goes anonymous.
    """
    if not path or not isinstance(path, str) or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return None
    return ytmusic_auth_headers(parse_netscape_cookies(content))


def ytmusic_auth_from_config() -> Optional[Dict[str, str]]:
    """ytmusicapi headers from Settings → YouTube cookies, or ``None`` (anonymous).

    Same PASTE_MODE + cookies_file rule as the playlist import path: only a
    pasted cookies.txt can be projected into headers. ``cookiesfrombrowser`` is
    yt-dlp's reader and leaves no file for us to hash. Public catalog search
    still works with ``None``.
    """
    try:
        from core.settings import config_manager
        if str(config_manager.get("youtube.cookies_browser", "") or "").strip() != PASTE_MODE:
            return None
        return ytmusic_auth_from_cookiefile(config_manager.get("youtube.cookies_file", ""))
    except Exception:  # noqa: BLE001 - auth is best-effort; anonymous still works
        return None


__all__ = [
    "PASTE_MODE",
    "build_youtube_cookie_opts",
    "looks_like_cookiefile",
    "write_pasted_cookiefile",
    "parse_netscape_cookies",
    "ytmusic_auth_headers",
    "ytmusic_auth_from_cookiefile",
    "ytmusic_auth_from_config",
]
