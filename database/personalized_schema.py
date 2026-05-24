"""Schema + migration helpers for the personalized-playlists subsystem.

Pre-existing state (this PR replaces over time):
- Group A (Fresh Tape / The Archives / Seasonal Mix) lives in
  `discovery_curated_playlists` (track_ids only) and
  `curated_seasonal_playlists` (track_ids + seasonal_tracks join).
  Read paths exist; refresh paths are tied to specific workers.
- Group B (Hidden Gems / Discovery Shuffle / Time Machine / Popular
  Picks / Genre / Daily Mixes) is computed on-demand by
  `PersonalizedPlaylistsService` — no persistence, every call reruns
  the generator with `ORDER BY RANDOM()` so the result rotates.

Post-overhaul (this module's responsibility):
- ALL personalized playlists land in a unified storage layer with a
  stable (profile_id, kind, variant) identity, JSON config per
  playlist (limit, diversity caps, popularity / recency filters,
  exclude-recent-days, randomization seed), and a persistent track
  list that only mutates when the playlist is explicitly refreshed.

Tables created here:

- ``personalized_playlists`` — one row per (profile, kind, variant).
  Variants disambiguate kinds with multiple instances:
    * ``time_machine``: variant = ``'1980s'`` / ``'1990s'`` / ...
    * ``seasonal_mix``: variant = ``'halloween'`` / ``'christmas'`` / ...
    * ``genre_playlist``: variant = ``'rock'`` / ``'electronic_dance'`` / ...
    * ``daily_mix``: variant = ``'1'`` / ``'2'`` / ``'3'`` / ``'4'``
    * Singletons (``hidden_gems``, ``discovery_shuffle``,
      ``popular_picks``, ``fresh_tape``, ``archives``): variant = ``''``.
  Variant '' (empty) is used instead of NULL so the UNIQUE
  constraint behaves predictably (NULL doesn't collide with NULL in
  SQLite UNIQUE indexes — would let multiple singleton rows
  coexist).

- ``personalized_playlist_tracks`` — current snapshot per playlist.
  Cleared + repopulated on refresh; never partial-mutates.

- ``personalized_track_history`` — append-only log of which tracks
  were served by which (profile, kind) over time. Powers the
  ``exclude_recent_days`` config option so generators can avoid
  recommending the same track twice in N days.

The schema is created idempotently — `ensure_personalized_schema`
runs CREATE TABLE IF NOT EXISTS at startup, so existing installs
upgrade silently."""

from __future__ import annotations

from typing import Any

from utils.logging_config import get_logger

logger = get_logger("database.personalized_schema")


PERSONALIZED_PLAYLISTS_DDL = """
CREATE TABLE IF NOT EXISTS personalized_playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL DEFAULT 1,
    kind TEXT NOT NULL,
    variant TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    track_count INTEGER NOT NULL DEFAULT 0,
    last_generated_at TIMESTAMP,
    last_synced_at TIMESTAMP,
    last_generation_source TEXT,
    last_generation_error TEXT,
    is_stale INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (profile_id, kind, variant)
)
"""

# Migration for installs that created the table before is_stale existed.
PERSONALIZED_PLAYLISTS_STALE_MIGRATION = """
ALTER TABLE personalized_playlists ADD COLUMN is_stale INTEGER NOT NULL DEFAULT 0
"""

PERSONALIZED_PLAYLIST_TRACKS_DDL = """
CREATE TABLE IF NOT EXISTS personalized_playlist_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    spotify_track_id TEXT,
    itunes_track_id TEXT,
    deezer_track_id TEXT,
    track_name TEXT,
    artist_name TEXT,
    album_name TEXT,
    album_cover_url TEXT,
    duration_ms INTEGER,
    popularity INTEGER,
    track_data_json TEXT,
    FOREIGN KEY (playlist_id) REFERENCES personalized_playlists(id) ON DELETE CASCADE,
    UNIQUE (playlist_id, position)
)
"""

PERSONALIZED_PLAYLIST_TRACKS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_personalized_tracks_playlist
    ON personalized_playlist_tracks(playlist_id)
"""

PERSONALIZED_TRACK_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS personalized_track_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL DEFAULT 1,
    kind TEXT NOT NULL,
    track_id TEXT NOT NULL,
    served_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

PERSONALIZED_TRACK_HISTORY_INDEX = """
CREATE INDEX IF NOT EXISTS idx_personalized_history_lookup
    ON personalized_track_history(profile_id, kind, track_id, served_at)
"""


def ensure_personalized_schema(connection: Any) -> None:
    """Create the personalized-playlist tables + indexes if missing.

    Idempotent. Safe to call on every app startup. Caller is responsible
    for committing the connection (we leave that to the caller so this
    composes with other schema-init steps in one transaction).
    """
    cursor = connection.cursor()
    cursor.execute(PERSONALIZED_PLAYLISTS_DDL)
    cursor.execute(PERSONALIZED_PLAYLIST_TRACKS_DDL)
    cursor.execute(PERSONALIZED_PLAYLIST_TRACKS_INDEX)
    cursor.execute(PERSONALIZED_TRACK_HISTORY_DDL)
    cursor.execute(PERSONALIZED_TRACK_HISTORY_INDEX)

    # Add is_stale column on installs that created the table before
    # this column existed. SQLite has no `ADD COLUMN IF NOT EXISTS` so
    # we probe with PRAGMA + tolerate the OperationalError that fires
    # when the column is already there.
    cursor.execute("PRAGMA table_info(personalized_playlists)")
    cols = {row[1] for row in cursor.fetchall()}
    if 'is_stale' not in cols:
        try:
            cursor.execute(PERSONALIZED_PLAYLISTS_STALE_MIGRATION)
            logger.info("Added is_stale column to personalized_playlists")
        except Exception as e:
            logger.debug("is_stale column migration: %s", e)

    logger.debug("Personalized-playlist schema ensured")


__all__ = [
    'ensure_personalized_schema',
    'PERSONALIZED_PLAYLISTS_DDL',
    'PERSONALIZED_PLAYLIST_TRACKS_DDL',
    'PERSONALIZED_PLAYLIST_TRACKS_INDEX',
    'PERSONALIZED_TRACK_HISTORY_DDL',
    'PERSONALIZED_TRACK_HISTORY_INDEX',
]
