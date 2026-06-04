"""Reconcile a source playlist against a media-server playlist (pure).

Lifted verbatim from the inline three-pass matcher in
``web_server.get_server_playlist_tracks`` so it can be unit-tested and so the
two #768 fixes live in importable, covered code:

  Pass 0  user-confirmed overrides (``sync_match_cache``), applied first.
  Pass 1  exact normalized-title match.
  Pass 2  fuzzy match on ``"artist title"`` (SequenceMatcher >= 0.75).
  Extra   server tracks no source claimed -> ``match_status='extra'``.

Two bug fixes over the original inline version:

* #768 Bug A — the source side is YouTube/streaming-shaped (title
  ``"Artist - Song"``, artist ``"Official Artist"``). The original passes
  compared the raw title, so ``"Arctic Monkeys - Do I Wanna Know?"`` never
  matched the library's ``"Do I Wanna Know?"`` and the track showed as
  unmatched while its server copy showed as an orphan "extra". We now also try
  the canonicalized source title/artist (see ``core.text.source_title``).

* #768 Bug B — the original built the per-source ``src_entry`` WITHOUT
  ``source_track_id``, so the editor UI never received it; "Find & add" then
  posted an empty id and the manual match was never persisted (it reverted to
  "extra" on reload, and re-adding duplicated the track). ``src_entry`` now
  carries ``source_track_id``.

Pure, no I/O. ``override_pairs`` (``{source_idx: server_idx}``) is computed by
the caller via ``core.sync.match_overrides.resolve_match_overrides`` so the DB
lookup stays out of this module.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from core.text.source_title import canonical_source_track

_FUZZY_THRESHOLD = 0.75

_FEAT_RE = re.compile(r'\s*[\(\[](?:feat|ft)\.?[^\)\]]*[\)\]]', re.IGNORECASE)
_REMASTER_RE = re.compile(
    r'\s*[\(\[](?:\d{4}\s+)?remaster(?:ed)?(?:\s+version)?\s*[\)\]]', re.IGNORECASE)
_EDITION_RE = re.compile(
    r'\s*[\(\[](?:deluxe|special|anniversary|legacy|expanded|limited)(?:\s+edition)?\s*[\)\]]',
    re.IGNORECASE)


def norm_title(t: str) -> str:
    """Strip feat./ft., remaster, and edition qualifiers for comparison only.
    Byte-faithful to web_server's inline ``_norm_title``."""
    t = _FEAT_RE.sub('', t or '')
    t = _REMASTER_RE.sub('', t)
    t = _EDITION_RE.sub('', t)
    return t.lower().strip()


def _src_entry(src: Dict[str, Any], position_fallback: int) -> Dict[str, Any]:
    return {
        'name': src.get('name', ''),
        'artist': src.get('artist', ''),
        'album': src.get('album', ''),
        'image_url': src.get('image_url', ''),
        'duration_ms': src.get('duration_ms', 0),
        'position': src.get('position', position_fallback),
        # #768 Bug B: echo the source id back so the editor can persist a
        # manual "Find & add" override against it.
        'source_track_id': src.get('source_track_id', '') or '',
    }


def _resolved_artist(src: Dict[str, Any]) -> str:
    """Artist string, falling back to the first of an ``artists`` list."""
    artist = src.get('artist', '')
    if not artist and src.get('artists'):
        a = src['artists'][0] if src['artists'] else ''
        artist = a.get('name', a) if isinstance(a, dict) else str(a)
    return artist or ''


def reconcile_playlist(
    source_tracks: List[Dict[str, Any]],
    server_tracks: List[Dict[str, Any]],
    override_pairs: Optional[Dict[int, int]] = None,
) -> List[Dict[str, Any]]:
    """Return the combined matched/missing/extra view (list of dicts).

    Each combined entry has ``source_track``, ``server_track``,
    ``match_status`` ('matched'|'missing'|'extra'), ``confidence``, and
    ``override: True`` on override hits."""
    override_pairs = override_pairs or {}
    combined: List[Dict[str, Any]] = []
    used_server_indices: set[int] = set()
    unmatched_source: List[tuple[int, Dict[str, Any], str]] = []

    # Precompute normalized server titles once.
    server_norm = [norm_title(svr.get('title', '')) for svr in server_tracks]

    for i, src in enumerate(source_tracks):
        src_artist = _resolved_artist(src)
        src_name = src.get('name', '')
        src_entry = _src_entry({**src, 'artist': src_artist}, i)

        # Pass 0: user-confirmed override.
        if i in override_pairs:
            j = override_pairs[i]
            used_server_indices.add(j)
            combined.append({
                'source_track': src_entry,
                'server_track': server_tracks[j],
                'match_status': 'matched',
                'confidence': 1.0,
                'override': True,
            })
            continue

        # Pass 1: exact normalized-title match — try the raw source title AND
        # the canonicalized one (strips "Artist - " prefix / channel artist).
        canon_title, _canon_artist = canonical_source_track(src_name, src_artist)
        candidates = {norm_title(src_name), norm_title(canon_title)}
        best_idx = -1
        for j, svr_norm in enumerate(server_norm):
            if j in used_server_indices:
                continue
            if svr_norm in candidates:
                best_idx = j
                break

        if best_idx >= 0:
            used_server_indices.add(best_idx)
            combined.append({
                'source_track': src_entry,
                'server_track': server_tracks[best_idx],
                'match_status': 'matched',
                'confidence': 1.0,
            })
        else:
            idx = len(combined)
            combined.append({
                'source_track': src_entry,
                'server_track': None,
                'match_status': 'missing',
                'confidence': 0.0,
            })
            # Carry the canonical artist for the fuzzy pass.
            unmatched_source.append((idx, src_entry, _canon_artist or src_artist))

    # Pass 2: fuzzy match on remaining unmatched source tracks. Build the key
    # from the canonicalized title/artist so YouTube-shaped sources can pair.
    for combo_idx, src_entry, canon_artist in unmatched_source:
        canon_title, _ = canonical_source_track(src_entry['name'], src_entry['artist'])
        src_key = f"{canon_artist} {norm_title(canon_title)}".strip().lower()
        best_score = 0.0
        best_j = -1
        for j, svr in enumerate(server_tracks):
            if j in used_server_indices:
                continue
            svr_key = f"{svr.get('artist', '')} {norm_title(svr.get('title', ''))}".strip().lower()
            score = SequenceMatcher(None, src_key, svr_key).ratio()
            if score > best_score and score >= _FUZZY_THRESHOLD:
                best_score = score
                best_j = j
        if best_j >= 0:
            used_server_indices.add(best_j)
            combined[combo_idx] = {
                'source_track': src_entry,
                'server_track': server_tracks[best_j],
                'match_status': 'matched',
                'confidence': round(best_score, 3),
            }

    # Extra: server tracks no source claimed.
    for j, svr in enumerate(server_tracks):
        if j not in used_server_indices:
            combined.append({
                'source_track': None,
                'server_track': svr,
                'match_status': 'extra',
                'confidence': 0.0,
            })

    # #766: a source row with no art of its own (e.g. a YouTube source, which
    # provides none) borrows its MATCHED server track's cover so both sides of
    # the editor show an image. Keyed off the actual pairing — works for
    # "Artist - Title" rows that a fuzzy title lookup would miss. Source rows
    # that already have their own art (Spotify CDN, etc.) keep it.
    for entry in combined:
        st = entry.get('source_track')
        sv = entry.get('server_track')
        if st and sv and not st.get('image_url') and sv.get('thumb'):
            st['image_url'] = sv['thumb']

    return combined


__all__ = ["reconcile_playlist", "norm_title"]
