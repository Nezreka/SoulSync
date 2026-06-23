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

import os
from typing import Any, Dict

# Sentinel dropdown value meaning "use a pasted cookies.txt file" rather than a
# browser name. Anything else non-empty is treated as a browser for cookiesfrombrowser.
PASTE_MODE = "custom"


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


__all__ = [
    "PASTE_MODE",
    "build_youtube_cookie_opts",
    "looks_like_cookiefile",
    "write_pasted_cookiefile",
]
