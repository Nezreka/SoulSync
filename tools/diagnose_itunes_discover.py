#!/usr/bin/env python3
"""
Diagnostic script to check iTunes data availability for the Discover page.

Run this script to identify issues with iTunes data population:
- Similar artists missing iTunes IDs
- Discovery pool tracks by source
- Recent albums by source
- Curated playlists status

Usage:
    python tools/diagnose_itunes_discover.py
"""

import sys
import os
import json

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.music_database import MusicDatabase


def diagnose_itunes_discover():
    """Run diagnostic checks for iTunes discover data."""

    print("=" * 60)
    print("iTunes Discover Page Diagnostic Report")
    print("=" * 60)

    db = MusicDatabase()

    # 1. Check Similar Artists
    print("\n[1] SIMILAR ARTISTS")
    print("-" * 40)

    try:
        with db._get_connection() as conn:
            cursor = conn.cursor()

            # Total similar artists
            cursor.execute("SELECT COUNT(*) as total FROM similar_artists")
            total = cursor.fetchone()['total']

            # With iTunes IDs
            cursor.execute("SELECT COUNT(*) as count FROM similar_artists WHERE similar_artist_itunes_id IS NOT NULL")
            with_itunes = cursor.fetchone()['count']

            # With Spotify IDs
            cursor.execute("SELECT COUNT(*) as count FROM similar_artists WHERE similar_artist_spotify_id IS NOT NULL")
            with_spotify = cursor.fetchone()['count']

            # With both
            cursor.execute("""
                SELECT COUNT(*) as count FROM similar_artists
                WHERE similar_artist_itunes_id IS NOT NULL
                AND similar_artist_spotify_id IS NOT NULL
            """)
            with_both = cursor.fetchone()['count']

            print(f"  Total similar artists: {total}")
            print(f"  With iTunes ID: {with_itunes} ({100*with_itunes/total:.1f}%)" if total > 0 else "  With iTunes ID: 0")
            print(f"  With Spotify ID: {with_spotify} ({100*with_spotify/total:.1f}%)" if total > 0 else "  With Spotify ID: 0")
            print(f"  With BOTH IDs: {with_both} ({100*with_both/total:.1f}%)" if total > 0 else "  With BOTH IDs: 0")

            if with_itunes == 0 and total > 0:
                print("  [CRITICAL] No similar artists have iTunes IDs - Hero section will be empty!")
            elif with_itunes < total * 0.5:
                print("  [WARNING] Less than 50% of similar artists have iTunes IDs")
            else:
                print("  [OK] iTunes coverage is adequate")

    except Exception as e:
        print(f"  [ERROR] Could not check similar artists: {e}")

    # 2. Check Discovery Pool
    print("\n[2] DISCOVERY POOL")
    print("-" * 40)

    try:
        with db._get_connection() as conn:
            cursor = conn.cursor()

            # Total tracks
            cursor.execute("SELECT COUNT(*) as total FROM discovery_pool")
            total = cursor.fetchone()['total']

            # By source
            cursor.execute("""
                SELECT source, COUNT(*) as count
                FROM discovery_pool
                GROUP BY source
            """)
            source_counts = {row['source']: row['count'] for row in cursor.fetchall()}

            print(f"  Total tracks: {total}")
            print(f"  Spotify tracks: {source_counts.get('spotify', 0)}")
            print(f"  iTunes tracks: {source_counts.get('itunes', 0)}")

            if source_counts.get('itunes', 0) == 0 and total > 0:
                print("  [CRITICAL] No iTunes tracks in discovery pool - Fresh Tape/Archives will be empty!")
            elif source_counts.get('itunes', 0) < total * 0.3:
                print("  [WARNING] Low iTunes track count in discovery pool")
            else:
                print("  [OK] iTunes tracks present")

    except Exception as e:
        print(f"  [ERROR] Could not check discovery pool: {e}")

    # 3. Check Recent Albums
    print("\n[3] RECENT ALBUMS CACHE")
    print("-" * 40)

    try:
        with db._get_connection() as conn:
            cursor = conn.cursor()

            # Total albums
            cursor.execute("SELECT COUNT(*) as total FROM discovery_recent_albums")
            total = cursor.fetchone()['total']

            # By source
            cursor.execute("""
                SELECT source, COUNT(*) as count
                FROM discovery_recent_albums
                GROUP BY source
            """)
            source_counts = {row['source']: row['count'] for row in cursor.fetchall()}

            print(f"  Total recent albums: {total}")
            print(f"  Spotify albums: {source_counts.get('spotify', 0)}")
            print(f"  iTunes albums: {source_counts.get('itunes', 0)}")

            if source_counts.get('itunes', 0) == 0 and total > 0:
                print("  [CRITICAL] No iTunes albums cached - Recent Releases section will be empty!")
            elif source_counts.get('itunes', 0) < 5:
                print("  [WARNING] Very few iTunes albums cached")
            else:
                print("  [OK] iTunes albums cached")

    except Exception as e:
        print(f"  [ERROR] Could not check recent albums: {e}")

    # 4. Check Curated Playlists
    print("\n[4] CURATED PLAYLISTS")
    print("-" * 40)

    try:
        with db._get_connection() as conn:
            cursor = conn.cursor()

            playlists_to_check = [
                'release_radar',
                'release_radar_spotify',
                'release_radar_itunes',
                'discovery_weekly',
                'discovery_weekly_spotify',
                'discovery_weekly_itunes'
            ]

            for playlist_type in playlists_to_check:
                cursor.execute("""
                    SELECT track_ids_json FROM discovery_curated_playlists
                    WHERE playlist_type = ?
                """, (playlist_type,))
                row = cursor.fetchone()

                if row:
                    track_ids = json.loads(row['track_ids_json'])
                    status = f"{len(track_ids)} tracks"
                    if len(track_ids) == 0:
                        status += " [EMPTY]"
                else:
                    status = "[NOT FOUND]"

                print(f"  {playlist_type}: {status}")

            # Check iTunes-specific playlists
            cursor.execute("""
                SELECT track_ids_json FROM discovery_curated_playlists
                WHERE playlist_type = 'release_radar_itunes'
            """)
            itunes_rr = cursor.fetchone()

            cursor.execute("""
                SELECT track_ids_json FROM discovery_curated_playlists
                WHERE playlist_type = 'discovery_weekly_itunes'
            """)
            itunes_dw = cursor.fetchone()

            if not itunes_rr or len(json.loads(itunes_rr['track_ids_json'])) == 0:
                print("\n  [CRITICAL] release_radar_itunes is empty or missing!")
            if not itunes_dw or len(json.loads(itunes_dw['track_ids_json'])) == 0:
                print("  [CRITICAL] discovery_weekly_itunes is empty or missing!")

    except Exception as e:
        print(f"  [ERROR] Could not check curated playlists: {e}")

    # 5. Check Watchlist Artists
    print("\n[5] WATCHLIST ARTISTS")
    print("-" * 40)

    try:
        with db._get_connection() as conn:
            cursor = conn.cursor()

            # Total artists
            cursor.execute("SELECT COUNT(*) as total FROM watchlist_artists")
            total = cursor.fetchone()['total']

            # With iTunes IDs
            cursor.execute("SELECT COUNT(*) as count FROM watchlist_artists WHERE itunes_artist_id IS NOT NULL")
            with_itunes = cursor.fetchone()['count']

            # With Spotify IDs
            cursor.execute("SELECT COUNT(*) as count FROM watchlist_artists WHERE spotify_artist_id IS NOT NULL")
            with_spotify = cursor.fetchone()['count']

            print(f"  Total watchlist artists: {total}")
            print(f"  With iTunes ID: {with_itunes} ({100*with_itunes/total:.1f}%)" if total > 0 else "  With iTunes ID: 0")
            print(f"  With Spotify ID: {with_spotify} ({100*with_spotify/total:.1f}%)" if total > 0 else "  With Spotify ID: 0")

            if with_itunes == 0 and total > 0:
                print("  [WARNING] No watchlist artists have iTunes IDs - source artist data limited")

    except Exception as e:
        print(f"  [ERROR] Could not check watchlist artists: {e}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY & RECOMMENDED ACTIONS")
    print("=" * 60)
    print("""
If you see [CRITICAL] or [WARNING] messages above, follow these steps:

QUICK FIX - Force Refresh Discover Data:
-----------------------------------------
Call the API endpoint to refresh discover data:
  curl -X POST http://localhost:5000/api/discover/refresh

This will:
- Cache recent albums from your watchlist artists
- Create curated playlists (Release Radar & Discovery Weekly)

FULL FIX - Run Watchlist Scan:
------------------------------
1. Go to the web UI Settings page
2. Click "Scan Watchlist" button
3. Wait for scan to complete

This will:
- Fetch similar artists from MusicMap for each watchlist artist
- Populate the discovery pool with tracks
- Cache recent albums
- Create curated playlists

ROOT CAUSE NOTES:
-----------------
- Similar artists = 0: MusicMap fetch may have failed. Watchlist scan needed.
- Recent albums = 0: cache_discovery_recent_albums() needs to run.
- Curated playlists missing: curate_discovery_playlists() needs to run.

The discover page will now fall back to watchlist artists if similar
artists are not available, so basic functionality should still work.
""")


if __name__ == '__main__':
    diagnose_itunes_discover()
