"""Blocked artists never show up in discovery.

The blocklist (profile-scoped, core/blocklist) was only enforced where things
get acquired. Discovery surfaces either ignored it or read the legacy global
discovery blacklist unioned with every profile's blocks. This is the one
definition discovery uses: this profile's blocked artists, by name or by the
same-source id. The legacy list was copied into every profile's blocklist
once, and the discover page's blocked artists modal edits the blocklist now,
so the old table is only a rollback copy.

``hide_blocked`` filters a route's JSON by an explicit spec, so each surface
says which lists hold artists, which hold works (tracks/albums, judged by
their artists) and which are plain name lists. A work with any blocked
artist goes, same as the acquisition cascade.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("discovery.blocked")

# item key -> blocklist source, for artist ids that name their source
_ARTIST_ID_KEYS = {
    'spotify_artist_id': 'spotify', 'spotify_id': 'spotify',
    'similar_artist_spotify_id': 'spotify',
    'itunes_artist_id': 'itunes', 'itunes_id': 'itunes',
    'similar_artist_itunes_id': 'itunes',
    'deezer_artist_id': 'deezer', 'deezer_id': 'deezer',
    'similar_artist_deezer_id': 'deezer',
    'musicbrainz_id': 'musicbrainz', 'mbid': 'musicbrainz',
}
_ARTIST_NAME_KEYS = ('artist_name', 'similar_artist_name', 'name')
_WORK_ARTIST_KEYS = ('artist_name', 'artist', 'album_artist', 'primary_artist')
_WORK_TITLE_KEYS = ('name', 'title', 'track_name', 'album_name', 'album')


def _norm(text: Any) -> str:
    return str(text or '').strip().casefold()


@dataclass
class BlockedArtists:
    """What discovery must not show this profile: blocked artists, and what
    it said "not now" to (core/discovery/feedback.py) until that expires.
    ``works`` holds not-now tracks and albums as (artist, title)."""
    names: frozenset = frozenset()
    ids: Dict[str, frozenset] = field(default_factory=dict)
    works: frozenset = frozenset()

    @property
    def is_empty(self) -> bool:
        return not self.names and not any(self.ids.values()) and not self.works

    @classmethod
    def load(cls, database, profile_id) -> 'BlockedArtists':
        names, ids = set(), {s: set() for s in ('spotify', 'itunes', 'deezer', 'musicbrainz')}
        try:
            for row in database.get_blocklist(profile_id or 1, entity_type='artist') or []:
                if row.get('name'):
                    names.add(_norm(row['name']))
                for source, col in (('spotify', 'spotify_id'), ('itunes', 'itunes_id'),
                                    ('deezer', 'deezer_id'), ('musicbrainz', 'musicbrainz_id')):
                    if row.get(col):
                        ids[source].add(str(row[col]))
        except Exception as exc:  # noqa: BLE001 - never take a page down
            logger.warning("blocklist read failed, serving unfiltered: %s", exc)
        from core.discovery.feedback import Taste
        taste = Taste.load(database, profile_id)
        names |= taste.snoozed_artists
        for source, snoozed in taste.snoozed_artist_ids.items():
            ids.setdefault(source, set()).update(snoozed)
        return cls(frozenset(names), {k: frozenset(v) for k, v in ids.items()},
                   taste.snoozed_works)

    def fingerprint(self) -> str:
        """Changes whenever the blocks do: a stored generation keyed on it
        rebuilds after a block instead of showing the artist until its TTL."""
        import hashlib
        parts = sorted(self.names) + sorted(
            f"{source}:{i}" for source, ids in self.ids.items() for i in ids) + sorted(
            f"w:{a}>{t}" for a, t in self.works)
        return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]

    def blocks_name(self, name: Any) -> bool:
        return bool(name) and _norm(name) in self.names

    def blocks_id(self, source: Optional[str], artist_id: Any) -> bool:
        return bool(artist_id) and str(artist_id) in self.ids.get(source or '', ())

    def blocks_artist(self, item: Any) -> bool:
        """An item that IS an artist (a card, a station, a recommendation)."""
        if isinstance(item, str):
            return self.blocks_name(item)
        if not isinstance(item, dict):
            return False
        for key, source in _ARTIST_ID_KEYS.items():
            if self.blocks_id(source, item.get(key)):
                return True
        if item.get('source') in self.ids and self.blocks_id(item['source'], item.get('id')):
            return True
        return any(self.blocks_name(item.get(k)) for k in _ARTIST_NAME_KEYS)

    def blocks_work(self, item: Any) -> bool:
        """A track or album, judged by every artist it carries, or itself
        when it was set aside (a not-now album also hides its tracks)."""
        if not isinstance(item, dict):
            return False
        artists = list(_work_artists(item))
        for artist in artists:
            if self.blocks_artist(artist):
                return True
        if self.works:
            titles = {_norm(item.get(k)) for k in _WORK_TITLE_KEYS if isinstance(item.get(k), str)}
            album = item.get('album')
            if isinstance(album, dict) and isinstance(album.get('name'), str):
                titles.add(_norm(album['name']))
            titles.discard('')
            for artist in artists:
                if isinstance(artist, dict):
                    name = artist.get('artist_name') or artist.get('name')
                else:
                    name = artist
                if isinstance(name, str) and any((_norm(name), t) in self.works for t in titles):
                    return True
        return False


def _work_artists(item: dict) -> Iterable[Any]:
    for key in _WORK_ARTIST_KEYS:
        value = item.get(key)
        if isinstance(value, (str, dict)) and value:
            yield value
    for key in ('artists', 'album_artists'):
        value = item.get(key)
        if isinstance(value, list):
            yield from (v for v in value if v)
    album = item.get('album')
    if isinstance(album, dict):
        for artist in album.get('artists') or []:
            if artist:
                yield artist
    for key in ('spotify_artist_id', 'itunes_artist_id', 'deezer_artist_id'):
        if item.get(key):
            yield {key: item[key]}


# ---- payload filtering -----------------------------------------------------

ARTISTS, WORKS, NAMES, GRAPH = 'artists', 'works', 'names', 'graph'


def _hide_graph(payload: dict, key: str, blocked: BlockedArtists) -> int:
    """An artist map: drop blocked nodes and every edge touching them. The
    center is whoever the person asked to explore, so it stays."""
    nodes = payload.get(key)
    if not isinstance(nodes, list):
        return 0
    gone = {n.get('id') for n in nodes
            if isinstance(n, dict) and n.get('type') != 'center' and blocked.blocks_artist(n)}
    if not gone:
        return 0
    payload[key] = [n for n in nodes if not (isinstance(n, dict) and n.get('id') in gone)]
    edges = payload.get('edges')
    if isinstance(edges, list):
        payload['edges'] = [e for e in edges if not (
            isinstance(e, dict) and (e.get('source') in gone or e.get('target') in gone))]
    return len(gone)


def _walk(node: Any, path: List[str], owner=None) -> List[Tuple[Any, Any, Any]]:
    """(container, key, owner) for every list the path reaches. ``owner`` is
    the (dict, key) of the list the container sits in, when it sits in one."""
    if not path:
        return []
    head, rest = path[0], path[1:]
    many = head.endswith('[]')
    key = head[:-2] if many else head
    if not isinstance(node, dict) or key not in node:
        return []
    if not rest:
        return [(node, key, owner)]
    child = node[key]
    if many and isinstance(child, list):
        out = []
        for t in child:
            out.extend(_walk(t, rest, (node, key)))
        return out
    return _walk(child, rest, owner)


def hide_blocked(payload: Any, spec: Dict[str, str], blocked: BlockedArtists) -> int:
    """Filter ``payload`` in place. ``spec`` maps dotted paths (``[]`` marks
    a list to descend into) to ARTISTS / WORKS / NAMES. Returns how many
    entries were removed. Deeper paths run first, so a section's tracks are
    filtered before the section itself is judged.

    A section or mix whose tracks (a WORKS list) were ALL blocked goes too,
    rather than rendering as a heading over nothing. An emptied name list
    (a station's "with") is not the station's content, so the station stays,
    and a list that was already empty stays: that isn't the block's doing."""
    if blocked.is_empty or not isinstance(payload, dict):
        return 0
    removed = 0
    top_level_removed = 0
    for path, kind in sorted(spec.items(), key=lambda kv: -kv[0].count('.')):
        if kind == GRAPH:
            gone = _hide_graph(payload, path, blocked)
            removed += gone
            top_level_removed += gone
            continue
        test: Callable[[Any], bool] = {
            ARTISTS: blocked.blocks_artist,
            WORKS: blocked.blocks_work,
            NAMES: blocked.blocks_artist,  # plain names, or {name, id} dicts
        }[kind]
        emptied = []
        for container, key, owner in _walk(payload, path.split('.')):
            items = container.get(key)
            if not isinstance(items, list):
                continue
            kept = [i for i in items if not test(i)]
            removed += len(items) - len(kept)
            if container is payload:
                top_level_removed += len(items) - len(kept)
            container[key] = kept
            if kind == WORKS and items and not kept and owner is not None:
                emptied.append((owner, container))
        for (owner_dict, owner_key), dead in emptied:
            siblings = owner_dict.get(owner_key)
            if isinstance(siblings, list) and any(x is dead for x in siblings):
                owner_dict[owner_key] = [x for x in siblings if x is not dead]
                removed += 1
                if owner_dict is payload:
                    top_level_removed += 1
    # a payload's count is of its top-level list, not of names inside items
    if top_level_removed and isinstance(payload.get('count'), int):
        payload['count'] = max(0, payload['count'] - top_level_removed)
    return removed


def hide_blocked_in_response(spec: Dict[str, str]):
    """Decorator for a Flask view that returns jsonify(...): drop blocked
    artists from the listed paths for the current profile."""
    def decorate(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            response = view(*args, **kwargs)
            try:
                return _filter_response(response, spec)
            except Exception as exc:  # noqa: BLE001 - the page matters more
                logger.warning("blocked-artist filter skipped for %s, serving unfiltered: %s",
                               view.__name__, exc)
                return response
        # the marker alone can't prove the filter is the OUTERMOST wrapper
        # (functools.wraps copies __dict__ outward), so the guard test
        # identifies the filter by its code object instead
        wrapper.hides_blocked_artists = True
        return wrapper
    return decorate


def _filter_response(response, spec):
    status = 200
    body = response
    if isinstance(response, tuple):
        body = response[0]
        status = response[1] if len(response) > 1 and isinstance(response[1], int) else 200
    if status != 200 or not getattr(body, 'is_json', False):
        return response
    from core.profile_context import get_current_profile_id
    from database.music_database import get_database

    blocked = BlockedArtists.load(get_database(), get_current_profile_id())
    if blocked.is_empty:
        return response
    payload = body.get_json(silent=True)
    if hide_blocked(payload, spec, blocked):
        body.set_data(json.dumps(payload))
    return response
