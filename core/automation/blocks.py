"""Static block definitions for the automation builder UI.

Returned verbatim by `/api/automations/blocks` (with `known_signals`
injected by the route from `signals.collect_known_signals`).

Three top-level lists:
- `TRIGGERS` — WHEN blocks: schedule, daily/weekly time, app started,
  event triggers (track_downloaded, batch_complete, etc.), signal_received,
  webhook_received.
- `ACTIONS` — DO blocks: process_wishlist, scan_library, etc.
- `NOTIFICATIONS` — THEN blocks: discord/pushbullet/telegram/webhook,
  plus fire_signal and run_script then-actions.

Each block carries an optional ``scope`` tag so the SAME definitions can
feed both the music and the (isolated) video automation builders:
  - ``"both"``  — generic; shown on both sides (schedule, notifications, …).
  - ``"video"`` — video-only; shown only on the video builder.
  - absent      — treated as music-only (the default); never shown on video.
Use :func:`blocks_for_scope` to get the filtered lists for one side.
"""

from __future__ import annotations

TRIGGERS: list[dict] = [
    {"type": "schedule", "label": "Schedule", "icon": "clock", "scope": "both", "description": "Run on a timer interval", "available": True,
     "config_fields": [
         {"key": "interval", "type": "number", "label": "Every", "default": 6, "min": 1},
         {"key": "unit", "type": "select", "label": "Unit",
          "options": [{"value": "minutes", "label": "Minutes"}, {"value": "hours", "label": "Hours"}, {"value": "days", "label": "Days"}],
          "default": "hours"}
     ]},
    {"type": "daily_time", "label": "Daily Time", "icon": "clock", "scope": "both", "description": "Run every day at a specific time", "available": True,
     "config_fields": [
         {"key": "time", "type": "time", "label": "At", "default": "03:00"}
     ]},
    {"type": "weekly_time", "label": "Weekly Schedule", "icon": "calendar", "scope": "both", "description": "Run on specific days of the week at a set time", "available": True,
     "config_fields": [
         {"key": "time", "type": "time", "label": "At", "default": "03:00"},
         {"key": "days", "type": "multi_select", "label": "Days",
          "options": [{"value": "mon", "label": "Mon"}, {"value": "tue", "label": "Tue"}, {"value": "wed", "label": "Wed"},
                      {"value": "thu", "label": "Thu"}, {"value": "fri", "label": "Fri"}, {"value": "sat", "label": "Sat"}, {"value": "sun", "label": "Sun"}]}
     ]},
    {"type": "app_started", "label": "App Started", "icon": "power", "scope": "both", "description": "When SoulSync starts up", "available": True},
    {"type": "track_downloaded", "label": "Track Downloaded", "icon": "download", "description": "When a track finishes downloading", "available": True,
     "has_conditions": True,
     "condition_fields": ["artist", "title", "album", "quality"],
     "variables": ["artist", "title", "album", "quality"]},
    {"type": "batch_complete", "label": "Batch Complete", "icon": "check-circle", "description": "When an album/playlist download finishes", "available": True,
     "has_conditions": True,
     "condition_fields": ["playlist_name"],
     "variables": ["playlist_name", "total_tracks", "completed_tracks", "failed_tracks"]},
    {"type": "watchlist_new_release", "label": "New Release Found", "icon": "bell", "description": "When watchlist detects new music", "available": True,
     "has_conditions": True,
     "condition_fields": ["artist"],
     "variables": ["artist", "new_tracks", "added_to_wishlist"]},
    {"type": "playlist_synced", "label": "Playlist Synced", "icon": "refresh", "description": "When a playlist sync completes", "available": True,
     "has_conditions": True,
     "condition_fields": ["playlist_name"],
     "variables": ["playlist_name", "total_tracks", "matched_tracks", "synced_tracks", "failed_tracks"]},
    {"type": "playlist_changed", "label": "Playlist Changed", "icon": "edit", "description": "When a mirrored playlist detects track changes from source", "available": True,
     "has_conditions": True,
     "condition_fields": ["playlist_name"],
     "variables": ["playlist_name", "old_count", "new_count", "added", "removed"]},
    {"type": "discovery_completed", "label": "Discovery Complete", "icon": "search", "description": "When playlist track discovery finishes", "available": True,
     "has_conditions": True,
     "condition_fields": ["playlist_name"],
     "variables": ["playlist_name", "total_tracks", "discovered_count", "failed_count", "skipped_count"]},
    # Phase 3 triggers
    {"type": "wishlist_processing_completed", "label": "Wishlist Processed", "icon": "check-circle",
     "description": "When auto-wishlist processing finishes", "available": True,
     "variables": ["tracks_processed", "tracks_found", "tracks_failed"]},
    {"type": "watchlist_scan_completed", "label": "Watchlist Scan Done", "icon": "check-circle",
     "description": "When watchlist scan finishes", "available": True,
     "variables": ["artists_scanned", "new_tracks_found", "tracks_added"]},
    {"type": "database_update_completed", "label": "Database Updated", "icon": "database",
     "description": "When library database refresh finishes", "available": True,
     "variables": ["total_artists", "total_albums", "total_tracks"]},
    {"type": "library_scan_completed", "label": "Library Scan Done", "icon": "hard-drive",
     "description": "When media library scan finishes", "available": True,
     "variables": ["server_type"]},
    {"type": "download_failed", "label": "Download Failed", "icon": "x-circle",
     "description": "When a track permanently fails to download", "available": True,
     "has_conditions": True, "condition_fields": ["artist", "title", "reason"],
     "variables": ["artist", "title", "reason"]},
    {"type": "download_quarantined", "label": "File Quarantined", "icon": "alert-triangle",
     "description": "When AcoustID verification fails", "available": True,
     "has_conditions": True, "condition_fields": ["artist", "title"],
     "variables": ["artist", "title", "reason"]},
    {"type": "wishlist_item_added", "label": "Wishlist Item Added", "icon": "plus-circle",
     "description": "When a track is added to wishlist", "available": True,
     "has_conditions": True, "condition_fields": ["artist", "title"],
     "variables": ["artist", "title", "reason"]},
    {"type": "watchlist_artist_added", "label": "Artist Watched", "icon": "user-plus",
     "description": "When an artist is added to watchlist", "available": True,
     "has_conditions": True, "condition_fields": ["artist"],
     "variables": ["artist", "artist_id"]},
    {"type": "watchlist_artist_removed", "label": "Artist Unwatched", "icon": "user-minus",
     "description": "When an artist is removed from watchlist", "available": True,
     "has_conditions": True, "condition_fields": ["artist"],
     "variables": ["artist", "artist_id"]},
    {"type": "import_completed", "label": "Import Complete", "icon": "upload",
     "description": "When album/track import finishes", "available": True,
     "has_conditions": True, "condition_fields": ["artist", "album_name"],
     "variables": ["track_count", "album_name", "artist"]},
    {"type": "mirrored_playlist_created", "label": "Playlist Mirrored", "icon": "copy",
     "description": "When a new playlist is mirrored", "available": True,
     "has_conditions": True, "condition_fields": ["playlist_name", "source"],
     "variables": ["playlist_name", "source", "track_count"]},
    {"type": "quality_scan_completed", "label": "Quality Scan Done", "icon": "bar-chart",
     "description": "When quality scan finishes", "available": True,
     "variables": ["quality_met", "low_quality", "total_scanned"]},
    {"type": "duplicate_scan_completed", "label": "Duplicate Scan Done", "icon": "layers",
     "description": "When duplicate cleaner finishes", "available": True,
     "variables": ["files_scanned", "duplicates_found", "space_freed"]},
    # Signal trigger
    {"type": "signal_received", "label": "Signal Received", "icon": "zap", "scope": "both",
     "description": "When another automation fires a named signal", "available": True,
     "config_fields": [
         {"key": "signal_name", "type": "signal_input", "label": "Signal Name"}
     ],
     "variables": ["signal_name"]},
    # Webhook trigger
    {"type": "webhook_received", "label": "Webhook Received", "icon": "globe", "scope": "both",
     "description": "When an external API request is received (POST /api/v1/request)", "available": True,
     "variables": ["query", "request_id", "source"]},

    # ── Video side (scope='video') — the post-download scan chain triggers ──
    {"type": "video_batch_complete", "label": "Video Download Batch Done", "icon": "check-circle", "scope": "video",
     "description": "When a batch of video downloads finishes", "available": True,
     "variables": ["completed"]},
    {"type": "video_library_scan_completed", "label": "Video Library Scan Done", "icon": "hard-drive", "scope": "video",
     "description": "When the media server finishes rescanning your video sections", "available": True,
     "variables": ["server"]},
]


ACTIONS: list[dict] = [
    {"type": "process_wishlist", "label": "Process Wishlist", "icon": "list", "description": "Retry failed downloads from wishlist", "available": True,
     "config_fields": [{"key": "category", "type": "select", "label": "Category", "options": [{"value": "all", "label": "All"}, {"value": "albums", "label": "Albums"}, {"value": "singles", "label": "Singles"}], "default": "all"}]},
    {"type": "scan_watchlist", "label": "Scan Watchlist", "icon": "eye", "description": "Check watched artists for new releases", "available": True},
    {"type": "scan_library", "label": "Scan Library", "icon": "refresh", "description": "Trigger media server library scan", "available": True},
    {"type": "refresh_mirrored", "label": "Refresh Mirrored Playlist", "icon": "copy", "description": "Re-fetch playlist from source and update mirror", "available": True,
     "config_fields": [
         {"key": "playlist_id", "type": "mirrored_playlist_select", "label": "Playlist"},
         {"key": "all", "type": "checkbox", "label": "Refresh all mirrored playlists", "default": False}
     ]},
    {"type": "sync_playlist", "label": "Sync Playlist", "icon": "sync", "description": "Sync mirrored playlist to media server", "available": True,
     "config_fields": [
         {"key": "playlist_id", "type": "mirrored_playlist_select", "label": "Playlist"}
     ]},
    {"type": "discover_playlist", "label": "Discover Playlist", "icon": "search", "description": "Find official Spotify/iTunes metadata for mirrored playlist tracks", "available": True,
     "config_fields": [
         {"key": "playlist_id", "type": "mirrored_playlist_select", "label": "Playlist"},
         {"key": "all", "type": "checkbox", "label": "Discover all mirrored playlists", "default": False}
     ]},
    {"type": "playlist_pipeline", "label": "Playlist Pipeline", "icon": "rocket",
     "description": "Full lifecycle: refresh → discover → sync → download missing. One automation for the entire flow.",
     "available": True,
     "config_fields": [
         {"key": "playlist_id", "type": "mirrored_playlist_select", "label": "Playlist"},
         {"key": "all", "type": "checkbox", "label": "Process all mirrored playlists", "default": False},
         {"key": "skip_wishlist", "type": "checkbox", "label": "Skip wishlist processing", "default": False},
     ]},
    {"type": "personalized_pipeline", "label": "Personalized Playlist Pipeline", "icon": "sparkles",
     "description": "Sync personalized / discover-page playlists (Hidden Gems, Time Machine, Fresh Tape, etc.) to your media server + queue missing tracks for download.",
     "available": True,
     "config_fields": [
         {"key": "kinds", "type": "personalized_playlist_select", "label": "Playlists to sync",
          "description": "Multi-select: Hidden Gems, Discovery Shuffle, Time Machine (per decade), Genre playlists, Fresh Tape, The Archives, Seasonal Mix (per season)"},
         {"key": "refresh_first", "type": "checkbox", "label": "Refresh playlists before sync (regenerate snapshots)", "default": False},
         {"key": "skip_wishlist", "type": "checkbox", "label": "Skip wishlist processing", "default": False},
     ]},
    {"type": "notify_only", "label": "Notify Only", "icon": "bell", "scope": "both", "description": "No action — just send notification", "available": True},
    # Phase 3 actions
    {"type": "start_database_update", "label": "Update Database", "icon": "database",
     "description": "Trigger library database refresh", "available": True,
     "config_fields": [
         {"key": "full_refresh", "type": "checkbox", "label": "Full refresh (slower)", "default": False}
     ]},
    {"type": "run_duplicate_cleaner", "label": "Run Duplicate Cleaner", "icon": "layers",
     "description": "Scan for and remove duplicate files", "available": True},
    {"type": "clear_quarantine", "label": "Clear Quarantine", "icon": "trash",
     "description": "Delete all quarantined files", "available": True},
    {"type": "cleanup_wishlist", "label": "Clean Up Wishlist", "icon": "filter",
     "description": "Remove duplicate/owned tracks from wishlist", "available": True},
    {"type": "update_discovery_pool", "label": "Update Discovery", "icon": "compass",
     "description": "Refresh discovery pool with new tracks", "available": True},
    {"type": "start_quality_scan", "label": "Run Quality Scan", "icon": "bar-chart",
     "description": "Run the Quality Upgrade Finder (scope is set in Library Maintenance)", "available": True},
    {"type": "backup_database", "label": "Backup Database", "icon": "save",
     "description": "Create timestamped database backup", "available": True},
    {"type": "refresh_beatport_cache", "label": "Refresh Beatport Cache", "icon": "music",
     "description": "Scrape Beatport homepage and warm the cache", "available": True},
    {"type": "clean_search_history", "label": "Clean Search History", "icon": "trash-2",
     "description": "Remove old searches from Soulseek", "available": True},
    {"type": "clean_completed_downloads", "label": "Clean Completed Downloads", "icon": "check-square",
     "description": "Clear completed downloads and empty directories", "available": True},
    {"type": "full_cleanup", "label": "Full Cleanup", "icon": "trash",
     "description": "Clear quarantine, download queue, import folder, and search history in one sweep", "available": True},
    {"type": "deep_scan_library", "label": "Deep Scan Library", "icon": "search",
     "description": "Full library comparison without losing enrichment data", "available": True},
    {"type": "run_script", "label": "Run Script", "icon": "terminal", "scope": "both",
     "description": "Execute a script from the scripts folder", "available": True},
    {"type": "search_and_download", "label": "Search & Download", "icon": "download",
     "description": "Search for a track and download the best match", "available": True,
     "config_fields": [
         {"key": "query", "type": "text", "label": "Search Query",
          "placeholder": "Artist - Track (leave empty to use trigger's query)"}
     ]},

    # ── Video side (isolated app, shared engine) ──────────────────────────
    # Tagged scope='video' so they appear ONLY on the video automation
    # builder, never the music one. Their handlers bridge into core.video.
    {"type": "video_scan_library", "label": "Scan Video Library", "icon": "refresh", "scope": "video",
     "description": "Tell the media server to rescan your selected movie/TV sections, then read what it found into SoulSync", "available": True,
     "config_fields": [
         {"key": "mode", "type": "select", "label": "Mode",
          "options": [{"value": "full", "label": "Full (add + refresh)"},
                      {"value": "incremental", "label": "Incremental (recent only)"},
                      {"value": "deep", "label": "Deep (also remove missing)"}],
          "default": "full"},
         {"key": "media_type", "type": "select", "label": "Library",
          "options": [{"value": "all", "label": "Movies + TV"},
                      {"value": "movie", "label": "Movies only"},
                      {"value": "show", "label": "TV only"}],
          "default": "all"}
     ]},
    # Post-download chain actions (two stages, like music's scan_library +
    # start_database_update). Stage 1 nudges the server; stage 2 reads it in.
    {"type": "video_scan_server", "label": "Scan Video Server", "icon": "refresh", "scope": "video",
     "description": "Get the server to index new downloads (skips the scan if it already has them), waits until it finishes, then fires 'Video Library Scan Done'", "available": True,
     "config_fields": [
         {"key": "media_type", "type": "select", "label": "Library",
          "options": [{"value": "all", "label": "Movies + TV"},
                      {"value": "movie", "label": "Movies only"},
                      {"value": "show", "label": "TV only"}],
          "default": "all"},
         {"key": "skip_if_present", "type": "checkbox", "label": "Skip the scan if the server already has the download", "default": True},
         {"key": "probe_grace_minutes", "type": "number", "label": "Give the server's auto-scan this long to ingest first (min)", "default": 2, "min": 0},
         {"key": "max_wait_minutes", "type": "number", "label": "Max wait for scan (min)", "default": 60, "min": 1},
         {"key": "debounce_seconds", "type": "number", "label": "Fallback wait if status unknown (sec)", "default": 120, "min": 10}
     ]},
    {"type": "video_update_database", "label": "Update Video Database", "icon": "database", "scope": "video",
     "description": "Read newly-indexed media from the server into SoulSync (incremental)", "available": True,
     "config_fields": [
         {"key": "mode", "type": "select", "label": "Mode",
          "options": [{"value": "incremental", "label": "Incremental (recent only)"},
                      {"value": "full", "label": "Full (add + refresh)"}],
          "default": "incremental"},
         {"key": "media_type", "type": "select", "label": "Library",
          "options": [{"value": "all", "label": "Movies + TV"},
                      {"value": "movie", "label": "Movies only"},
                      {"value": "show", "label": "TV only"}],
          "default": "all"}
     ]},
    # Per-library deep-scan presets (the system 'Auto-Deep Scan TV/Movie Library' run
    # these). Scope + deep mode are baked in by the registration wrapper, so no config
    # fields — drag one in and it just deep-scans that library.
    {"type": "video_deep_scan_tv", "label": "Deep Scan TV Library", "icon": "search", "scope": "video",
     "description": "Full reconcile of the TV library: re-read every show from the server and drop ones it no longer has (a read, NOT a Plex disk-scan; never touches movies)", "available": True},
    {"type": "video_deep_scan_movies", "label": "Deep Scan Movie Library", "icon": "search", "scope": "video",
     "description": "Full reconcile of the Movie library: re-read every movie from the server and drop ones it no longer has (a read, NOT a Plex disk-scan; never touches TV)", "available": True},
    {"type": "video_add_airing_episodes", "label": "Wishlist Today's Airings", "icon": "calendar", "scope": "video",
     "description": "Sonarr-style: add every episode airing today (for shows you follow) to the wishlist, skipping ones you already own. Also tidies the watchlist by dropping shows that have ended/been canceled.", "available": True,
     "config_fields": [
         {"key": "prune_ended", "type": "checkbox", "label": "Also remove ended/canceled shows from the watchlist", "default": True}
     ]},
    # Video twins of the music maintenance actions. Distinct action_type (the
    # system seeder keys on action_type, so a shared key would collide with the
    # music row) but the SAME shared handler — the cleanup operates on the common
    # download/search state, so behaviour stays identical. scope='video' keeps
    # them on the video builder only; the music blocks above are untouched.
    {"type": "video_clean_search_history", "label": "Clean Search History", "icon": "trash-2", "scope": "video",
     "description": "Remove old searches from Soulseek", "available": True},
]


NOTIFICATIONS: list[dict] = [
    {"type": "discord_webhook", "label": "Discord Webhook", "icon": "message", "scope": "both", "description": "Send a Discord notification", "available": True,
     "variables": ["time", "name", "run_count", "status"]},
    {"type": "pushbullet", "label": "Pushbullet", "icon": "push", "scope": "both", "description": "Push notification to phone/desktop", "available": True,
     "variables": ["time", "name", "run_count", "status"]},
    {"type": "telegram", "label": "Telegram", "icon": "message", "scope": "both", "description": "Send a Telegram message", "available": True,
     "variables": ["time", "name", "run_count", "status"]},
    {"type": "webhook", "label": "Webhook (POST)", "icon": "globe", "scope": "both", "description": "Send a POST request to any URL", "available": True,
     "variables": ["time", "name", "run_count", "status"]},
    # Signal fire action
    {"type": "fire_signal", "label": "Fire Signal", "icon": "zap", "scope": "both",
     "description": "Fire a signal that other automations can listen for", "available": True,
     "config_fields": [
         {"key": "signal_name", "type": "signal_input", "label": "Signal Name"}
     ]},
    # Run script then-action
    {"type": "run_script", "label": "Run Script", "icon": "terminal", "scope": "both",
     "description": "Execute a script after the action completes", "available": True,
     "config_fields": [
         {"key": "script_name", "type": "script_select", "label": "Script"}
     ]},
]


def _in_scope(block: dict, scope: str) -> bool:
    """A block belongs to ``scope`` if it's generic (``"both"``) or tagged for
    that side. Untagged blocks default to ``"music"`` (the original behaviour),
    so the video builder never picks up music-only blocks by accident."""
    s = block.get("scope", "music")
    return s == "both" or s == scope


def blocks_for_scope(scope: str = "music") -> dict:
    """Return the trigger/action/notification lists filtered to one side.

    ``scope="music"`` reproduces the pre-scope behaviour (everything except
    video-only blocks); ``scope="video"`` returns generic + video-only blocks.
    """
    return {
        "triggers": [b for b in TRIGGERS if _in_scope(b, scope)],
        "actions": [b for b in ACTIONS if _in_scope(b, scope)],
        "notifications": [b for b in NOTIFICATIONS if _in_scope(b, scope)],
    }
