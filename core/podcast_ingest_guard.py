"""Guards for everything the podcast side takes in from outside: urls and xml.

podcasts are the one side of the app where a url comes from outside: an rss feed
somebody pastes, an opml file exported by another app, an enclosure url chosen by
whoever runs that feed. every one of those ends up in a server-side fetch.

audiobooks solved the same problem with an allowlist (api/audiobooks.py,
is_allowed_sample_url) because a sample only ever comes from audible or apple.
podcast audio comes from thousands of cdns, so there is no list to check against
and the test has to run the other way round: everything is allowed except the
addresses that are not on the internet.

what this blocks: loopback, private ranges, link-local (which covers the cloud
metadata endpoint at 169.254.169.254), and the reserved blocks. plus anything
that is not http or https, so file:// cannot be used to read a local file.

self-hosting is a real podcast use case, so a lan address is allowed when
podcasts.allow_private_feed_hosts is turned on. it is off by default: the person
who needs it knows they need it, and everybody else should not be running an
open relay to find out.

residual risk worth naming: a host is resolved here and then resolved again by
the http stack when it connects, so a dns entry that changes between the two can
still slip past. pinning the address we checked would mean owning the socket, and
that is a bigger change than this hole warrants. every practical version of this
attack needs a name the user pasted in the first place.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import xml.etree.ElementTree as ET
from typing import Optional, Tuple
from urllib.parse import urlparse

from utils.logging_config import get_logger

logger = get_logger("podcast_ingest_guard")

# a feed is text. ten megabytes is far past the biggest real one and still small
# enough that a hostile server cannot use it to exhaust memory.
MAX_FEED_BYTES = 10 * 1024 * 1024

# an opml file is a list of urls, so it is smaller again.
MAX_OPML_BYTES = 5 * 1024 * 1024

# how many hops a redirect chain may take before we stop following it. each hop
# is re-checked, so this only bounds the work, not the safety.
MAX_REDIRECTS = 5

_ALLOWED_SCHEMES = ("http", "https")


def _allow_private_hosts() -> bool:
    """Whether the user has opted in to feeds on their own network."""
    try:
        from core.settings import config_manager
        if config_manager is None:
            return False
        return bool(config_manager.get("podcasts.allow_private_feed_hosts", False))
    except Exception:                                        # noqa: BLE001
        # a config read that fails must not open the gate
        return False


def _address_is_public(addr: str) -> bool:
    """False for anything that is not a routable internet address."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        # ::ffff:127.0.0.1 is loopback wearing a v6 hat
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve(host: str) -> list:
    """Every address this host answers with, or [] when it does not resolve."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        return []
    return [i[4][0] for i in infos]


def check_url(url: str) -> Tuple[bool, str]:
    """(ok, reason). reason is empty when ok is True and safe to show a user.

    Checked in the order a person would: is it a url at all, is it a scheme we
    fetch, does the name resolve, and is every address it resolves to on the
    internet. ALL addresses have to pass - a name that answers with both a public
    and a private address is a rebinding trick, not a misconfiguration.
    """
    raw = (url or "").strip()
    if not raw:
        return False, "No URL was given"
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False, "That does not parse as a URL"

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return False, f"Only http and https URLs can be fetched, not {scheme or 'a relative path'}"

    host = (parsed.hostname or "").strip().rstrip(".")
    if not host:
        return False, "That URL has no host"

    if _allow_private_hosts():
        return True, ""

    # a bare ip needs no dns
    try:
        ipaddress.ip_address(host)
        return (True, "") if _address_is_public(host) else (
            False, "That address is on a private or local network")
    except ValueError:
        pass

    addresses = _resolve(host)
    if not addresses:
        return False, f"Could not resolve {host}"
    for addr in addresses:
        if not _address_is_public(addr):
            return False, "That host resolves to a private or local network address"
    return True, ""


def has_fetchable_scheme(url: str) -> bool:
    """http or https, and nothing else. Pure - never touches dns.

    This is the check for a parser. Working out whether a NAME points somewhere
    private means resolving it, and a parser that resolves is a parser that does
    network i/o: an opml file with 200 feeds would make 200 lookups before it
    could show a preview. The address check runs at fetch time instead, in
    check_url, which is the only place it can be trusted anyway - the answer can
    change between parsing a file and fetching from it.

    What this DOES settle cheaply is scheme, so file:// and friends never reach
    the watchlist at all.
    """
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    return (parsed.scheme or "").lower() in _ALLOWED_SCHEMES and bool(parsed.hostname)


def is_public_url(url: str) -> bool:
    """check_url without the reason, for call sites that only branch on it."""
    ok, _ = check_url(url)
    return ok


def read_capped(response, limit: int) -> Optional[bytes]:
    """Read at most limit bytes from a streaming response, or None if it exceeds.

    requests' .content reads whatever the server sends, so a feed url pointed at
    something enormous is a memory problem before it is ever a parse problem.
    None rather than a truncated body on purpose: half an xml document is not a
    feed, and treating it as one would report a parse error for a size problem.
    """
    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_guarded(session, url: str, *, timeout: int, limit: int,
                  headers: Optional[dict] = None):
    """GET a checked url, following redirects by hand so each hop is checked too.

    requests follows redirects itself, which would let a url that passes the
    check 302 straight to 127.0.0.1. Every hop goes back through check_url.

    Returns (body_bytes, error). Exactly one of them is None.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        ok, reason = check_url(current)
        if not ok:
            return None, reason
        try:
            resp = session.get(current, timeout=timeout, stream=True,
                               allow_redirects=False, headers=headers or None)
        except Exception as exc:                             # noqa: BLE001
            return None, f"Could not fetch that URL: {exc}"

        if resp.status_code in (301, 302, 303, 307, 308):
            nxt = resp.headers.get("Location") or ""
            resp.close()
            if not nxt:
                return None, "That host redirected without saying where"
            # a relative Location is resolved against the hop we just made
            from urllib.parse import urljoin
            current = urljoin(current, nxt)
            continue

        try:
            if resp.status_code >= 400:
                return None, f"That host returned {resp.status_code}"
            body = read_capped(resp, limit)
        finally:
            resp.close()

        if body is None:
            return None, "That response was too large to read"
        return body, None

    return None, "That URL redirected too many times"


# ---------------------------------------------------------------------------
# xml
# ---------------------------------------------------------------------------

# a DOCTYPE is the only way an xml document can define entities, and entity
# expansion is the whole billion-laughs family. stdlib ElementTree already
# refuses to fetch an EXTERNAL entity, so a feed cannot read a local file, but
# it happily expands INTERNAL ones: measured on this interpreter, four levels
# turned a 224 byte document into 10,000 characters, and each further level
# multiplies by ten again.
#
# no real feed or opml export has a DOCTYPE. refusing the declaration outright
# is both the whole fix and less code than counting expansions.
_DOCTYPE_RE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)


class UnsafeXmlError(ValueError):
    """Raised for xml we will not parse. The message is safe to show a user."""


def parse_xml_safely(content):
    """ET.fromstring for untrusted xml. Raises UnsafeXmlError on a DOCTYPE.

    Takes bytes or str and always parses BYTES, so ElementTree honours the
    document's own encoding declaration rather than whatever we decoded with.
    """
    if isinstance(content, str):
        raw = content.strip().encode("utf-8", "replace")
    else:
        raw = bytes(content or b"")
    if not raw:
        raise UnsafeXmlError("That document was empty")
    # only the prolog can carry a DOCTYPE, so a bounded look is enough and a
    # feed that merely mentions the word in an episode title stays readable
    if _DOCTYPE_RE.search(raw[:4096]):
        raise UnsafeXmlError(
            "That document declares a DOCTYPE, which SoulSync will not parse")
    return ET.fromstring(raw)
