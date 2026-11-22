"""The ids providers give "Various Artists": a credit, not an artist (#2229).

A compilation's album artist on spotify is "Various Artists", id
0LyfQWJT6nXafLPZqxe9Of, the same id on every compilation spotify has. it came
along with a download, landed on the one real artist the download was
credited to (ryo (supercell)), and from then on every other compilation's
download was found by that id and renamed after ryo.

So one of these ids never says who an artist is. it is never stored on a
library artist, never a track artist's provider id, never looked up to find
or name an artist, never a watchlist match.

Each id is checked against the provider's own page:
  * spotify 0LyfQWJT6nXafLPZqxe9Of, discogs 194, amazon B000QJO93Y: the url
    links of musicbrainz's own Various Artists entry
  * musicbrainz 89ad4ac3-39f7-470e-963a-56509c546377: musicbrainz's special
    purpose artist (core/musicbrainz_service.py had it already)
  * deezer 5080: api.deezer.com/artist/5080 answers "Various Artists"
  * tidal 2935: tidal.com/browse/artist/2935 is "Various Artists"
itunes has no one id for it (a store's compilations carry no artist id, or
a local one per storefront), so none is listed.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

VARIOUS_ARTISTS_IDS: Dict[str, str] = {
    "spotify": "0LyfQWJT6nXafLPZqxe9Of",
    "musicbrainz": "89ad4ac3-39f7-470e-963a-56509c546377",
    "deezer": "5080",
    "discogs": "194",
    "tidal": "2935",
    "amazon": "B000QJO93Y",
}

# ids nothing else can look like: a provider-less value is matched on these.
# the numeric ones are another provider's real artist (deezer's 194 is not
# discogs' 194), so they only count with their own provider
_UNMISTAKABLE = frozenset({
    VARIOUS_ARTISTS_IDS["spotify"],
    VARIOUS_ARTISTS_IDS["musicbrainz"],
    VARIOUS_ARTISTS_IDS["amazon"],
})


def _provider(value: Any) -> str:
    """the provider a key names: 'spotify', 'spotify_artist_id', 'Deezer',
    'deezer_id', 'apple' ... -> the key in VARIOUS_ARTISTS_IDS, else ''."""
    text = str(value or "").strip().lower()
    for provider in VARIOUS_ARTISTS_IDS:
        if text.startswith(provider):
            return provider
    return ""


def _bare(value: Any) -> str:
    """the id out of a uri or link: spotify:artist:<id>, .../artist/194-Various"""
    text = str(value or "").strip()
    if ":" in text and not text.lower().startswith("http"):
        text = text.rsplit(":", 1)[-1]
    if "/" in text:
        text = text.rstrip("/").rsplit("/", 1)[-1]
    return text.strip()


def is_various_artists_id(value: Any, provider: Optional[Any] = None) -> bool:
    """True when ``value`` is a provider's Various Artists id. ``provider``
    (a provider name, or a key like 'deezer_id') is needed for the numeric
    ones; without it only the ids no other provider uses count."""
    text = _bare(value)
    if not text:
        return False
    if text in _UNMISTAKABLE or text.lower() == VARIOUS_ARTISTS_IDS["musicbrainz"]:
        return True
    key = _provider(provider)
    if key and text == VARIOUS_ARTISTS_IDS[key]:
        return True
    # discogs links carry the name after the id: 194-Various
    return key == "discogs" and text.split("-", 1)[0] == VARIOUS_ARTISTS_IDS["discogs"]


def without_various_artists_ids(ids: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """``ids`` ({provider: id}) without the Various Artists ids."""
    return {provider: value for provider, value in (ids or {}).items()
            if not is_various_artists_id(value, provider)}


__all__ = ["VARIOUS_ARTISTS_IDS", "is_various_artists_id", "without_various_artists_ids"]
