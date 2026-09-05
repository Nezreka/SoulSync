#!/usr/bin/env python3
"""Explain why `/api/wishlist/count` and `/api/wishlist/tracks` disagree.

STRICTLY READ-ONLY. The database is opened with SQLite's ``mode=ro`` URI, so
this cannot delete, clean up or "repair" anything — which matters because the
tracks endpoint itself used to mutate the wishlist, and any diagnostic that
did the same would destroy the evidence it was sent to collect.

A production report showed ``count = 614`` while the tracks endpoint returned
611 rows, with no way to tell which stage dropped the other three. This walks
the exact same pipeline the endpoint walks and reports the population after
each stage, naming the rows lost at each one:

    stored            SELECT COUNT(*)                       (what the badge shows)
    parsed            MusicDatabase.get_wishlist_tracks     (drops unreadable JSON)
    deduped           sanitize_and_dedupe_wishlist_tracks   (drops repeated track ids)
    visible           what `/api/wishlist/tracks` returns

Usage:
    python scripts/diagnose_wishlist_visibility.py [--db PATH] [--profile N] [--json]

With no ``--db`` it uses the configured database path.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _open_readonly(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _default_db_path() -> str:
    try:
        from core.settings import config_manager

        configured = config_manager.get("database.path", "database/music_library.db")
    except Exception:
        configured = "database/music_library.db"
    if not os.path.isabs(configured):
        configured = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), configured
        )
    return configured


def diagnose(db_path: str, profile_id: int) -> Dict[str, Any]:
    conn = _open_readonly(db_path)
    try:
        stored = conn.execute(
            "SELECT COUNT(*) AS n FROM wishlist_tracks WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()["n"]

        rows = conn.execute(
            "SELECT id, spotify_track_id, spotify_data, source_info, source_type, "
            "       date_added "
            "FROM wishlist_tracks WHERE profile_id = ? ORDER BY date_added",
            (profile_id,),
        ).fetchall()
    finally:
        conn.close()

    unreadable: List[Dict[str, Any]] = []
    parsed: List[Dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["spotify_data"])
        except (TypeError, ValueError) as exc:
            unreadable.append({
                "wishlist_id": row["id"],
                "spotify_track_id": row["spotify_track_id"],
                "error": str(exc),
            })
            continue
        parsed.append({
            "wishlist_id": row["id"],
            "track_id": row["spotify_track_id"],
            "date_added": row["date_added"],
            "name": (payload or {}).get("name") if isinstance(payload, dict) else None,
        })

    seen: Dict[str, int] = {}
    shadowed: List[Dict[str, Any]] = []
    for entry in parsed:
        key = entry["track_id"]
        if not key:
            continue
        if key in seen:
            shadowed.append({**entry, "shadowed_by_wishlist_id": seen[key]})
        else:
            seen[key] = entry["wishlist_id"]

    # Rows that carry no usable id at all survive dedupe but are indistinguishable
    # to every consumer keyed on the track id (removal, retry, presence checks).
    idless = [entry for entry in parsed if not entry["track_id"]]

    # Two Library-v2 entities holding one provider id collapse into ONE wishlist
    # row, because the row key is built from those ids — so this belongs in a
    # wishlist visibility report even though the cause lives in the catalogue.
    identity_conflicts = _provider_id_conflicts(db_path)

    duplicate_ids = Counter(entry["track_id"] for entry in parsed if entry["track_id"])
    repeated = {k: v for k, v in duplicate_ids.items() if v > 1}

    by_origin: Dict[str, int] = defaultdict(int)
    for row in rows:
        try:
            info = json.loads(row["source_info"]) if row["source_info"] else {}
        except (TypeError, ValueError):
            info = {}
        by_origin[str((info or {}).get("source") or "other")] += 1

    visible = len(parsed) - len(shadowed)
    return {
        "database": db_path,
        "profile_id": profile_id,
        "stored": stored,
        "parsed": len(parsed),
        "visible": visible,
        "gap": stored - visible,
        "dropped_unreadable_json": unreadable,
        "dropped_duplicate_track_id": shadowed,
        "rows_without_track_id": idless,
        "repeated_track_ids": repeated,
        "rows_by_source": dict(by_origin),
        "library_v2_identity_conflicts": identity_conflicts,
    }


def _provider_id_conflicts(db_path: str) -> List[Dict[str, Any]]:
    """Library-v2 entities sharing one provider id, if the catalogue is present."""
    try:
        from core.library2.match_status import provider_id_conflicts
    except Exception:
        return []
    conn = _open_readonly(db_path)
    try:
        found: List[Dict[str, Any]] = []
        for kind in ("artist", "album", "track"):
            found.extend(provider_id_conflicts(conn, kind))
        return found
    except Exception:
        return []
    finally:
        conn.close()


def _print_report(result: Dict[str, Any]) -> None:
    print(f"database : {result['database']}")
    print(f"profile  : {result['profile_id']}")
    print()
    print(f"  stored (SQL COUNT, what /api/wishlist/count reports) : {result['stored']}")
    print(f"  parsed (readable JSON)                               : {result['parsed']}")
    print(f"  visible (what /api/wishlist/tracks returns)           : {result['visible']}")
    print(f"  GAP                                                  : {result['gap']}")
    print()
    if result["dropped_unreadable_json"]:
        print(f"Dropped for unreadable JSON ({len(result['dropped_unreadable_json'])}):")
        for entry in result["dropped_unreadable_json"]:
            print(f"  wishlist_id={entry['wishlist_id']} "
                  f"track_id={entry['spotify_track_id']!r}: {entry['error']}")
        print()
    if result["dropped_duplicate_track_id"]:
        print(f"Dropped as duplicate track ids ({len(result['dropped_duplicate_track_id'])}):")
        for entry in result["dropped_duplicate_track_id"]:
            print(f"  wishlist_id={entry['wishlist_id']} track_id={entry['track_id']!r} "
                  f"added={entry['date_added']} "
                  f"(shadowed by wishlist_id={entry['shadowed_by_wishlist_id']})")
        print()
    if result["rows_without_track_id"]:
        print(f"Rows with no track id ({len(result['rows_without_track_id'])}):")
        for entry in result["rows_without_track_id"]:
            print(f"  wishlist_id={entry['wishlist_id']} added={entry['date_added']}")
        print()
    if not result["gap"]:
        print("No gap: every stored row is visible in the tracks endpoint.")
    print("rows by source_info.source:", result["rows_by_source"])
    conflicts = result.get("library_v2_identity_conflicts") or []
    if conflicts:
        print()
        print(f"Library-v2 provider ids held by more than one entity ({len(conflicts)}):")
        summary = Counter((e["entity_type"], e["service"]) for e in conflicts)
        for (kind, service), count in sorted(summary.items(), key=lambda kv: -kv[1]):
            print(f"  {kind:6} {service:12} {count}")
        print("  first 20:")
        for entry in conflicts[:20]:
            print(f"    {entry['entity_type']} {entry['service']}={entry['external_id']!r} "
                  f"-> ids {entry['entity_ids']}")
        print("  (pre-existing data; the write-time guard only stops NEW ones."
              " Use --json for the full list.)")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=None, help="path to music_library.db")
    parser.add_argument("--profile", type=int, default=1, help="profile id (default 1)")
    parser.add_argument("--json", action="store_true", help="emit the raw report as JSON")
    args = parser.parse_args(argv)

    db_path = args.db or _default_db_path()
    if not os.path.exists(db_path):
        print(f"database not found: {db_path}", file=sys.stderr)
        return 2

    result = diagnose(db_path, args.profile)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
