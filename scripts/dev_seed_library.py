#!/usr/bin/env python3
"""Fill an empty Library-v2 database with synthetic data for UI work.

Development only. It wipes the ``lib2_*`` catalogue tables and writes a
fixture chosen for what the *UI* has to survive, not for what a real library
looks like: names long enough to wrap, an artist with nothing in it, albums
that are complete / partial / entirely missing, and files in every
``file_state`` / ``import_status`` / ``verification_status`` the row badges
can render.

Nothing here touches the legacy tables or the download pipeline. Live
download state is in-memory (``core.runtime_state.download_tasks``), so it
cannot be seeded from another process -- see ``--fake-queue`` on the running
server for that half.

    .venv/Scripts/python.exe scripts/dev_seed_library.py
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "database" / "music_library.db"
MUSIC_ROOT = "M:/Music"

# Every lib2 catalogue table the fixture writes, in delete order (children
# first) so the FKs never complain.
CATALOGUE_TABLES = (
    "lib2_track_files",
    "lib2_track_artists",
    "lib2_tracks",
    "lib2_album_artists",
    "lib2_albums",
    "lib2_artist_rollup",
    "lib2_wanted_tracks",
    "lib2_monitor_rules",
    "lib2_artists",
)


def normalize_name(name: str) -> str:
    """Mirror of ``core.library2.importer.normalize_name``."""
    import re

    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def safe_path(*parts: str) -> str:
    cleaned = []
    for part in parts:
        for bad in '<>:"/\\|?*':
            part = part.replace(bad, "_")
        cleaned.append(part.strip())
    return "/".join([MUSIC_ROOT, *cleaned])


# --- The fixture -------------------------------------------------------------
# Each album is (title, type, year, tracks, plan). ``plan`` says what the UI
# should end up showing for that release:
#   complete   -- every track has an active, verified, primary file
#   partial    -- roughly half the tracks have files
#   missing    -- monitored, no files at all (the wanted case)
#   degraded   -- files exist but in states that must render a warning badge
#   unknown    -- files with no quality metadata at all
#   lossy      -- below-profile files, the upgrade-candidate case

LONG_ARTIST = (
    "The Chromatic Aberration of a Thoroughly Modern Symphonic Ensemble "
    "and Their Attendant Orchestral Apparatus"
)
LONG_ALBUM = (
    "Lift Your Skinny Fists Like Antennas to Heaven, and Other Meditations "
    "on the Slow Collapse of Everything We Were Promised"
)

FIXTURE = [
    {
        "name": "Boards of Canada",
        "genres": ["electronic", "ambient", "idm"],
        "monitored": 1,
        "albums": [
            ("Music Has the Right to Children", "album", 1998, 17, "complete"),
            ("Geogaddi", "album", 2002, 23, "complete"),
            ("Tomorrow's Harvest", "album", 2013, 17, "complete"),
            ("In a Beautiful Place Out in the Country", "ep", 2000, 4, "complete"),
        ],
    },
    {
        "name": "Aphex Twin",
        "genres": ["electronic", "idm", "acid techno"],
        "monitored": 1,
        "albums": [
            ("Selected Ambient Works 85-92", "album", 1992, 13, "complete"),
            ("Richard D. James Album", "album", 1996, 15, "partial"),
            ("Syro", "album", 2014, 12, "partial"),
            ("Windowlicker", "single", 1999, 3, "complete"),
            ("Come to Daddy", "ep", 1997, 8, "missing"),
            ("Drukqs", "album", 2001, 30, "lossy"),
        ],
    },
    {
        "name": "Godspeed You! Black Emperor",
        "genres": ["post-rock", "experimental"],
        "monitored": 1,
        "albums": [
            (LONG_ALBUM, "album", 2000, 4, "complete"),
            ("F♯ A♯ ∞", "album", 1997, 3, "partial"),
            ("Slow Riot for New Zerø Kanada", "ep", 1999, 2, "complete"),
        ],
    },
    {
        "name": LONG_ARTIST,
        "genres": ["modern classical", "contemporary"],
        "monitored": 1,
        "albums": [
            ("An Exhaustively Titled Suite in Four Uninterrupted Movements", "album", 2021, 4, "complete"),
            ("Second Movement, Reprised and Considerably Extended", "ep", 2023, 3, "partial"),
        ],
    },
    {
        "name": "AC/DC",
        "genres": ["hard rock", "rock"],
        "monitored": 1,
        "albums": [
            ("Back in Black", "album", 1980, 10, "complete"),
            ("Highway to Hell", "album", 1979, 10, "degraded"),
        ],
    },
    {
        "name": "Sigur Rós",
        "genres": ["post-rock", "ambient"],
        "monitored": 1,
        "albums": [
            ("Ágætis byrjun", "album", 1999, 10, "complete"),
            ("( )", "album", 2002, 8, "partial"),
            ("Von brigði", "album", 1998, 9, "missing"),
        ],
    },
    {
        "name": "女王蜂",
        "genres": ["j-rock", "alternative"],
        "monitored": 1,
        "albums": [
            ("十二単 〜 songs for the philosophy", "album", 2020, 12, "complete"),
            ("BL", "album", 2018, 11, "partial"),
        ],
    },
    {
        "name": "Fleetwood Mac",
        "genres": ["rock", "pop rock"],
        "monitored": 1,
        "albums": [
            ("Rumours", "album", 1977, 11, "missing"),
            ("Tusk", "album", 1979, 20, "missing"),
        ],
    },
    {
        "name": "Autechre",
        "genres": ["idm", "electronic"],
        "monitored": 1,
        "albums": [
            ("Amber", "album", 1994, 11, "degraded"),
            ("Tri Repetae", "album", 1995, 10, "degraded"),
            ("Confield", "album", 2001, 9, "partial"),
        ],
    },
    {
        "name": "Nils Frahm",
        "genres": ["modern classical", "ambient"],
        "monitored": 1,
        "albums": [],  # the empty-artist case
    },
    {
        "name": "Burial",
        "genres": ["dubstep", "future garage"],
        "monitored": 0,
        "albums": [
            ("Untrue", "album", 2007, 13, "unknown"),
            ("Burial", "album", 2006, 12, "unknown"),
        ],
    },
    {
        "name": "Four Tet",
        "genres": ["electronic", "folktronica"],
        "monitored": 1,
        "albums": [
            ("Rounds", "album", 2003, 10, "complete"),
            ("There Is Love in You", "album", 2010, 9, "partial"),
            ("Sixteen Oceans", "album", 2020, 16, "missing"),
        ],
    },
    {
        "name": "Jon Hopkins",
        "genres": ["electronic", "ambient techno"],
        "monitored": 1,
        "albums": [
            ("Immunity", "album", 2013, 8, "lossy"),
            ("Singularity", "album", 2018, 9, "lossy"),
        ],
    },
    {
        "name": "Various Artists",
        "genres": ["compilation"],
        "monitored": 0,
        "albums": [
            ("Late Night Tales: Ambient Edition", "compilation", 2019, 18, "partial"),
        ],
    },
]

TRACK_WORDS = [
    "Kaini", "Telephasic", "Roygbiv", "Olson", "Aquarius", "Turquoise Hexagon",
    "Sixtyten", "Nlogax", "Bocuma", "Amo Bishop", "Dawn Chorus", "Reach for the Dead",
    "Cold Earth", "Palace Posy", "Split Your Infinities", "Nothing Is Real",
    "Sundown", "New Seeds", "Come to Dust", "Semena Mertvykh", "Gyroscope",
    "Alpha and Omega", "The Devil Is in the Details", "You Could Feel the Sky",
    "Corsair", "Opening", "Untitled", "Beach Coma", "Everything You Do",
    "Slow This Bird Down", "Constants Are Changing", "Left Side Drive",
]

FORMATS = {
    "flac": (16, 44100, 1000, "lossless"),
    "flac24": (24, 96000, 2600, "lossless"),
    "mp3_320": (None, 44100, 320, "lossy_high"),
    "mp3_192": (None, 44100, 192, "lossy_low"),
    "m4a": (None, 44100, 256, "lossy_high"),
}


def make_track_title(rng: random.Random, index: int) -> str:
    base = rng.choice(TRACK_WORDS)
    if rng.random() < 0.12:
        # A title long enough to force the table cell to wrap or ellipsize.
        return (
            f"{base} (Extended Rework for the Very Long Title Test, "
            f"Part {index} of an Ongoing Series)"
        )
    if rng.random() < 0.2:
        return f"{base} {index}"
    return base


def file_spec(rng: random.Random, plan: str, position: int):
    """Return the (format, states...) tuple for one file, per album plan."""
    if plan == "lossy":
        fmt = "mp3_192" if position % 2 == 0 else "mp3_320"
        return fmt, "active", "imported", "verified", "pass"
    if plan == "unknown":
        return None, "active", "imported", "unverified", None
    if plan == "degraded":
        # Rotate through the states the row badges must distinguish.
        wheel = [
            ("flac", "quarantined", "failed", "unverified", "fail"),
            ("mp3_320", "missing_suspected", "imported", "verified", "pass"),
            ("flac", "missing_confirmed", "imported", "verified", "pass"),
            ("mp3_320", "active", "staged", "unverified", "skip"),
            ("flac", "active", "imported", "force_imported", "fail"),
            ("flac", "active", "imported", "verified", "pass"),
        ]
        return wheel[position % len(wheel)]
    fmt = "flac24" if rng.random() < 0.15 else ("flac" if rng.random() < 0.7 else "mp3_320")
    return fmt, "active", "imported", "verified", "pass"


def seed(conn: sqlite3.Connection, seed_value: int) -> dict:
    rng = random.Random(seed_value)
    cur = conn.cursor()

    for table in CATALOGUE_TABLES:
        try:
            cur.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass  # table not in this install

    counts = {"artists": 0, "albums": 0, "tracks": 0, "files": 0}

    for art in FIXTURE:
        cur.execute(
            """INSERT INTO lib2_artists
               (name, name_key, sort_name, genres, summary, monitored,
                monitor_new_items, quality_profile_id, image_url, added_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            (
                art["name"],
                normalize_name(art["name"]),
                art["name"],
                json.dumps(art["genres"]),
                f"Synthetic fixture row for {art['name']}. Development data only.",
                art["monitored"],
                "all",
                1,
                None,
            ),
        )
        artist_id = cur.lastrowid
        counts["artists"] += 1
        # Without a monitor rule the wanted projection falls through to
        # ``default_unmonitored``, and the artist card's track fraction
        # collapses to "tracks that already have a file" -- an artist whose
        # albums are entirely missing then reads as 100% complete.
        cur.execute(
            """INSERT INTO lib2_monitor_rules
               (entity_type, entity_id, profile_id, monitored, provenance)
               VALUES ('artist', ?, 1, ?, 'user_explicit')""",
            (artist_id, art["monitored"]),
        )

        for title, atype, year, n_tracks, plan in art["albums"]:
            month, day = rng.randint(1, 12), rng.randint(1, 28)
            cur.execute(
                """INSERT INTO lib2_albums
                   (primary_artist_id, title, album_type, release_date, year,
                    genres, label, track_count, expected_track_count, duration,
                    monitored, origin, quality_profile_id, added_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
                (
                    artist_id, title, atype, f"{year}-{month:02d}-{day:02d}", year,
                    json.dumps(art["genres"][:2]),
                    rng.choice(["Warp", "Constellation", "Domino", "Text", "Erased Tapes", None]),
                    n_tracks, n_tracks, n_tracks * 4 * 60 * 1000,
                    1, "library", 1,
                ),
            )
            album_id = cur.lastrowid
            counts["albums"] += 1
            cur.execute(
                "INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES (?,?,'primary')",
                (album_id, artist_id),
            )
            # 'unknown' releases stay unmonitored so the neutral (never-wanted)
            # album state is represented too, not just green/amber/red.
            cur.execute(
                """INSERT INTO lib2_monitor_rules
                   (entity_type, entity_id, profile_id, monitored, provenance)
                   VALUES ('album', ?, 1, ?, 'cascade')""",
                (album_id, 0 if plan == "unknown" else 1),
            )

            for position in range(1, n_tracks + 1):
                track_title = make_track_title(rng, position)
                cur.execute(
                    """INSERT INTO lib2_tracks
                       (album_id, title, track_number, disc_number, duration,
                        monitored, quality_profile_id, play_count, added_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
                    (
                        album_id, track_title, position, 1,
                        rng.randint(90, 480) * 1000, 1, 1, rng.randint(0, 40),
                    ),
                )
                track_id = cur.lastrowid
                counts["tracks"] += 1
                cur.execute(
                    """INSERT INTO lib2_track_artists(track_id, artist_id, role, position)
                       VALUES (?,?,'primary',0)""",
                    (track_id, artist_id),
                )

                if plan == "missing":
                    continue
                if plan == "partial" and position % 2 == 0:
                    continue

                fmt, state, import_status, verification, acoustid = file_spec(rng, plan, position)
                if fmt is None:
                    bit_depth = sample_rate = bitrate = None
                    tier = None
                    ext = "mp3"
                else:
                    bit_depth, sample_rate, bitrate, tier = FORMATS[fmt]
                    ext = "flac" if fmt.startswith("flac") else ("m4a" if fmt == "m4a" else "mp3")

                path = safe_path(
                    art["name"], f"{title} ({year})",
                    f"{position:02d} - {track_title}.{ext}",
                )
                cur.execute(
                    """INSERT INTO lib2_track_files
                       (track_id, path, size, bitrate, sample_rate, bit_depth, format,
                        quality_tier, source, import_status, verification_status,
                        acoustid_status, is_primary, file_role, file_state,
                        missing_since, content_hash, added_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,'master',?,?,?,
                               datetime('now'),datetime('now'))""",
                    (
                        track_id, path,
                        rng.randint(4, 90) * 1024 * 1024,
                        bitrate, sample_rate, bit_depth, ext, tier,
                        rng.choice(["soulseek", "usenet", "torrent", "manual"]),
                        import_status, verification, acoustid, state,
                        "2026-08-30 12:00:00" if state.startswith("missing") else None,
                        f"{rng.getrandbits(128):032x}",
                    ),
                )
                counts["files"] += 1

    conn.commit()

    from core.library2.artist_rollup import refresh_artist_rollup

    refresh_artist_rollup(conn)
    try:
        from core.library2.wanted import recompute_wanted

        recompute_wanted(conn, profile_id=1)
    except Exception as exc:  # projection is optional for UI work
        print(f"  (wanted projection skipped: {exc})")
    conn.commit()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH), help="database path")
    parser.add_argument("--seed", type=int, default=20260907, help="RNG seed")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    # recompute_wanted reads its rows by column name.
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        counts = seed(conn, args.seed)
    finally:
        conn.close()

    print(
        f"Seeded {counts['artists']} artists, {counts['albums']} albums, "
        f"{counts['tracks']} tracks, {counts['files']} files into {args.db}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
