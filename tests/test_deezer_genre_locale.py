"""Deezer genre names came back in the server's language (#1157, PfannkuchenWolf).

    (SoulSync 3.2.1, Server based in Germany)
    Deezer localizes genre names by the caller's IP, so a German-region server
    gets German genre names instead of English.

Verified against the live API while fixing this:

    curl -s                            .../genre/173  ->  "Films/Games"
    curl -s -H 'Accept-Language: en'   .../genre/173  ->  "Films/Games"
    curl -s -H 'Accept-Language: de'   .../genre/173  ->  "Filme/Videospiele"

So the header overrides the IP geolocation outright. **11 of Deezer's 28 genres
translate**, and album/artist/track payloads are byte-identical between ``en``
and ``de`` — genre names are the only thing that moves, which is what makes
pinning the language safe rather than a blunt instrument.

Why it mattered beyond cosmetics: ``_extract_deezer_fields`` has two branches
for the same album. When the payload carries ``genres.data[].name`` it uses that
name verbatim (localized); otherwise it falls back to ``genre_id`` and the
hardcoded English ``_DEEZER_GENRE_MAP``. One library therefore ended up holding
both "Filme/Videospiele" and "Films/Games" for the same genre, and strict genre
filtering matched neither, because the whitelist is English.

The fix is the header the Deezer DOWNLOAD client has always sent. The metadata
client was the odd one out.

Auditing the map against the live list turned up a second bug nobody reported:
**464 is Metal, not Rap.** Every album that fell back to the map with that genre
was filed as rap.
"""

import re

import pytest

from core.metadata.cache import MetadataCache

CLIENT = 'core/deezer_client.py'
DOWNLOAD_CLIENT = 'core/deezer_download_client.py'


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


# ── the header ──────────────────────────────────────────────────────────────

def test_the_metadata_client_pins_the_language():
    """Without it the names follow the server's IP, which is why a German host
    saw German genres."""
    src = read(CLIENT)
    block = src[src.index('self.session.headers.update({'):]
    block = block[:block.index('})')]
    assert 'Accept-Language' in block


def test_it_pins_english_specifically():
    """The whitelist and _DEEZER_GENRE_MAP are both English, so English is the
    only value that makes the two agree."""
    src = read(CLIENT)
    header = re.search(r"'Accept-Language':\s*'([^']+)'", src).group(1)
    assert header.lower().startswith('en'), f"got {header!r}"


def test_both_deezer_clients_ask_for_the_same_language():
    """The download client already sent this header; the metadata client didn't.
    Two clients hitting one API in different languages is how the mismatch got
    into a single library in the first place."""
    a = re.search(r"'Accept-Language':\s*'([^']+)'", read(CLIENT)).group(1)
    b = re.search(r"'Accept-Language':\s*'([^']+)'", read(DOWNLOAD_CLIENT)).group(1)
    assert a == b, f"metadata client sends {a!r}, download client sends {b!r}"


def test_the_header_is_on_the_session_not_a_single_call():
    """Genres arrive on album, artist and search payloads alike — a per-call
    header would fix whichever endpoint someone remembered."""
    src = read(CLIENT)
    assert 'self.session.headers.update({' in src
    idx = src.index("'Accept-Language'")
    assert src.index('self.session.headers.update({') < idx < src.index('})', idx - 400)


# ── the map ─────────────────────────────────────────────────────────────────

def test_genre_464_is_metal_not_rap():
    """The second bug, found by diffing the map against the live genre list.
    Nobody reported it because a wrong genre looks like bad metadata, not a bug."""
    assert MetadataCache._DEEZER_GENRE_MAP[464] == 'Metal'


def test_the_map_is_still_english():
    """It's the fallback for payloads with no genre name, and it has to agree
    with what the API now returns under Accept-Language: en."""
    m = MetadataCache._DEEZER_GENRE_MAP
    assert m[173] == 'Films/Games', "the exact id from the report"
    assert m[98] == 'Classical'
    assert m[186] == 'Christian'
    assert m[2] == 'African Music'


@pytest.mark.parametrize("genre_id,german", [
    (173, 'Filme/Videospiele'), (98, 'Klassik'), (186, 'Christliche Musik'),
    (2, 'Afrikanische Musik'), (16, 'Asiatische Musik'),
])
def test_no_localized_name_leaked_into_the_map(genre_id, german):
    """If the map were ever rebuilt from an unpinned call it would silently pick
    up the host's language."""
    assert MetadataCache._DEEZER_GENRE_MAP.get(genre_id) != german


def test_every_mapped_name_is_ascii():
    """A cheap catch-all: the translated names carry umlauts and accents, so
    non-ascii in this table means a localized string got in."""
    bad = {k: v for k, v in MetadataCache._DEEZER_GENRE_MAP.items() if not v.isascii()}
    assert bad == {}, f"non-english looking entries: {bad}"
