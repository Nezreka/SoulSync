"""Fetch an NZB from Prowlarr / the indexer here, then hand the usenet
client the file (#2030), the way Sonarr and Radarr do.

Handing the client the download URL instead (SAB ``addurl``, NZBGet
``append`` with a URL) shows that URL, Prowlarr api key included, in the
client's queue, adds a "Trying to fetch NZB" placeholder job, and needs the
client to reach Prowlarr itself.

There is no fallback to the URL handoff: a fetch that fails here fails the
grab, so the key never reaches the client's queue.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

import requests as http_requests

from utils.logging_config import get_logger

logger = get_logger("usenet.nzb_fetch")

FETCH_TIMEOUT = 60
# a big season pack nzb runs to a few tens of MB; anything far past that is
# not an nzb
MAX_NZB_BYTES = 100 * 1024 * 1024
MAX_REDIRECTS = 5
DEFAULT_NZB_FILENAME = "soulsync.nzb"


class NzbFetchError(Exception):
    """The NZB could not be downloaded from the indexer, so nothing was sent
    to the client. The message is safe to show: it never holds the URL (the
    query string carries the Prowlarr api key)."""


def _log_safe(url: str) -> str:
    return str(url or "").split("?", 1)[0]


def _clean_filename(raw: str) -> str:
    """A release name fit to be the client's job name: no folders, no quotes,
    always ending in .nzb (SAB and NZBGet name the job after the file)."""
    name = unquote(str(raw or "")).strip().strip('"').strip("'")
    name = re.split(r"[\\/]", name)[-1].strip()
    name = re.sub(r"[\x00-\x1f]", "", name)
    if not name or name.lower() == ".nzb":
        return ""
    if not name.lower().endswith(".nzb"):
        name += ".nzb"
    return name


def _filename_from_disposition(value: str) -> str:
    if not value:
        return ""
    # rfc 5987 form first (filename*=UTF-8''Name.nzb), then the plain one
    m = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", value, re.IGNORECASE)
    if m:
        return _clean_filename(m.group(1))
    m = re.search(r'filename\s*=\s*("([^"]*)"|[^;]+)', value, re.IGNORECASE)
    if m:
        return _clean_filename(m.group(2) if m.group(2) is not None else m.group(1))
    return ""


def _filename_from_url(url: str) -> str:
    parts = urlsplit(str(url or ""))
    # prowlarr download links carry the release title as file=
    for key in ("file", "title", "name"):
        values = parse_qs(parts.query).get(key)
        if values and values[0].strip():
            return _clean_filename(values[0])
    last = parts.path.rsplit("/", 1)[-1]
    if last.lower().endswith(".nzb"):
        return _clean_filename(last)
    return ""


def _filename_from_nzb(payload: bytes) -> str:
    head = payload[:8192].decode("utf-8", "ignore")
    m = re.search(r'<meta\s+type\s*=\s*"name"\s*>([^<]+)</meta>', head, re.IGNORECASE)
    return _clean_filename(m.group(1)) if m else ""


def looks_like_nzb(payload: bytes) -> bool:
    return b"<nzb" in payload[:8192].lower()


def nzb_filename(url: str, payload: bytes, content_disposition: str = "",
                 final_url: str = "") -> str:
    """The job name the client would have picked from the URL fetch itself:
    the indexer's Content-Disposition filename, else the title in the link
    (or the link it redirected to), else the name inside the nzb."""
    return (_filename_from_disposition(content_disposition)
            or _filename_from_url(url)
            or (_filename_from_url(final_url) if final_url and final_url != url else "")
            or _filename_from_nzb(payload)
            or DEFAULT_NZB_FILENAME)


def _session():
    session = http_requests.Session()
    session.max_redirects = MAX_REDIRECTS
    return session


def fetch_nzb(url: str, timeout: float = FETCH_TIMEOUT) -> Tuple[bytes, str]:
    """Download the NZB at ``url``. Returns ``(payload, filename)``; raises
    ``NzbFetchError`` with a user-safe reason when it can't."""
    session = _session()
    try:
        with session.get(url, timeout=timeout, stream=True, allow_redirects=True) as resp:
            if not resp.ok:
                logger.warning("NZB fetch from %s returned HTTP %s",
                               _log_safe(url), resp.status_code)
                raise NzbFetchError(
                    f"could not download the NZB from the indexer (HTTP {resp.status_code})")
            chunks = []
            total = 0
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_NZB_BYTES:
                    raise NzbFetchError("the indexer sent a file too big to be an NZB")
                chunks.append(chunk)
            payload = b"".join(chunks)
            disposition = str(resp.headers.get("Content-Disposition") or "")
            final_url = str(getattr(resp, "url", "") or url)
    except NzbFetchError:
        raise
    except http_requests.exceptions.RequestException as e:
        # requests errors quote the url, api key and all
        logger.warning("NZB fetch from %s failed (%s)", _log_safe(url), type(e).__name__)
        raise NzbFetchError(
            f"could not download the NZB from the indexer ({type(e).__name__})") from None
    finally:
        session.close()
    if not payload or not looks_like_nzb(payload):
        logger.warning("NZB fetch from %s did not return an NZB (%d bytes)",
                       _log_safe(url), len(payload))
        raise NzbFetchError("the indexer did not send an NZB file")
    return payload, nzb_filename(url, payload, disposition, final_url=final_url)


def resolve_nzb(url_or_bytes, timeout: float = FETCH_TIMEOUT) -> Tuple[bytes, str]:
    """Raw bytes pass through; a URL is fetched here (see the module doc)."""
    if isinstance(url_or_bytes, (bytes, bytearray)):
        payload = bytes(url_or_bytes)
        return payload, _filename_from_nzb(payload) or DEFAULT_NZB_FILENAME
    return fetch_nzb(str(url_or_bytes), timeout=timeout)


__all__ = ["NzbFetchError", "fetch_nzb", "resolve_nzb", "nzb_filename", "looks_like_nzb"]
