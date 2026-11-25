"""the music page's listening rows: what this profile played, as things in
its library it can open and play.

the stats page answers "what did i listen to" in names (artist, album and
title text, whatever the scrobble said). a row on the player's music page
needs the library's own ids, so a card opens an album and plays it. these
read the plays that were matched to a library track
(listening_history.lib2_track_id) and group them by the library's albums and
artists. a play that never matched a library track isn't here: there would
be nothing to open.

only what the caller may see: owned tracks in its library scope, and no
explicit tracks for a profile that hides them (the same three filters the
music channels use). read only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

RECENT = 24          # albums in "recently played"
TOP = 20             # cards in each "most played" row
RECENT_PLAYS = 600   # the plays "recently played" looks back over


def _art(kind: str, entity_id: Any) -> Optional[str]:
    return f"/api/library/v2/artwork/{kind}/{int(entity_id)}" if entity_id else None


def read(database, profile_id: Optional[int], *, hide_explicit: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """{recent, heavy_rotation, top_albums, top_tracks, all_time_artists}.
    never raises on an empty history: every list is just empty."""
    from core.library2.sql_util import owned_sql, scope_visibility_sql

    seen = [owned_sql("track", "t")]
    visible = scope_visibility_sql("track", "t")
    if visible:
        seen.append(visible)
    month = database._listening_time_filter("30d", alias="lh", owner=database._listening_owner(profile_id))
    ever = database._listening_time_filter("all", alias="lh", owner=database._listening_owner(profile_id))
    conn = database._get_connection()
    try:
        if hide_explicit:
            from core.subsonic.library import _has_overrides, track_explicit_sql
            seen.append(f"NOT {track_explicit_sql('t', 'al', _has_overrides(conn))}")
        mine = " AND ".join(seen)
        joins = ("JOIN lib2_tracks t ON t.id = lh.lib2_track_id "
                 "JOIN lib2_albums al ON al.id = t.album_id "
                 "LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id")
        artist_id = "COALESCE(ar.canonical_artist_id, ar.id)"

        # the last plays, newest first, folded to their albums in that order
        recent_rows = conn.execute(f"""
            SELECT al.id, al.title, ar.name, al.year, MAX(p.played_at) AS last_played
            FROM (SELECT lh.lib2_track_id, lh.played_at FROM listening_history lh
                  {ever} AND lh.lib2_track_id IS NOT NULL
                  ORDER BY lh.played_at DESC LIMIT {RECENT_PLAYS}) p
            JOIN lib2_tracks t ON t.id = p.lib2_track_id
            JOIN lib2_albums al ON al.id = t.album_id
            LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            WHERE {mine}
            GROUP BY al.id ORDER BY last_played DESC LIMIT {RECENT}
        """).fetchall()  # noqa: S608 - the fragments are the library's own sql builders

        def artists(where: str) -> list:
            return conn.execute(f"""
                SELECT {artist_id} AS aid, MAX(ar.name), COUNT(*) AS plays
                FROM listening_history lh {joins}
                {where} AND lh.lib2_track_id IS NOT NULL AND ar.id IS NOT NULL AND {mine}
                GROUP BY aid ORDER BY plays DESC LIMIT {TOP}
            """).fetchall()  # noqa: S608

        heavy = artists(month)
        all_time = artists(ever)
        album_rows = conn.execute(f"""
            SELECT al.id, al.title, ar.name, al.year, COUNT(*) AS plays
            FROM listening_history lh {joins}
            {month} AND lh.lib2_track_id IS NOT NULL AND {mine}
            GROUP BY al.id ORDER BY plays DESC LIMIT {TOP}
        """).fetchall()  # noqa: S608
        track_rows = conn.execute(f"""
            SELECT t.id, t.title, ar.name, al.id, al.title, COUNT(*) AS plays
            FROM listening_history lh {joins}
            {month} AND lh.lib2_track_id IS NOT NULL AND {mine}
            GROUP BY t.id ORDER BY plays DESC LIMIT {TOP}
        """).fetchall()  # noqa: S608
    finally:
        conn.close()

    def album(r, plays: bool) -> Dict[str, Any]:
        out = {"album_id": r[0], "title": r[1], "artist": r[2], "year": r[3], "image_url": _art("album", r[0])}
        out["plays" if plays else "played_at"] = r[4]
        return out

    def artist(r) -> Dict[str, Any]:
        return {"artist_id": r[0], "name": r[1], "plays": r[2], "image_url": _art("artist", r[0])}

    return {
        "recent": [album(r, False) for r in recent_rows],
        "heavy_rotation": [artist(r) for r in heavy],
        "top_albums": [album(r, True) for r in album_rows],
        "top_tracks": [{"track_id": r[0], "title": r[1], "artist": r[2], "album_id": r[3],
                        "album": r[4], "plays": r[5], "image_url": _art("album", r[3])}
                       for r in track_rows],
        "all_time_artists": [artist(r) for r in all_time],
    }
