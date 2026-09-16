"""Guards for what the podcast side accepts from outside: urls and xml.

Podcasts are the one part of the app where a url comes from somewhere else - a
feed somebody pastes, an opml file another app exported, an enclosure chosen by
whoever runs the feed - and every one of those ends in a server-side fetch.

No live dns here. getaddrinfo is patched, so a name resolves to whatever the
test says it does and the suite does not depend on the network.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from core.podcast_ingest_guard import (
    MAX_FEED_BYTES,
    MAX_OPML_BYTES,
    UnsafeXmlError,
    check_url,
    has_fetchable_scheme,
    is_public_url,
    parse_xml_safely,
    read_capped,
)


def _resolving_to(*addresses):
    """Patch dns so any name answers with these addresses."""
    infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in addresses]
    return patch("socket.getaddrinfo", return_value=infos)


# ---------------------------------------------------------------------------
# scheme
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://example.com/",
    "data:text/html,<script>",
    "ftp://example.com/f",
    "",
    "not-a-url",
])
def test_only_http_and_https_are_fetchable(url):
    ok, reason = check_url(url)
    assert ok is False
    assert reason


def test_a_scheme_check_needs_no_dns():
    """has_fetchable_scheme is used by the opml parser, which must stay pure."""
    with patch("socket.getaddrinfo", side_effect=AssertionError("dns was consulted")):
        assert has_fetchable_scheme("https://feeds.example.com/rss") is True
        assert has_fetchable_scheme("file:///etc/passwd") is False
        assert has_fetchable_scheme("https://") is False


# ---------------------------------------------------------------------------
# address
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("literal", [
    "http://127.0.0.1/x",           # loopback
    "http://10.0.0.5/x",            # private
    "http://192.168.1.1/x",         # private
    "http://172.16.0.1/x",          # private
    "http://169.254.169.254/x",     # link local: the cloud metadata endpoint
    "http://0.0.0.0/x",             # unspecified
    "http://[::1]/x",               # v6 loopback
    "http://[::ffff:127.0.0.1]/x",  # v4 loopback wearing a v6 hat
])
def test_an_address_literal_off_the_internet_is_refused(literal):
    with patch("socket.getaddrinfo", side_effect=AssertionError("a literal needs no dns")):
        ok, reason = check_url(literal)
    assert ok is False
    assert reason


def test_a_name_that_resolves_inward_is_refused():
    with _resolving_to("127.0.0.1"):
        ok, reason = check_url("http://podcast.internal/rss")
    assert ok is False
    assert "private or local" in reason


def test_a_name_that_resolves_to_a_real_address_is_allowed():
    with _resolving_to("93.184.216.34"):
        assert is_public_url("https://feeds.example.com/rss") is True


def test_one_private_answer_is_enough_to_refuse():
    """A name answering with both a public and a private address is a trick."""
    with _resolving_to("93.184.216.34", "127.0.0.1"):
        ok, _ = check_url("https://split.example.com/rss")
    assert ok is False


def test_a_name_that_does_not_resolve_is_refused():
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("nope")):
        ok, reason = check_url("https://nowhere.example/rss")
    assert ok is False
    assert "resolve" in reason


def test_the_opt_in_lets_a_self_hoster_use_their_own_network():
    class _Cfg:
        def get(self, key, default=None):
            return True if key == "podcasts.allow_private_feed_hosts" else default

    with patch("core.settings.config_manager", _Cfg()):
        assert is_public_url("http://192.168.1.50:8000/rss") is True


def test_a_config_that_cannot_be_read_keeps_the_gate_shut():
    class _Boom:
        def get(self, key, default=None):
            raise RuntimeError("database is down")

    with patch("core.settings.config_manager", _Boom()):
        assert is_public_url("http://192.168.1.50:8000/rss") is False


# ---------------------------------------------------------------------------
# xml
# ---------------------------------------------------------------------------

_BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY a "AAAAAAAAAA">
  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
]>
<rss><channel><title>&c;</title></channel></rss>"""


def test_entity_expansion_is_refused():
    with pytest.raises(UnsafeXmlError):
        parse_xml_safely(_BILLION_LAUGHS)


def test_stdlib_would_have_expanded_it():
    """The reason the guard exists, pinned so nobody 'simplifies' it away.

    ElementTree refuses an EXTERNAL entity, so a feed cannot read a local file,
    but it expands INTERNAL ones - which is the whole billion-laughs family.
    """
    import xml.etree.ElementTree as ET
    root = ET.fromstring(_BILLION_LAUGHS)
    grew = len(root.find("channel/title").text)
    # three levels of ten, so the title is 10^3 characters. each further level
    # multiplies by ten again, which is how a document this size reaches
    # gigabytes - the fixture is deliberately kept small enough to be harmless.
    assert grew == 1000
    assert grew > 4 * len(_BILLION_LAUGHS)


def test_a_real_feed_still_parses():
    feed = b'<?xml version="1.0"?><rss><channel><title>Real</title></channel></rss>'
    assert parse_xml_safely(feed).find("channel/title").text == "Real"


def test_a_feed_that_merely_says_the_word_still_parses():
    feed = b"<rss><channel><title>All About DOCTYPE Files</title></channel></rss>"
    assert parse_xml_safely(feed).find("channel/title").text == "All About DOCTYPE Files"


def test_str_input_is_parsed_as_bytes():
    assert parse_xml_safely("<rss><channel><title>x</title></channel></rss>") is not None


def test_empty_is_refused():
    with pytest.raises(UnsafeXmlError):
        parse_xml_safely(b"")


# ---------------------------------------------------------------------------
# size
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, chunks):
        self._chunks = chunks

    def iter_content(self, chunk_size=None):
        return iter(self._chunks)


def test_a_body_within_the_cap_is_returned_whole():
    assert read_capped(_Resp([b"abc", b"def"]), limit=100) == b"abcdef"


def test_a_body_over_the_cap_is_refused_not_truncated():
    """Half an xml document is not a feed, so this must not hand back a prefix."""
    assert read_capped(_Resp([b"x" * 60, b"y" * 60]), limit=100) is None


def test_the_caps_are_sane():
    assert MAX_FEED_BYTES >= 1024 * 1024
    assert MAX_OPML_BYTES >= 1024 * 1024
