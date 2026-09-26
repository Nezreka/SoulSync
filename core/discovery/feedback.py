"""Discovery remembers what you told it (plan phase 5c).

Four answers to a recommendation, from its ⋯ menu:

- more like this: the seeds that brought it (its explanation) count for more
  when the next recs are ranked, and the artist itself ranks higher.
- less like this: the artist ranks lower everywhere, and lower still where
  the same seed would bring it back (the seed → artist edge).
- not now: hidden for NOT_NOW_DAYS, then it may come back.
- block: the existing blocklist. Not stored here, so resetting taste (which
  clears this table) never clears a block.

``Taste`` is the read side: every ranker asks it for multipliers rather
than reading rows. Hiding a not-now item rides the blocked-artist filter
(core/discovery/blocked.py), which loads the active not-nows beside blocks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from utils.logging_config import get_logger

logger = get_logger("discovery.feedback")

KINDS = ('more', 'less', 'not_now')
ENTITY_TYPES = ('artist', 'album', 'track')
NOT_NOW_DAYS = 30

# more like this: each time a seed earns it, +MORE_SEED_STEP, up to MAX_SEED_WEIGHT
MORE_SEED_STEP = 0.5
MAX_SEED_WEIGHT = 2.0
MORE_ARTIST = 1.5
# less like this: the artist everywhere, and again through the edge that brought it
LESS_ARTIST = 0.4
LESS_EDGE = 0.5


def _norm(text: Any) -> str:
    return str(text or '').strip().casefold()


def entity_key(entity_type: str, name: Any, artist_name: Any = None) -> str:
    """An artist by name; a work by its artist and title."""
    if entity_type == 'artist':
        return _norm(name)
    return f"{_norm(artist_name)}\x1f{_norm(name)}"


def _seed_names(explanation: Any) -> List[str]:
    if not isinstance(explanation, dict):
        return []
    out = []
    for seed in explanation.get('seeds') or []:
        name = seed.get('name') if isinstance(seed, dict) else seed
        if _norm(name):
            out.append(str(name).strip())
    return out


def record(database, profile_id: int, kind: str, entity: Dict[str, Any],
           explanation: Optional[Dict[str, Any]] = None,
           now: Optional[datetime] = None) -> Optional[int]:
    """Store one answer. ``entity`` is {type, name, artist_name?, ids?};
    ``explanation`` is the one the item was shown with (core/discovery/
    explain.py), kept as the seed context. Returns the row id, or None when
    the answer doesn't make sense (unknown kind or type, no name)."""
    entity_type = str(entity.get('type') or '').lower()
    name = str(entity.get('name') or '').strip()
    artist_name = str(entity.get('artist_name') or '').strip() or None
    if kind not in KINDS or entity_type not in ENTITY_TYPES or not name:
        return None
    if entity_type != 'artist' and not artist_name:
        return None     # a work is only identified with its artist
    ids = {str(k): str(v) for k, v in (entity.get('ids') or {}).items() if v}
    context = None
    if isinstance(explanation, dict):
        context = json.dumps({'kind': explanation.get('kind'),
                              'seeds': [{'name': n} for n in _seed_names(explanation)]})
    expires = None
    if kind == 'not_now':
        now = now or datetime.now(timezone.utc)
        # sqlite's datetime('now') format, so the expiry compares as text
        expires = (now + timedelta(days=NOT_NOW_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
    return database.set_discovery_feedback(
        profile_id, entity_type, entity_key(entity_type, name, artist_name), name, kind,
        artist_name=artist_name, ids_json=json.dumps(ids) if ids else None,
        seed_context_json=context, expires_at=expires)


@dataclass
class Taste:
    more_seeds: Dict[str, int] = field(default_factory=dict)
    more_artists: FrozenSet[str] = frozenset()
    less_artists: FrozenSet[str] = frozenset()
    less_edges: FrozenSet[Tuple[str, str]] = frozenset()
    snoozed_artists: FrozenSet[str] = frozenset()
    snoozed_artist_ids: Dict[str, FrozenSet[str]] = field(default_factory=dict)
    snoozed_works: FrozenSet[Tuple[str, str]] = frozenset()

    @classmethod
    def load(cls, database, profile_id) -> 'Taste':
        try:
            rows = database.get_discovery_feedback(profile_id or 1) or []
        except Exception as exc:  # noqa: BLE001 - no feedback is the old behaviour
            logger.debug("discovery feedback read failed: %s", exc)
            rows = []
        return cls.from_rows(rows)

    @classmethod
    def from_rows(cls, rows: Iterable[Dict[str, Any]]) -> 'Taste':
        more_seeds: Dict[str, int] = {}
        more_artists, less_artists, less_edges = set(), set(), set()
        snoozed_artists, snoozed_works = set(), set()
        snoozed_ids: Dict[str, set] = {}
        for row in rows:
            kind = row.get('kind')
            etype = row.get('entity_type')
            artist = _norm(row.get('name') if etype == 'artist' else row.get('artist_name'))
            try:
                seeds = [_norm(n) for n in _seed_names(json.loads(row.get('seed_context_json') or 'null'))]
            except (TypeError, ValueError):
                seeds = []
            if kind == 'more':
                if artist:
                    more_artists.add(artist)
                # a station's seed is its own artist: more of it is exactly that
                for s in seeds:
                    more_seeds[s] = more_seeds.get(s, 0) + 1
            elif kind == 'less':
                if artist:
                    less_artists.add(artist)
                    less_edges.update((s, artist) for s in seeds if s != artist)
            elif kind == 'not_now':
                if etype == 'artist':
                    snoozed_artists.add(artist)
                    try:
                        ids = json.loads(row.get('ids_json') or '{}') or {}
                    except (TypeError, ValueError):
                        ids = {}
                    for source, sid in ids.items():
                        snoozed_ids.setdefault(str(source), set()).add(str(sid))
                elif artist:
                    snoozed_works.add((artist, _norm(row.get('name'))))
        return cls(more_seeds, frozenset(more_artists), frozenset(less_artists),
                   frozenset(less_edges), frozenset(snoozed_artists),
                   {k: frozenset(v) for k, v in snoozed_ids.items()}, frozenset(snoozed_works))

    @property
    def is_empty(self) -> bool:
        return not (self.more_seeds or self.more_artists or self.less_artists
                    or self.snoozed_artists or self.snoozed_works)

    # ---- the multipliers every ranker uses ----

    def seed_weight(self, seed: Any) -> float:
        """How much a seed counts: more-like-this raises it, and a seed you
        said less of (as an artist) counts for less too."""
        key = _norm(seed)
        w = min(MAX_SEED_WEIGHT, 1.0 + MORE_SEED_STEP * self.more_seeds.get(key, 0))
        if key in self.less_artists:
            w *= LESS_ARTIST
        return w

    def artist_factor(self, artist: Any) -> float:
        key = _norm(artist)
        if key in self.less_artists:
            return LESS_ARTIST
        if key in self.more_artists:
            return MORE_ARTIST
        return 1.0

    def edge_factor(self, seed: Any, artist: Any) -> float:
        return LESS_EDGE if (_norm(seed), _norm(artist)) in self.less_edges else 1.0

    def rec_factor(self, artist: Any, seeds: Sequence[Any] = ()) -> float:
        """One recommendation's multiplier: the artist's own, times the best
        of the seeds that brought it (each through its edge)."""
        names = [s.get('name') if isinstance(s, dict) else s for s in (seeds or ())]
        names = [n for n in names if _norm(n)]
        via = max((self.seed_weight(s) * self.edge_factor(s, artist) for s in names), default=1.0)
        return self.artist_factor(artist) * via

    def adjust_related(self, seed: Any, related: Sequence[Dict[str, Any]],
                       weight_key: str = 'weight') -> List[Dict[str, Any]]:
        """A seed's related artists, re-weighted and re-sorted (heaviest first)."""
        if self.is_empty:
            return list(related)
        out = []
        for rel in related:
            rel = dict(rel)
            # a missing weight is the ranker's default of 1.0, not zero
            base = rel.get(weight_key)
            base = 1.0 if base is None else float(base)
            rel[weight_key] = base * self.artist_factor(
                rel.get('name')) * self.edge_factor(seed, rel.get('name'))
            out.append(rel)
        out.sort(key=lambda r: -r[weight_key])
        return out

    def adjust_seeds(self, seeds: Sequence[Dict[str, Any]],
                     weight_key: str = 'weight') -> List[Dict[str, Any]]:
        """Your seed artists with their weights scaled, order kept."""
        if self.is_empty:
            return list(seeds)
        out = []
        for s in seeds:
            base = s.get(weight_key)
            base = 1.0 if base is None else float(base)
            out.append(dict(s, **{weight_key: base * self.seed_weight(s.get('name'))}))
        return out

    def fingerprint(self) -> str:
        """Changes with the feedback that shapes rankings: a stored
        generation keyed on it rebuilds after a more or a less."""
        parts = (sorted(f"m:{k}:{v}" for k, v in self.more_seeds.items())
                 + sorted(f"ma:{a}" for a in self.more_artists)
                 + sorted(f"l:{a}" for a in self.less_artists)
                 + sorted(f"e:{s}>{a}" for s, a in self.less_edges))
        return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]
