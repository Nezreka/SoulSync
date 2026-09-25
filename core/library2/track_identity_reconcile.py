"""Conservatively merge provider track IDs for one confirmed album release.

The canonical tracklist resolver intentionally stops at the first provider
that answers. That is sufficient for titles and positions, but it used to
leave every track carrying only that provider's ID even when the album itself
was already matched to several catalogues. This module fetches only those
explicit album IDs and aligns their tracklists without title-search guessing.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, Iterable, Mapping, Optional

from core.library2.importer import dedup_title_key
from core.library2.provider_adapters import (
    TracklistProviderResult,
    fetch_matched_album_tracklists,
)
from core.library2.provider_ids import (
    parse_external_ids,
    provider_only,
    source_ids_from_values,
)


def _album_source_ids(conn: Any, album_id: int) -> Dict[str, str]:
    row = conn.execute(
        """SELECT al.spotify_id AS album_spotify_id,
                  al.musicbrainz_id AS album_musicbrainz_id,
                  al.external_ids AS album_external_ids,
                  ed.spotify_id AS edition_spotify_id,
                  ed.musicbrainz_id AS edition_musicbrainz_id,
                  ed.external_ids AS edition_external_ids
             FROM lib2_albums al
             LEFT JOIN lib2_release_editions ed
                    ON ed.release_group_id=al.id AND ed.is_default=1
            WHERE al.id=?""",
        (album_id,),
    ).fetchone()
    if row is None:
        return {}
    values = parse_external_ids(row["album_external_ids"])
    values.update(parse_external_ids(row["edition_external_ids"]))
    spotify_id = row["edition_spotify_id"] or row["album_spotify_id"]
    musicbrainz_id = (
        row["edition_musicbrainz_id"] or row["album_musicbrainz_id"]
    )
    if spotify_id:
        values["spotify"] = str(spotify_id)
    if musicbrainz_id:
        values["musicbrainz"] = str(musicbrainz_id)
    return provider_only(values)


def _track_source_ids(row: Mapping[str, Any]) -> Dict[str, str]:
    return source_ids_from_values(
        spotify_id=row["spotify_id"],
        musicbrainz_id=row["musicbrainz_id"],
        external_ids=row["external_ids"],
        isrc=row["isrc"],
    )


def _choose_track(
    tracks: list[Dict[str, Any]],
    provider_track: Any,
    *,
    provider: str,
    provider_title_counts: Counter,
    local_title_counts: Counter,
    used_track_ids: set[int],
) -> Optional[Dict[str, Any]]:
    """Return one unambiguous local row; never trust position or title alone."""
    provider_id = str(provider_track.provider_id or "").strip()
    if provider_id:
        exact = [
            track
            for track in tracks
            if track["id"] not in used_track_ids
            and _track_source_ids(track).get(provider) == provider_id
        ]
        if len(exact) == 1:
            return exact[0]

    title_key = dedup_title_key(provider_track.title)
    if not title_key:
        return None
    if provider_track.track_number is not None:
        positioned = [
            track
            for track in tracks
            if track["id"] not in used_track_ids
            and int(track["disc_number"] or 1) == int(provider_track.disc_number or 1)
            and track["track_number"] == provider_track.track_number
            and dedup_title_key(track["title"]) == title_key
        ]
        if len(positioned) == 1:
            return positioned[0]

    # Numbering can differ across providers, but a title unique on both exact
    # release tracklists is still safe. Duplicate interludes/reprises are left
    # untouched unless position + title disambiguated them above.
    if provider_title_counts[title_key] != 1 or local_title_counts[title_key] != 1:
        return None
    titled = [
        track
        for track in tracks
        if track["id"] not in used_track_ids
        and dedup_title_key(track["title"]) == title_key
    ]
    return titled[0] if len(titled) == 1 else None


def reconcile_album_track_provider_ids(
    conn: Any,
    album_id: int,
    *,
    provider_results: Optional[Iterable[TracklistProviderResult]] = None,
) -> Dict[str, int]:
    """Merge track IDs from every exact album provider, preserving conflicts.

    ``provider_results`` is injectable for tests and offline maintenance. The
    caller owns the transaction so a background worker can commit albums
    independently.
    """
    stats = {
        "providers": 0,
        "matched": 0,
        "ids_added": 0,
        "conflicts": 0,
        "skipped": 0,
    }
    source_ids = _album_source_ids(conn, int(album_id))
    if provider_results is None:
        provider_results = fetch_matched_album_tracklists(source_ids)
    results = tuple(provider_results)
    stats["providers"] = len(results)
    if not results:
        return stats

    rows = conn.execute(
        """SELECT id, title, track_number, disc_number, duration,
                  spotify_id, musicbrainz_id, isrc, external_ids
             FROM lib2_tracks
            WHERE album_id=?
            ORDER BY COALESCE(disc_number, 1), track_number, id""",
        (int(album_id),),
    ).fetchall()
    tracks = [dict(row) for row in rows]
    local_title_counts = Counter(dedup_title_key(row["title"]) for row in tracks)

    for result in results:
        provider_tracks = tuple(result.tracks)
        provider_title_counts = Counter(
            dedup_title_key(track.title) for track in provider_tracks
        )
        used_track_ids: set[int] = set()
        for provider_track in provider_tracks:
            target = _choose_track(
                tracks,
                provider_track,
                provider=result.provider,
                provider_title_counts=provider_title_counts,
                local_title_counts=local_title_counts,
                used_track_ids=used_track_ids,
            )
            if target is None:
                stats["skipped"] += 1
                continue
            used_track_ids.add(target["id"])
            stats["matched"] += 1

            stored = parse_external_ids(target["external_ids"])
            identities = _track_source_ids(target)
            changed = False

            incoming_provider_id = str(provider_track.provider_id or "").strip()
            if incoming_provider_id:
                current = identities.get(result.provider)
                if current and current != incoming_provider_id:
                    stats["conflicts"] += 1
                elif not current:
                    stored[result.provider] = incoming_provider_id
                    identities[result.provider] = incoming_provider_id
                    stats["ids_added"] += 1
                    changed = True

            for namespace, incoming in (
                ("isrc", provider_track.isrc),
                ("musicbrainz", provider_track.musicbrainz_id),
            ):
                value = str(incoming or "").strip()
                if not value:
                    continue
                current = identities.get(namespace)
                if current and current != value:
                    stats["conflicts"] += 1
                elif not current:
                    stored[namespace] = value
                    identities[namespace] = value
                    stats["ids_added"] += 1
                    changed = True

            spotify_id = identities.get("spotify")
            musicbrainz_id = identities.get("musicbrainz")
            isrc = identities.get("isrc")
            # Heal indexed columns even when the same value was present only
            # in external_ids. This is not a new identity, but it restores the
            # fast lookup path and canonical schema shape.
            if spotify_id and not target["spotify_id"]:
                target["spotify_id"] = spotify_id
                changed = True
            if musicbrainz_id and not target["musicbrainz_id"]:
                target["musicbrainz_id"] = musicbrainz_id
                changed = True
            if isrc and not target["isrc"]:
                target["isrc"] = isrc
                changed = True

            if not changed:
                continue
            target["external_ids"] = json.dumps(
                stored, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            conn.execute(
                """UPDATE lib2_tracks
                      SET spotify_id=?, musicbrainz_id=?, isrc=?,
                          external_ids=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (
                    target["spotify_id"],
                    target["musicbrainz_id"],
                    target["isrc"],
                    target["external_ids"],
                    target["id"],
                ),
            )

    return stats


__all__ = ["reconcile_album_track_provider_ids"]
