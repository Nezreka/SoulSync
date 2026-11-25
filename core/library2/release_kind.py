"""Fill a genuinely unknown release kind without reclassifying a release."""

import json
import re
from typing import List, Tuple

KINDS = {'album', 'single', 'ep', 'compilation', 'live', 'remix', 'soundtrack', 'other'}


def confirm_release_kind(conn, album_id: int, value, *, known: bool = True) -> bool:
    kind = str(value or '').strip().lower()
    if not known or kind not in KINDS:
        return False
    changed = conn.execute(
        "UPDATE lib2_albums SET album_type=?, album_type_known=1, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=? AND album_type_known=0",
        (kind, album_id),
    )
    return bool(changed.rowcount)


def settle_shape_kind(conn, album_id: int) -> str:
    """a row typed 'album' by no source's word takes the kind its folder
    took. a bare 'album' is every source's default (itunes and deezer's
    track lookups say it of every single), so the path builder files by the
    release's shape and the row said Album: 'Bubzstep', one track, sat under
    Albums while its folder said Single (#2240). how the download filed it
    first (filed_release), else the release's track count and runtime.
    only a row still unconfirmed (album_type_known=0); it stays unconfirmed,
    so a provider that does say can still correct it. returns the new kind,
    '' when nothing changed."""
    row = conn.execute(
        "SELECT album_type, album_type_known, filed_release, expected_track_count,"
        " track_count, duration FROM lib2_albums WHERE id=?", (int(album_id),)).fetchone()
    if row is None or str(row[0] or 'album').lower() != 'album' or row[1]:
        return ''
    try:
        filed = json.loads(row[2] or '{}')
    except (TypeError, ValueError):
        filed = {}
    if isinstance(filed, dict) and filed.get('type'):
        from core.imports.paths import get_album_type_display
        kind = get_album_type_display(
            filed['type'], filed.get('total_tracks'), str(filed.get('source') or ''),
            bool(filed.get('locked')), filed.get('duration_ms') or None).strip().lower()
    else:
        from core.metadata.release_type import release_type_from_shape
        count, total_ms = row[3] or row[4], row[5]
        if not count:
            # no release count: only the plain single's own shape, one song
            # named like the release. two held songs of a 12-track album
            # are no single
            held = conn.execute(
                "SELECT COUNT(*), MAX(t.duration), MAX(t.title), MAX(al.title)"
                "  FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id"
                " WHERE t.album_id=?", (int(album_id),)).fetchone()
            from core.library2.importer import release_title_key
            if held[0] != 1 or release_title_key(held[2] or '') != release_title_key(held[3] or ''):
                return ''
            count, total_ms = 1, total_ms or held[1]
        kind = release_type_from_shape(count, total_ms)
    if kind not in ('single', 'ep'):
        return ''
    conn.execute("UPDATE lib2_albums SET album_type=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 (kind, int(album_id)))
    conn.execute("UPDATE lib2_albums SET album_type_known=0 WHERE id=?", (int(album_id),))
    return kind


# one time repair: releases a count guess typed single/ep that run 30+ minutes.
# album_type_known can't tell a guess from a provider's word (the kind trigger
# marks every non-album row known), so a row only changes when a provider it
# is matched to positively says album (musicbrainz enrichment, deezer's cached
# record_type, apple's name with no ' - EP'), none says single/ep, the title
# doesn't say it, it isn't a remix bundle (labels call those singles at any
# length), and nobody locked or hand set the type. on boulder's library that
# was 51 rows (love over gold, origami harvest); 195 more long eps with no
# provider word either way stay as they are.
_REMIX_BUNDLE = re.compile(r'(?i)\b(remix(es)?|mixes|versions|reworks?|edits|dubs?|single)\b')
_GUESS_RANGE = {'single': 3, 'ep': 6}


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def _cached_kind(conn, source: str, entity_id) -> str:
    """what the provider's cached raw album said, '' when not cached."""
    from core.metadata.release_type import itunes_release_type
    row = conn.execute(
        "SELECT raw_json FROM metadata_cache_entities WHERE source=? AND entity_type='album' "
        "AND entity_id=?", (source, str(entity_id))).fetchone()
    if not row:
        return ''
    try:
        raw = json.loads(row[0] or '{}')
    except (TypeError, ValueError):
        return ''
    if not isinstance(raw, dict):
        return ''
    if source == 'deezer':
        return str(raw.get('record_type') or '').lower()
    if source == 'itunes' and raw.get('collectionName'):
        return itunes_release_type(raw.get('collectionName'), raw.get('collectionType'))
    return ''


def long_short_release_candidates(conn) -> List[Tuple[int, str, str, int, int]]:
    """(album id, title, stored type, track count, runtime ms) for every
    single/ep row the store rule and a matched provider both call an album.
    read only, so it can be measured before the repair runs."""
    from core.metadata.release_type import SHORT_RELEASE_MS, title_says_ep
    rows = conn.execute(
        """SELECT a.id, a.title, a.album_type, a.expected_track_count, a.enrichment,
                  a.external_ids, COUNT(t.id), SUM(t.duration),
                  SUM(CASE WHEN COALESCE(t.duration, 0) <= 0 THEN 1 ELSE 0 END)
             FROM lib2_albums a JOIN lib2_tracks t ON t.album_id = a.id
            WHERE a.album_type IN ('single', 'ep')
            GROUP BY a.id
           HAVING SUM(t.duration) >= ?
              AND SUM(CASE WHEN COALESCE(t.duration, 0) <= 0 THEN 1 ELSE 0 END) = 0""",
        (SHORT_RELEASE_MS,)).fetchall()
    if not rows:
        return []
    held = set()
    if _table_exists(conn, 'lib2_metadata_overrides'):
        held |= {int(r[0]) for r in conn.execute(
            "SELECT entity_id FROM lib2_metadata_overrides "
            "WHERE entity_type='release_group' AND field_name='album_type'")}
    if _table_exists(conn, 'lib2_entity_locks'):
        held |= {int(r[0]) for r in conn.execute(
            "SELECT entity_id FROM lib2_entity_locks WHERE entity_type='album' "
            "AND (',' || scopes || ',') LIKE '%,metadata,%'")}
    cache = _table_exists(conn, 'metadata_cache_entities')
    out = []
    for album_id, title, kind, expected, enrichment, external_ids, count, runtime, _missing in rows:
        hi = _GUESS_RANGE.get(kind)
        # a count the guess could have produced, with the whole tracklist there
        if not hi or not 1 <= count <= hi or (expected and int(expected) > count):
            continue
        if int(album_id) in held or title_says_ep(title) or _REMIX_BUNDLE.search(title or ''):
            continue
        try:
            enr = json.loads(enrichment or '{}')
        except (TypeError, ValueError):
            enr = {}
        said = set()
        mb = enr.get('musicbrainz') if isinstance(enr, dict) else None
        if isinstance(mb, dict) and mb.get('album_type'):
            said.add(str(mb['album_type']).lower())
        try:
            ids = json.loads(external_ids or '{}')
        except (TypeError, ValueError):
            ids = {}
        if cache and isinstance(ids, dict):
            for src in ('deezer', 'itunes'):
                if ids.get(src):
                    said.add(_cached_kind(conn, src, ids[src]))
        if 'album' not in said or said & {'single', 'ep'}:
            continue
        out.append((int(album_id), title, kind, int(count), int(runtime)))
    return out


def retype_long_short_releases(conn) -> int:
    """the repair: every candidate above becomes an album."""
    changed = 0
    for album_id, *_rest in long_short_release_candidates(conn):
        changed += conn.execute(
            "UPDATE lib2_albums SET album_type='album', album_type_known=1, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND album_type IN ('single', 'ep')",
            (album_id,)).rowcount or 0
    return changed
