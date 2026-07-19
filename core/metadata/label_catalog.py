"""Record-label catalog — the data layer behind the labels watchlist.

A label is monitored like the video-side studio watchlist and displayed like
an artist's discography. Its catalog is a list of RELEASES that belong to
many different artists — so every item resolves to a REAL artist for
acquisition/tagging/filing, NEVER the label.

MusicBrainz-first by deliberate choice: MB's release-group model gives us
DISTINCT albums natively (collapse the label's releases by release-group id),
where Discogs would hand back every pressing/edition as a separate row —
exactly the dedup trap that caused the re-releases-as-owned bug. And MB is
keyless, so the feature isn't gated on the user configuring Discogs.

Purely additive: this module is standalone. It reads only the MusicBrainz
client (via injected getter for testability) and touches no existing path.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("metadata.label_catalog")

# Cap paging like the video studio scan (engine.company_films max_pages) — a
# major label has thousands of releases; the watchlist wants the newest, and
# MB is rate-limited to 1 req/s, so an unbounded walk would be abusive + slow.
_PAGE_SIZE = 100
_MAX_PAGES = 8

# Only real album-type release groups are worth monitoring; skip the noise.
_SKIP_SECONDARY = {'compilation', 'live', 'remix', 'dj-mix', 'mixtape/street',
                   'interview', 'audiobook', 'spokenword'}


def _default_mb():
    from core.musicbrainz_client import MusicBrainzClient
    return MusicBrainzClient()


def search_labels(query: str, *, mb_getter: Optional[Callable] = None,
                  limit: int = 10) -> List[Dict[str, Any]]:
    """Label search results for the search page's label section.
    Returns ``[{id, name, disambiguation, type, area}]`` (MBID as id)."""
    q = str(query or '').strip()
    if not q:
        return []
    mb = (mb_getter or _default_mb)()
    if mb is None:
        return []
    out = []
    for lab in (mb.search_labels(q, limit=limit) or []):
        lid = str(lab.get('id') or '').strip()
        name = str(lab.get('name') or '').strip()
        if not lid or not name:
            continue
        out.append({
            'id': lid,
            'name': name,
            'disambiguation': lab.get('disambiguation') or '',
            'type': lab.get('type') or '',
            'area': (lab.get('area') or {}).get('name') if isinstance(lab.get('area'), dict) else '',
        })
    return out


def _artist_from_credit(release: Dict[str, Any]) -> str:
    """The release's REAL artist display name from artist-credit (never the
    label). Joins multi-artist credits with their join phrases."""
    credits = release.get('artist-credit') or []
    if not isinstance(credits, list):
        return ''
    parts = []
    for c in credits:
        if isinstance(c, dict):
            name = c.get('name') or (c.get('artist') or {}).get('name') or ''
            parts.append(name)
            join = c.get('joinphrase') or ''
            if join:
                parts.append(join)
        elif isinstance(c, str):
            parts.append(c)
    return ''.join(parts).strip()


def _artist_id_from_credit(release: Dict[str, Any]) -> str:
    """The primary artist's MusicBrainz id from artist-credit, when present —
    lets a label release link straight to that artist's detail page."""
    credits = release.get('artist-credit') or []
    if not isinstance(credits, list):
        return ''
    for c in credits:
        if isinstance(c, dict):
            aid = str((c.get('artist') or {}).get('id') or '').strip()
            if aid:
                return aid
    return ''


def label_catalog(label_mbid: str, *, mb_getter: Optional[Callable] = None,
                  max_pages: int = _MAX_PAGES) -> List[Dict[str, Any]]:
    """Distinct albums released on a label, newest-first, each with its REAL
    artist. Returns ``[{artist, album, year, release_group_id}]``.

    Collapses the label's releases by release-group (so the 2010 vinyl and
    2011 CD of one album become ONE entry), keeps only album/EP primary
    types, and skips compilations/live/remix noise.
    """
    mbid = str(label_mbid or '').strip()
    if not mbid:
        return []
    mb = (mb_getter or _default_mb)()
    if mb is None:
        return []

    by_rg: Dict[str, Dict[str, Any]] = {}
    for page in range(max_pages):
        try:
            releases = mb.browse_label_releases(mbid, limit=_PAGE_SIZE,
                                                offset=page * _PAGE_SIZE) or []
        except Exception:
            logger.exception("label_catalog: page %s failed for %s", page, mbid)
            break
        if not releases:
            break
        for rel in releases:
            rg = rel.get('release-group') or {}
            rg_id = str(rg.get('id') or '').strip()
            if not rg_id:
                continue
            primary = str(rg.get('primary-type') or '').lower()
            if primary not in ('album', 'ep'):
                continue
            secondary = {str(s).lower() for s in (rg.get('secondary-types') or [])}
            if secondary & _SKIP_SECONDARY:
                continue
            artist = _artist_from_credit(rel)
            title = str(rg.get('title') or rel.get('title') or '').strip()
            if not artist or not title:
                continue
            # collapse by release-group; keep the EARLIEST release date seen
            date = str(rg.get('first-release-date') or rel.get('date') or '')[:4]
            existing = by_rg.get(rg_id)
            if existing is None or (date and (not existing['year'] or date < existing['year'])):
                by_rg[rg_id] = {'artist': artist,
                                'artist_id': _artist_id_from_credit(rel),
                                'album': title,
                                'year': date, 'release_group_id': rg_id,
                                # a concrete RELEASE mbid → Cover Art Archive art
                                # lives at release scope far more often than at
                                # release-group scope (fixes the blank covers)
                                'release_id': str(rel.get('id') or ''),
                                'primary_type': primary}
        if len(releases) < _PAGE_SIZE:
            break

    items = list(by_rg.values())
    # newest-first (unknown years sink to the bottom)
    items.sort(key=lambda x: x['year'] or '0000', reverse=True)
    return items
