"""The draw every discovery-pool playlist shares: taste-weighted, one version
of each song, no junk.

the pool is big (145k tracks on a real install) and it used to be read by
ORDER BY RANDOM(): popular picks was a random handful of the thousands of
tracks tied at the top popularity (mozart, a zelda soundtrack, 'various
artists' wagner), time machine and genre were pure chance. now each candidate
weighs what it is to you:

- artist: how close its artist sits to your library in the similar-artist
  graph (how many of your artists list it, and how high)
- genre: how much its genres match what you play (the genre-taste profile)

and the playlist is a weighted draw on that, so it's still a fresh set every
refresh, tilted toward you. pure, no db.
"""

from __future__ import annotations

import json
import math
import random
import re
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.personalized.daily_mixes import in_season, is_filler, song_key, weighted_order

# not an artist anyone follows
_JUNK_ARTISTS = frozenset({'various artists', 'various', 'unknown artist', 'unknown', 'va'})
# audiobook and podcast chapters that land in the pool as "tracks"
_CHAPTER = re.compile(r'^\s*(?:kapitel|chapter|capítulo|capitulo|chapitre|teil|folge|episode)\s+\d+',
                      re.I)

# how much each signal counts; the floor keeps a stranger possible, so
# discovery still discovers
W_FLOOR, W_ARTIST, W_GENRE = 0.15, 0.50, 0.35


def _genres(track: Dict[str, Any]) -> List[str]:
    raw = track.get('_artist_genres_raw')
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = []
    return [str(g).lower() for g in raw] if isinstance(raw, list) else []


def is_junk(track: Dict[str, Any], today: Optional[date] = None) -> bool:
    """a compilation credit, a chapter, a skit, or christmas in july"""
    artist = str(track.get('artist_name') or '').strip().lower()
    title = str(track.get('track_name') or '')
    if artist in _JUNK_ARTISTS or _CHAPTER.search(title):
        return True
    if is_filler(title, track.get('duration_ms')):
        return True
    shaped = {'name': title, 'album': {'name': track.get('album_name') or ''}}
    return not in_season(shaped, today or date.today())


def artist_affinity(seeds: Iterable[Dict[str, Any]],
                    similars_by_seed: Dict[str, List[Dict[str, Any]]]) -> Dict[str, float]:
    """{name_lower: 0..1}: how close an artist sits to what you PLAY. each of
    your played artists pulls on its similar artists by log(plays) times how
    close the edge is, summed over everyone pointing at it. the whole
    similar-artist table won't do: the pool is built from it, so every pool
    artist was 'close'."""
    weight = {str(s.get('name') or '').strip().lower(): float(s.get('weight') or 0)
              for s in seeds}
    raw: Dict[str, float] = {}
    for seed, rows in (similars_by_seed or {}).items():
        pull = math.log1p(weight.get(str(seed).strip().lower(), 0.0))
        for r in rows or []:
            key = str(r.get('name') or '').strip().lower()
            if key:
                raw[key] = raw.get(key, 0.0) + pull * float(r.get('score', 1.0) or 0.0)
    # your own artists are as close as it gets
    for name, w in weight.items():
        if name and w > 0:
            raw[name] = raw.get(name, 0.0) + math.log1p(w) * 2.0
    # by rank, not by the top value: one artist with 6,000 plays squashed
    # everyone else's neighbours to nothing
    order = sorted((k for k, v in raw.items() if v > 0), key=lambda k: (raw[k], k))
    n = len(order)
    return {k: (i + 1) / n for i, k in enumerate(order)}


def nearest(artists: Dict[str, float], n: int) -> List[str]:
    """the n artists closest to what you play"""
    return sorted(artists, key=lambda k: (-artists[k], k))[:n]


_LIVE = re.compile(r'[\(\[-]\s*live\b|\blive (?:at|from|in|on)\b', re.I)


def version_factor(title: Any) -> float:
    """a live take is a poor first listen of an artist you don't know"""
    return 0.3 if _LIVE.search(str(title or '')) else 1.0


def taste_weight(track: Dict[str, Any], genre_profile: Dict[str, float],
                 artists: Dict[str, float]) -> float:
    """0.15..1.0: how much this track is yours to discover"""
    artist = artists.get(str(track.get('artist_name') or '').strip().lower(), 0.0)
    genre = max((float(genre_profile.get(g, 0.0)) for g in _genres(track)), default=0.0)
    base = W_FLOOR + W_ARTIST * artist + W_GENRE * min(1.0, genre)
    return base * version_factor(track.get('track_name'))


def draw(tracks: List[Dict[str, Any]], weight: Callable[[Dict[str, Any]], float],
         rng: random.Random, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """every usable candidate, heavier ones likelier to come first, one version
    of each song (the first drawn). the caller's diversity caps cut it down."""
    usable = [t for t in tracks if not is_junk(t, today)]
    ordered = weighted_order(usable, [max(1e-6, float(weight(t))) for t in usable], rng)
    seen, out = set(), []
    for t in ordered:
        key = song_key(t.get('artist_name'), t.get('track_name'))
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def spread(tracks: List[Dict[str, Any]], gap: int = 2) -> List[Dict[str, Any]]:
    """the same tracks, in draw order as far as it goes, but no artist within
    `gap` slots of itself while someone else can go there (three u2 songs in
    a row topped hidden gems)."""
    pending = list(tracks)
    out: List[Dict[str, Any]] = []
    while pending:
        recent = {str(t.get('artist_name') or '').lower() for t in out[-gap:]}
        pick = next((i for i, t in enumerate(pending)
                     if str(t.get('artist_name') or '').lower() not in recent), 0)
        out.append(pending.pop(pick))
    return out
